"""MOBOT v0.2.0 AGV connection module, running on the main PC.

No-darkroom profile: PICK_UP -> DELIVER -> UNLOAD -> RETURN_STANDBY.
The business server owns destination/inspection decisions and the next command.
The existing v0.2 wire DTO is retained, but its old GO_TO_INSPECTION stage is rejected.
This module owns one AGV, a durable command journal and an event outbox.
It never selects a destination or retries an ambiguous physical operation.
Run --help for configuration. Importing this module does not contact a device.
"""

import argparse
import base64
import hmac
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from agv_client import AGVClient


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def identifier():
    return str(uuid.uuid4())


SCHEMAS = json.loads(Path(__file__).with_name("contract_v1.schema.json").read_text(encoding="utf-8"))


def validate(name, value):
    schema = {"$ref": "#/components/schemas/" + name, "components": SCHEMAS["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


class Problem(Exception):
    def __init__(self, status, code, message, retry="NONE"):
        self.status = status
        self.body = {"code": code, "message": message, "retry": retry, "trace_id": identifier()}
        super().__init__(message)


FAULTS = {"unknown", "stopped", "wifi_lost", "line_lost", "timeout", "control_lost"}
NEXT = {"DELIVER": "PICK_UP",
        "UNLOAD": "DELIVER", "RETURN_STANDBY": "UNLOAD"}


class Adapter:
    def __init__(self, client, journal, command_timeout, clock=time.monotonic):
        if not math.isfinite(command_timeout) or command_timeout <= 0:
            raise ValueError("A positive, measured command timeout profile is required")
        self.client, self.timeout, self.clock = client, command_timeout, clock
        self.lock = threading.RLock()
        self._last_pending = []
        self._volatile_faults = {}
        self._storage_fault = False
        if str(journal) == ":memory:":
            raise ValueError("Physical commands require a durable journal")
        # A separate SQLite lock file provides an OS-released process lease on
        # Windows and Linux. A second process must not mark live work UNKNOWN.
        self._lease = sqlite3.connect(str(journal) + "-lock", timeout=0, check_same_thread=False)
        try:
            self._lease.execute("BEGIN EXCLUSIVE")
        except sqlite3.Error:
            self._lease.close()
            raise ValueError("This AGV journal is already owned by another process") from None
        self.db = sqlite3.connect(journal, check_same_thread=False)
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {"agv_meta", "agv_commands", "agv_events", "agv_boots", "agv_cycles"}:
            self.db.close()
            self._lease.close()
            raise ValueError("Use a separate AGV journal, not the business database")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS agv_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agv_commands
                (id TEXT PRIMARY KEY, body TEXT NOT NULL, result TEXT NOT NULL, plan TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agv_boots (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS agv_cycles (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS agv_events
                (id TEXT PRIMARY KEY, boot TEXT NOT NULL, seq INTEGER NOT NULL,
                 body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, UNIQUE(boot,seq));
        """)
        self.boot_id = identifier()
        self.raw, self.observed_at, self.online = None, utc(), False
        row = self.db.execute("SELECT value FROM agv_meta WHERE key='lane'").fetchone()
        self.lane = json.loads(row[0]) if row else {
            "cycle_id": None, "phase": "READY", "pause_id": None,
            "inspection_id": None, "destination": None, "recovery": False}
        self.stopping = threading.Event()
        self.threads = []
        with self.db:
            # Migrate existing journals: retain every observed cycle, not only
            # the most recent one. Tombstones survive recovery and restart.
            for (encoded,) in self.db.execute("SELECT body FROM agv_commands").fetchall():
                recorded = json.loads(encoded)
                cycle = recorded["cycle_id"]
                if cycle and recorded['action'] not in {'PAUSE','RESUME'}:
                    self.db.execute("INSERT OR IGNORE INTO agv_cycles VALUES (?)", (str(uuid.UUID(cycle)),))
            for cycle in (self.lane.get("cycle_id"), self.lane.get("last_cycle_id")):
                if cycle:
                    self.db.execute("INSERT OR IGNORE INTO agv_cycles VALUES (?)", (str(uuid.UUID(cycle)),))
            self.db.execute("INSERT INTO agv_boots VALUES (?)", (self.boot_id,))
            # A process restart is not proof that an accepted movement did not run.
            for command in self._pending():
                self._unknown(command, "Adapter restarted before a verified completion")
            if self.lane["phase"] not in {"READY", "CLOSED"} or self.lane["pause_id"]:
                self.lane["recovery"] = True
            self._save_lane()
        self._last_pending = []

    def _save_lane(self):
        self.db.execute("INSERT OR REPLACE INTO agv_meta VALUES ('lane',?)",
                        (json.dumps(self.lane, sort_keys=True),))

    def _pending(self):
        rows = self.db.execute("SELECT body,result,plan FROM agv_commands ORDER BY rowid").fetchall()
        pending = [(json.loads(a), json.loads(b), json.loads(c)) for a, b, c in rows
                   if json.loads(b)["state"] in {"ACCEPTED", "RUNNING"}]
        self._last_pending = json.loads(json.dumps(pending))
        return pending

    def _save(self, body, result, plan):
        self.db.execute("INSERT OR REPLACE INTO agv_commands VALUES (?,?,?,?)",
                        (body["command_id"], json.dumps(body, sort_keys=True),
                         json.dumps(result), json.dumps(plan)))

    def _event(self, body, kind, data):
        seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM agv_events WHERE boot=?",
                              (self.boot_id,)).fetchone()[0]
        event = {"event_id": identifier(), "device_id": "AGV", "boot_id": self.boot_id,
                 "event_seq": seq, "cycle_id": body["cycle_id"],
                 "command_id": body["command_id"], "occurred_at": utc(), "kind": kind, "data": data}
        validate("DeviceEvent", event)
        self.db.execute("INSERT INTO agv_events(id,boot,seq,body) VALUES (?,?,?,?)",
                        (event["event_id"], self.boot_id, seq, json.dumps(event)))
        return event["event_id"]

    def _unknown(self, command, message):
        body, result, plan = command
        result.update(state="UNKNOWN", updated_at=utc(), error={
            "code": "RECONCILE_REQUIRED", "message": message, "retry": "OPERATOR",
            "trace_id": identifier(), "device_id": "AGV", "command_id": body["command_id"]})
        if body["cycle_id"]:
            result["error"]["cycle_id"] = body["cycle_id"]
        result["result_event_id"] = self._event(body, "DEVICE_FAILED", result["error"])
        self._save(body, result, plan)
        self.lane["recovery"] = True

    def _fault(self, message):
        # Interlock first, independently of both network and journal writes.
        self.lane["recovery"] = True
        self.client.suspend_heartbeat()
        try:
            self.client.stop()
        except Exception:
            pass
        try:
            pending = self._pending()
        except sqlite3.Error:
            pending = self._last_pending
        for body, result, plan in pending:
            if body["command_id"] in self._volatile_faults:
                continue
            result = dict(result, state="UNKNOWN", updated_at=utc(), result_event_id=None,
                          error={"code": "RECONCILE_REQUIRED", "message": message,
                                 "retry": "OPERATOR", "trace_id": identifier()})
            self._volatile_faults[body["command_id"]] = json.loads(json.dumps((body, result, plan)))
        self._persist_faults()

    def _persist_faults(self):
        # Until the journal becomes writable, expose UNKNOWN from memory and
        # keep movement interlocked. Never publish a nonexistent durable event.
        try:
            with self.db:
                for command in self._volatile_faults.values():
                    body, result, plan = json.loads(json.dumps(command))
                    self._unknown((body, result, plan), result["error"]["message"])
                self._save_lane()
        except sqlite3.Error:
            self._storage_fault = True
        else:
            self._storage_fault = False
            self._volatile_faults.clear()
            self._last_pending = []

    def command(self, command_id):
        with self.lock:
            if command_id in self._volatile_faults:
                return json.loads(json.dumps(self._volatile_faults[command_id][1]))
            try:
                row = self.db.execute("SELECT result FROM agv_commands WHERE id=?", (command_id,)).fetchone()
            except sqlite3.Error:
                raise Problem(503, "DEVICE_UNAVAILABLE", "Command journal unavailable", "QUERY_FIRST") from None
            if not row:
                raise Problem(404, "NOT_FOUND", "Unknown command; query device state before retrying", "QUERY_FIRST")
            return json.loads(row[0])

    def accept(self, body, expected_boot):
        with self.lock:
            previous = json.loads(json.dumps(self.lane))
            try:
                return self._accept(body, expected_boot)
            except sqlite3.Error:
                self.lane = previous
                self._fault("Command acceptance could not be journaled")
                raise Problem(503, "DEVICE_UNAVAILABLE", "Command journal unavailable", "OPERATOR") from None

    def _accept(self, body, expected_boot):
        try:
            validate("DeviceCommand", body)
            validate("Id", expected_boot)
        except Exception:
            raise Problem(400, "BAD_REQUEST", "DeviceCommand and X-Expected-Boot-Id are required by v0.2.0") from None
        expected_boot = str(uuid.UUID(expected_boot))
        # UUID text is case-insensitive. Canonicalize before deduplication so a
        # change in casing cannot create a second physical command or cycle.
        body = json.loads(json.dumps(body))
        for key in ("command_id", "cycle_id"):
            if body[key] is not None: body[key] = str(uuid.UUID(body[key]))
        for key in ("inspection_id", "pause_command_id"):
            if key in body["params"]: body["params"][key] = str(uuid.UUID(body["params"][key]))
        with self.lock:
            if body["device_id"] != "AGV":
                raise Problem(403, "FORBIDDEN", "This authenticated module controls AGV only")
            if expected_boot != self.boot_id:
                raise Problem(409, "CONFLICT", "Boot ID changed; query current status", "QUERY_FIRST")
            if body["command_id"] in self._volatile_faults:
                if self._volatile_faults[body["command_id"]][0] != body:
                    raise Problem(409, "CONFLICT", "Command ID was reused with different input")
                return 200, self.command(body["command_id"])
            if self._storage_fault:
                raise Problem(503, "DEVICE_UNAVAILABLE", "Command journal requires recovery", "OPERATOR")
            row = self.db.execute("SELECT body FROM agv_commands WHERE id=?", (body["command_id"],)).fetchone()
            if row:
                if json.loads(row[0]) != body:
                    raise Problem(409, "CONFLICT", "Command ID was reused with different input")
                result = self.command(body["command_id"])
                return (202 if result["state"] in {"ACCEPTED", "RUNNING"} else 200), result
            action = body["action"]
            if self.lane["cycle_id"] and self.raw and self.raw["state"] in FAULTS | {"inspecting"} and not self.lane["recovery"]:
                self._fault("Cancelled or legacy inspection state requires operator reconciliation")
            if action not in {"PICK_UP", "DELIVER", "UNLOAD", "RETURN_STANDBY", "PAUSE", "RESUME"}:
                raise Problem(422, "BAD_TRANSITION", "Unsupported AGV action")
            if not self.online:
                raise Problem(503, "DEVICE_UNAVAILABLE", "No current device status", "QUERY_FIRST")
            pending = self._pending()
            if action != "PAUSE" and self.lane["recovery"]:
                raise Problem(409, "RECONCILE_REQUIRED", "Operator reconciliation is required", "OPERATOR")
            if any(b["action"] in {"PAUSE", "RESUME"} for b, _, _ in pending):
                raise Problem(409, "BAD_TRANSITION", "Pause or resume is still pending", "QUERY_FIRST")
            if action in {"PAUSE", "RESUME"}:
                # A plant can be paused before its first AGV pickup. Keep that
                # control context separate from the AGV's physical work cycle.
                idle_control = (action == 'PAUSE' and self.lane['cycle_id'] is None
                                and self.lane['phase'] in {'READY','CLOSED'} and not pending)
                expected_cycle = self.lane.get('pause_cycle_id',self.lane['cycle_id']) if action == 'RESUME' else self.lane['cycle_id']
                if body["cycle_id"] != expected_cycle and not idle_control:
                    raise Problem(409, "STALE_CYCLE", "Pause cycle does not match the current cycle")
                if action == "PAUSE" and self.lane["pause_id"]:
                    raise Problem(409, "BAD_TRANSITION", "Already paused")
                if action == "RESUME" and (self.lane["pause_id"] != body["params"]["pause_command_id"]
                                            or self.raw["state"] != "paused"):
                    raise Problem(409, "BAD_TRANSITION", "Resume must reference the verified pause")
            else:
                if pending or self.lane["pause_id"]:
                    raise Problem(409, "BAD_TRANSITION", "An operation or normal pause is active", "QUERY_FIRST")
                if action == "PICK_UP":
                    used = self.db.execute("SELECT 1 FROM agv_cycles WHERE id=?", (body["cycle_id"],)).fetchone()
                    if self.lane["phase"] not in {"READY", "CLOSED"} or used:
                        raise Problem(409, "STALE_CYCLE", "A new pickup requires a new idle cycle")
                    if not self._at(2) or not self._fork(85):
                        raise Problem(409, "BAD_TRANSITION", "Pickup requires standby at point 2, fork 85")
                elif body["cycle_id"] != self.lane["cycle_id"] or self.lane["phase"] != NEXT[action]:
                    raise Problem(409, "STALE_CYCLE", "Cycle or predecessor does not match")
                if action in {"DELIVER", "UNLOAD"}:
                    if action == "UNLOAD" and (body["params"]["inspection_id"] != self.lane["inspection_id"]
                                               or body["params"]["destination"] != self.lane["destination"]):
                        raise Problem(409, "CONFLICT", "Unload inspection and destination must match delivery")
                if self.raw["mode"] != "auto" or not self.raw["control_connected"]:
                    raise Problem(409, "BAD_TRANSITION", "Automatic control ownership is required")
            plan = {"steps": self._plan(body), "index": 0, "issued": False,
                    "deadline": self.clock() + self.timeout, "pause_started": None}
            result = {"command_id": body["command_id"], "cycle_id": body["cycle_id"], "device_id": "AGV",
                      "state": "ACCEPTED", "updated_at": utc(), "result_event_id": None, "error": None}
            with self.db:
                self._save(body, result, plan)
                if action == "PICK_UP":
                    self.db.execute("INSERT INTO agv_cycles VALUES (?)", (body["cycle_id"],))
                    self.lane.update(cycle_id=body["cycle_id"], inspection_id=None, destination=None)
                if action == "PAUSE":
                    self.lane["pause_id"] = body["command_id"]
                    self.lane['pause_cycle_id'] = body['cycle_id']
                    self.lane["paused_at"] = self.clock()
                self._save_lane()
            self._last_pending = json.loads(json.dumps(pending + [(body, result, plan)]))
            return 202, result

    def _at(self, point):
        return self.raw["state"] in {"ready", "arrived"} and self.raw["position"] == point

    def _fork(self, angle):
        return self.raw["servo_angle"] == angle and self.raw["fork_moving"] is False

    def _plan(self, body):
        action, params = body["action"], body["params"]
        if action == "PICK_UP":
            return [["move", 1], ["fork", 110]]
        if action == "DELIVER":
            if not self._at(1) or not self._fork(110):
                raise Problem(409, "BAD_TRANSITION", "Delivery requires completed pickup at point 1")
            return [["move", 2], ["move", params["destination"]]]
        if action == "UNLOAD":
            if not self._at(params["destination"]) or not self._fork(110):
                raise Problem(409, "BAD_TRANSITION", "Unload requires arrival at the recorded destination")
            return [["fork", 60]]
        if action == "RETURN_STANDBY":
            if not self._at(self.lane["destination"]) or not self._fork(60):
                raise Problem(409, "BAD_TRANSITION", "Return requires completed unload")
            return [["move", 2], ["fork", 85]]
        return [[action.lower(), None]]

    def _complete(self, body, result, plan):
        previous = json.loads(json.dumps(self.lane))
        try:
            self._complete_transaction(body, result, plan)
        except sqlite3.Error:
            self.lane = previous
            raise

    def _complete_transaction(self, body, result, plan):
        action = body["action"]
        if action == "PICK_UP":
            kind, data = "LIFT_FINISHED", {"station": 1, "fork_target_deg": 110, "fork_moving": False}
        elif action == "DELIVER":
            kind, data = "DESTINATION_ARRIVED", {"destination": body["params"]["destination"]}
        elif action == "UNLOAD":
            kind, data = "UNLOAD_FINISHED", {"destination": body["params"]["destination"],
                                            "fork_target_deg": 60, "fork_moving": False}
        elif action == "RETURN_STANDBY":
            kind, data = "STANDBY_RETURNED", {"station": 2, "fork_target_deg": 85, "fork_moving": False}
        elif action == "PAUSE":
            kind, data = "DEVICE_PAUSED", {"drive_stopped": True, "fork_motion_stopped": True}
        else:
            kind, data = "DEVICE_RESUMED", {}
        with self.db:
            if action == "RESUME":
                duration = self.clock() - self.lane["paused_at"]
                for original, original_result, original_plan in self._pending():
                    if original["action"] not in {"PAUSE", "RESUME"}:
                        original_plan["deadline"] += duration
                        # The physical operation continued, but /status now names RESUME.
                        if original_plan["issued"]:
                            original_plan["operation_id"] = plan["operation_id"]
                        self._save(original, original_result, original_plan)
                self.lane["pause_id"] = None
                self.lane.pop('pause_cycle_id',None)
            elif action not in {"PAUSE"}:
                self.lane["phase"] = "CLOSED" if action == "RETURN_STANDBY" else action
                if action == "RETURN_STANDBY":
                    self.lane.update(cycle_id=None, last_cycle_id=body["cycle_id"])
                if action == "DELIVER":
                    self.lane.update(inspection_id=body["params"]["inspection_id"],
                                     destination=body["params"]["destination"])
            result.update(state="COMPLETED", updated_at=utc(), result_event_id=self._event(body, kind, data))
            self.lane["last_completed_command_id"] = body["command_id"]
            self._save(body, result, plan)
            self._save_lane()
        self._last_pending = [c for c in self._last_pending if c[0]["command_id"] != body["command_id"]]

    def _advance(self, command):
        body, result, plan = command
        operation, value = plan["steps"][plan["index"]]
        if self.clock() >= plan["deadline"]:
            self._fault("Configured completion limit exceeded; physical outcome is unverified")
            return
        if not plan["issued"]:
            plan.update(issued=True, operation_id=str(uuid.uuid5(uuid.UUID(body["command_id"]), str(plan["index"]))))
            result.update(state="RUNNING", updated_at=utc())
            # Persist intent before I/O. If the response is lost, do not send it twice.
            with self.db:
                self._save(body, result, plan)
            op_id = plan["operation_id"]
            if operation == "move": self.client.move(value, op_id)
            elif operation == "fork": self.client.set_fork(value, op_id)
            elif operation == "pause": self.client.pause(op_id)
            else: self.client.resume(op_id)
            return
        if self.raw["operation_id"] != plan["operation_id"]:
            # The loop may not have drained the queue yet. A different applied
            # operation after ours was observed is a control conflict.
            if plan.get("seen"):
                self._fault("Applied operation changed outside the current command")
            return
        plan["seen"] = True
        matches = ((operation == "move" and self._at(value) and not self.raw["fork_moving"]) or
                   (operation == "fork" and self._fork(value)) or
                   (operation == "pause" and self.raw["state"] == "paused" and not self.raw["fork_moving"]) or
                   (operation == "resume" and self.raw["state"] != "paused"))
        if matches:
            plan["index"] += 1
            if plan["index"] == len(plan["steps"]):
                self._complete(body, result, plan)
                return
            plan.update(issued=False, seen=False)
        with self.db:
            self._save(body, result, plan)

    @staticmethod
    def _validate_raw(raw):
        required = {"state": str, "position": int, "servo_angle": int, "fork_moving": bool,
                    "control_connected": bool, "mode": str, "operation_id": str, "boot_id": str}
        if not isinstance(raw, dict) or any(type(raw.get(k)) is not t for k, t in required.items()):
            raise ValueError("Firmware status is incompatible")
        uuid.UUID(raw["boot_id"])
        if raw["mode"] not in {"auto", "rc"} or raw["state"] not in FAULTS | {"ready", "moving", "arrived", "reverse_testing", "inspecting", "paused"}:
            raise ValueError("Invalid firmware state or mode")
        if not 0 <= raw["position"] <= 7 or not -1 <= raw["servo_angle"] <= 180:
            raise ValueError("Invalid firmware position or fork output")

    def step(self):
        with self.lock:
            try:
                if self._storage_fault or self._volatile_faults:
                    self._persist_faults()
                    if self._storage_fault:
                        return
                try:
                    raw = self.client.status()
                except OSError as error:
                    if isinstance(error,HTTPError):raise
                    # Retrying GET cannot replay movement. Give the heartbeat
                    # its existing priority before one fresh status observation.
                    logging.getLogger(__name__).warning('AGV status GET failed (%s); retrying GET once',type(error).__name__)
                    raw = self.client.status()
                self._validate_raw(raw)
                previous = self.raw
                self.raw, self.online, self.observed_at = raw, True, utc()
                if previous and previous["boot_id"] != raw["boot_id"]:
                    self._fault("ESP32 rebooted; reconcile the previous physical operation")
                    return
                if self.lane["recovery"] and (raw["state"] in {"moving", "reverse_testing"} or raw["fork_moving"]):
                    # This includes an abrupt adapter restart, where there is
                    # no live worker associated with the persisted operation.
                    self.client.stop()
                    return
                pending = self._pending()
                if (pending or self.lane["cycle_id"]) and not self.lane["recovery"] and (raw["state"] in FAULTS or raw["state"] == "inspecting" or raw["servo_angle"] < 0 or
                                raw["mode"] != "auto" or not raw["control_connected"]):
                    self._fault(f"Device state={raw['state']}: control, position or fork output requires reconciliation")
                    return
                controls = [c for c in pending if c[0]["action"] in {"PAUSE", "RESUME"}]
                if controls:
                    control_body, _, control_plan = controls[0]
                    # Do not overwrite the ESP32's one-slot queue between its
                    # HTTP acceptance and loop application of the original op.
                    if control_body["action"] == "PAUSE" and not control_plan["issued"]:
                        if any(p["issued"] and raw["operation_id"] != p["operation_id"]
                               for b, _, p in pending if b["action"] not in {"PAUSE", "RESUME"}):
                            if self.clock() >= control_plan["deadline"]:
                                self._fault("Original queued operation was not observed before pause")
                            return
                    self._advance(controls[0])
                elif pending and not self.lane["pause_id"] and not self.lane["recovery"]:
                    self._advance(pending[0])
            except Exception as error:
                self.online = False
                try:
                    active = bool(self._pending())
                except sqlite3.Error:
                    active = True
                if active or self.lane["recovery"] or isinstance(error, sqlite3.Error):
                    self._fault("Device communication, journal or completion evidence is unavailable")

    def status(self):
        with self.lock:
            try:
                pending = self._pending()
            except sqlite3.Error:
                pending = self._last_pending
            pending = [c for c in pending if c[0]["command_id"] not in self._volatile_faults]
            primary = next((c for c in pending if c[0]["action"] not in {"PAUSE", "RESUME"}), None)
            active = primary or (pending[0] if pending else None)
            state = ("UNKNOWN" if not self.online or self.raw["state"] == "unknown" else
                     "FAULT" if self.lane["recovery"] or self.raw["state"] in FAULTS or self.raw["servo_angle"] < 0
                     or not self.raw["control_connected"] else
                     "PAUSED" if self.raw["state"] == "paused" else "BUSY" if active else "IDLE")
            result = {"device_id": "AGV", "boot_id": self.boot_id,
                      "mode": "MANUAL" if self.raw and self.raw["mode"] == "rc" else "AUTO",
                      "state": state, "cycle_id": self.lane["cycle_id"],
                      "active_command_id": active[0]["command_id"] if active else None,
                      "last_completed_command_id": self.lane.get("last_completed_command_id"),
                      "observed_at": self.observed_at, "online": self.online}
            validate("DeviceStatus", result)
            return result

    def events(self, boot_id, after_seq=0, limit=50, cursor=None):
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise Problem(400, "BAD_REQUEST", "Invalid event page")
        with self.lock:
            if not self.db.execute("SELECT 1 FROM agv_boots WHERE id=?", (boot_id,)).fetchone():
                raise Problem(404, "NOT_FOUND", "Unknown boot ID")
            last = after_seq
            if cursor:
                try:
                    data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                    if data["boot"] != boot_id or data["after"] != after_seq or type(data["last"]) is not int:
                        raise ValueError()
                    last = max(after_seq, data["last"])
                except Exception:
                    raise Problem(400, "BAD_REQUEST", "Cursor does not match the event query") from None
            rows = self.db.execute("SELECT seq,body FROM agv_events WHERE boot=? AND seq>? ORDER BY seq LIMIT ?",
                                   (boot_id, last, limit + 1)).fetchall()
            page = rows[:limit]
            next_cursor = base64.urlsafe_b64encode(json.dumps({"boot": boot_id, "after": after_seq,
                "last": page[-1][0]}).encode()).decode() if len(rows) > limit else None
            return {"events": [json.loads(r[1]) for r in page], "next_cursor": next_cursor}

    def deliver_once(self, url, token):
        with self.lock:
            row = self.db.execute("SELECT id,body FROM agv_events WHERE delivered=0 ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return
        request = Request(url, data=row[1].encode(), method="POST", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            if response.status != 200:
                raise ValueError("Event was not acknowledged")
            ack = json.loads(response.read())
        validate("EventAck", ack)
        if ack["event_id"] != row[0]:
            raise ValueError("Event ACK does not match")
        with self.lock, self.db:
            self.db.execute("UPDATE agv_events SET delivered=1 WHERE id=?", (row[0],))
            if ack["disposition"] == "HELD_FOR_REVIEW":
                self._fault("Business server held an event for reconciliation")

    def confirm_standby_recovery(self, reason):
        """Local operator hook, after business-server reconciliation and a
        physical check. It never fabricates completion for UNKNOWN commands.
        No public HTTP recovery endpoint is added to the common contract.
        """
        with self.lock:
            if (not self.lane["recovery"] or self._storage_fault or self._volatile_faults or self._pending()):
                raise ValueError("Recovery requires a fault and no accepted or running command")
            raw = self.client.status()
            self._validate_raw(raw)
            self.raw, self.observed_at, self.online = raw, utc(), True
            if (not reason.strip() or not self._at(2) or not self._fork(85) or raw["mode"] != "auto"):
                raise ValueError("Confirm the actual point-2/85-degree standby before recovery")
            # This is the explicit operator recovery path, not an automatic
            # reconnect in the poll loop. Claim control without replaying work.
            self.client.connect()
            raw = self.client.status()
            self._validate_raw(raw)
            self.raw = raw
            if not raw["control_connected"] or not self._at(2) or not self._fork(85) or raw["mode"] != "auto":
                self._fault("Standby changed during recovery")
                raise ValueError("Verified standby and control ownership are required")
            restored = dict(self.lane, recovery=False, phase="CLOSED", pause_id=None,
                            last_cycle_id=self.lane["cycle_id"], cycle_id=None)
            try:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO agv_meta VALUES ('lane',?)", (json.dumps(restored),))
                    self.db.execute("INSERT OR REPLACE INTO agv_meta VALUES ('operator_recovery',?)",
                                    (json.dumps({"reason": reason, "recorded_at": utc()}),))
            except sqlite3.Error:
                self._fault("Operator recovery could not be journaled")
                raise Problem(503, "DEVICE_UNAVAILABLE", "Recovery journal unavailable", "OPERATOR") from None
            self.lane = restored

    def start(self, events_url, event_token, poll_seconds):
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("Positive status polling interval required")
        self.client.connect()
        self.step()

        def poll():
            while not self.stopping.wait(poll_seconds):
                self.step()

        def publish():
            while not self.stopping.wait(poll_seconds):
                try:
                    self.deliver_once(events_url, event_token)
                except Exception:
                    # Durable outbox: resend this event_id; never replay motion.
                    pass

        self.threads = [threading.Thread(target=f, daemon=True) for f in (poll, publish)]
        for thread in self.threads: thread.start()

    def close(self):
        self.stopping.set()
        self.client.suspend_heartbeat()
        try:
            for thread in self.threads: thread.join()
            with self.lock:
                try:
                    pending = self._pending()
                except sqlite3.Error:
                    pending = self._last_pending
                if pending or self._storage_fault:
                    self._fault("Adapter stopped with unverified work")
        finally:
            try:
                self.client.close()
            finally:
                self.db.close()
                self._lease.close()


def serve(adapter, controller_token, address):
    if len(controller_token) < 32:
        raise ValueError("Configure a private controller token of at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log tokens, identifiers or customer information.

        def respond(self, status, value):
            payload = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def dispatch(self):
            try:
                if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + controller_token).encode()):
                    raise Problem(401, "UNAUTHORIZED", "Controller authentication required")
                target = urlsplit(self.path)
                prefix = "/device/v1/commands/"
                if self.command == "POST" and target.path == "/device/v1/commands":
                    if self.headers.get_content_type() != "application/json":
                        raise Problem(400, "BAD_REQUEST", "JSON body required")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384 or self.headers.get("Transfer-Encoding"):
                        raise Problem(400, "BAD_REQUEST", "Invalid request length")
                    value = json.loads(self.rfile.read(length))
                    status, result = adapter.accept(value, self.headers.get("X-Expected-Boot-Id"))
                elif self.command == "GET" and target.path == "/device/v1/status":
                    status, result = 200, adapter.status()
                elif self.command == "GET" and target.path.startswith(prefix):
                    command_id = target.path[len(prefix):]
                    validate("Id", command_id)
                    command_id = str(uuid.UUID(command_id))
                    status, result = 200, adapter.command(command_id)
                elif self.command == "GET" and target.path == "/device/v1/events":
                    query = parse_qs(target.query, keep_blank_values=True)
                    if set(query) - {"boot_id", "after_seq", "limit", "cursor"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError()
                    boot = query["boot_id"][0]
                    validate("Id", boot)
                    boot = str(uuid.UUID(boot))
                    cursor = query.get("cursor", [None])[0]
                    if cursor is not None and not 1 <= len(cursor) <= 256: raise ValueError()
                    result = adapter.events(boot, int(query.get("after_seq", ["0"])[0]),
                                            int(query.get("limit", ["50"])[0]), cursor)
                    status = 200
                else:
                    raise Problem(404, "NOT_FOUND", "Unknown device API")
                self.respond(status, result)
            except Problem as error:
                self.respond(error.status, error.body)
            except (ValueError, KeyError, TypeError, ValidationError):
                self.respond(400, Problem(400, "BAD_REQUEST", "Invalid JSON, identifier or query").body)
            except Exception:
                self.respond(503, Problem(503, "DEVICE_UNAVAILABLE", "Adapter storage or device unavailable", "QUERY_FIRST").body)

        do_GET = do_POST = dispatch

    return ThreadingHTTPServer(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, help="Private adapter SQLite path, separate from business DB")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--command-timeout-seconds", type=float, required=True, help="Measured completion timeout profile")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=0.3, help="Comparison-test default, not final team timing")
    parser.add_argument("--confirm-standby-recovery", metavar="OPERATOR_REASON")
    args = parser.parse_args()
    # Configure secrets outside the repository. The adapter is localhost-only by
    # default; remote deployment requires the team's TLS/network configuration.
    values = {k: os.environ[k] for k in ("AGV_URL", "AGV_DEVICE_KEY", "AGV_CONTROLLER_TOKEN", "AGV_EVENT_TOKEN", "AGV_EVENTS_URL")}
    if len({values[k] for k in ("AGV_DEVICE_KEY", "AGV_CONTROLLER_TOKEN", "AGV_EVENT_TOKEN")}) != 3:
        raise ValueError("Device, controller and event credentials must be distinct")
    if any(len(values[k]) < 32 for k in ("AGV_CONTROLLER_TOKEN", "AGV_EVENT_TOKEN")):
        raise ValueError("Configure private controller and event credentials")
    adapter = Adapter(AGVClient(values["AGV_URL"], values["AGV_DEVICE_KEY"], heartbeat_seconds=args.heartbeat_seconds),
                      args.journal, args.command_timeout_seconds)
    server = serve(adapter, values["AGV_CONTROLLER_TOKEN"], (args.listen, args.port))
    try:
        adapter.start(values["AGV_EVENTS_URL"], values["AGV_EVENT_TOKEN"], args.poll_seconds)
        if args.confirm_standby_recovery:
            adapter.confirm_standby_recovery(args.confirm_standby_recovery)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        adapter.close()


if __name__ == "__main__":
    main()
