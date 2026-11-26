"""Loopback HTTP, private temporary journals and inert device only."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device_adapter import Adapter, Problem, identifier, serve, validate


class Device:
    def __init__(self):
        self.raw = dict(state="ready", position=2, servo_angle=85, fork_moving=False,
                        mode="auto", control_connected=True, boot_id=identifier(), operation_id="")
        self.calls = []
        self.saved = None
        self.last = None
        self.fail_send = False
        self.heartbeat_suspended = False

    def status(self): return copy.deepcopy(self.raw)
    def connect(self): self.heartbeat_suspended = False
    def suspend_heartbeat(self): self.heartbeat_suspended = True
    def close(self): pass

    def send(self, kind, value, op):
        self.calls.append((kind, value, op))
        self.last = kind, value
        self.raw["operation_id"] = op
        if kind == "move": self.raw.update(state="moving", position=0)
        elif kind == "fork": self.raw["fork_moving"] = True
        elif kind == "inspection": self.raw.update(state="moving", position=0)
        elif kind == "pause":
            self.saved = self.raw["state"], self.raw["fork_moving"]
            self.raw.update(state="paused", fork_moving=False)
        elif kind == "resume":
            self.raw["state"], self.raw["fork_moving"] = self.saved
        if self.fail_send:
            raise OSError("Response lost after device applied operation")

    def move(self, value, op): self.send("move", value, op)
    def set_fork(self, value, op): self.send("fork", value, op)
    def inspect(self, op): self.send("inspection", None, op)
    def pause(self, op): self.send("pause", None, op)
    def resume(self, op): self.send("resume", None, op)
    def stop(self):
        self.calls.append(("stop", None, None))
        self.raw.update(state="stopped", fork_moving=False)

    def finish(self):
        kind, value = self.last
        if kind == "move": self.raw.update(state="arrived", position=value)
        elif kind == "fork": self.raw.update(servo_angle=value, fork_moving=False)
        elif kind == "inspection": self.raw.update(state="inspecting", position=0)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "private-journal.sqlite")
        self.device = Device()
        self.now = 100.
        self.adapter = Adapter(self.device, self.path, 30, clock=lambda: self.now)
        self.adapter.step()
        self.cycle = identifier()

    def tearDown(self):
        self.adapter.close()
        self.tmp.cleanup()

    def body(self, action, params=None, cycle="current"):
        return {"command_id": identifier(), "device_id": "AGV", "cycle_id": self.cycle if cycle == "current" else cycle,
                "action": action, "params": params or {}}

    def accept(self, body): return self.adapter.accept(body, self.adapter.boot_id)

    def complete(self, body):
        self.accept(body)
        for _ in range(20):
            self.adapter.step()
            self.device.finish()
            if self.adapter.command(body["command_id"])["state"] == "COMPLETED":
                return self.adapter.command(body["command_id"])
        self.fail("Command did not complete")

    def assert_problem(self, status, callback):
        with self.assertRaises(Problem) as context: callback()
        self.assertEqual(context.exception.status, status)

    def test_five_full_direct_routes_and_contract_events(self):
        for destination in range(3, 8):
            self.cycle = identifier()
            inspection = identifier()
            for action, params in [("PICK_UP", {}),
                ("DELIVER", {"destination": destination, "inspection_id": inspection}),
                ("UNLOAD", {"destination": destination, "inspection_id": inspection}), ("RETURN_STANDBY", {})]:
                result = self.complete(self.body(action, params))
                validate("CommandResult", result)
                self.assertIsNotNone(result["result_event_id"])
            self.assertEqual(self.adapter.status()["state"], "IDLE")
            self.assertIsNone(self.adapter.status()["cycle_id"])
            self.assertEqual((self.device.raw["position"], self.device.raw["servo_angle"]), (2, 85))
        events = self.adapter.events(self.adapter.boot_id)["events"]
        self.assertEqual(len(events), 20)
        for event in events: validate("DeviceEvent", event)
        self.assertEqual([e["event_seq"] for e in events], list(range(1, 21)))
        self.assertEqual(sum(e["kind"] == "INSPECTION_ARRIVED" for e in events), 0)
        self.assertEqual(sum(c[0] == "inspection" for c in self.device.calls), 0)

    def test_duplicate_conflicting_and_stale_commands(self):
        body = self.body("PICK_UP")
        self.accept(body)
        self.adapter.step()
        self.assertEqual(self.accept(body)[0], 202)
        upper = copy.deepcopy(body)
        upper["command_id"] = upper["command_id"].upper()
        upper["cycle_id"] = upper["cycle_id"].upper()
        self.assertEqual(self.accept(upper)[0], 202)
        bad = copy.deepcopy(body); bad["cycle_id"] = identifier()
        self.assert_problem(409, lambda: self.accept(bad))
        self.assert_problem(409, lambda: self.adapter.accept(self.body("PAUSE"), identifier()))
        self.assert_problem(409, lambda: self.accept(self.body("PICK_UP")))
        self.assertEqual(len(self.device.calls), 1)
        result = self.complete(body)
        before = len(self.device.calls)
        self.assertEqual(self.accept(body), (200, result))
        self.assertEqual(len(self.device.calls), before)
        self.assert_problem(409, lambda: self.accept(self.body("DELIVER", {"destination":3,"inspection_id":identifier()}, cycle=identifier())))

    def test_unload_and_return_do_not_complete_early(self):
        self.complete(self.body("PICK_UP"))
        info = {"destination": 6, "inspection_id": identifier()}
        self.complete(self.body("DELIVER", info))
        wrong = dict(info, inspection_id=identifier())
        self.assert_problem(409, lambda: self.accept(self.body("UNLOAD", wrong)))
        self.complete(self.body("UNLOAD", info))
        body = self.body("RETURN_STANDBY")
        self.accept(body); self.adapter.step(); self.device.finish(); self.adapter.step()
        self.assertEqual(self.device.raw["position"], 2)
        self.assertEqual(self.device.raw["servo_angle"], 60)
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "RUNNING")
        self.assert_problem(409, lambda: self.accept(self.body("PICK_UP", cycle=identifier())))
        self.adapter.step(); self.device.finish(); self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "COMPLETED")

    def test_pause_resume_preserve_active_command_and_exclude_pause_time(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        pause = self.body("PAUSE")
        self.accept(pause); self.adapter.step(); self.adapter.step()
        self.assertEqual(self.adapter.status()["state"], "PAUSED")
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "RUNNING")
        self.now += 100
        self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "RUNNING")
        self.assert_problem(409, lambda: self.accept(self.body("RESUME", {"pause_command_id": identifier()})))
        self.complete(self.body("RESUME", {"pause_command_id": pause["command_id"]}))
        self.device.last = "move", 1
        self.device.finish()
        self.complete(body)
        self.assertEqual(sum(c[0] == "move" for c in self.device.calls), 1)
        self.assertEqual(sum(c[0] == "fork" for c in self.device.calls), 1)
        self.assertEqual(self.adapter.status()["last_completed_command_id"], body["command_id"])

    def test_pause_idle_uses_null_cycle(self):
        pause = self.body("PAUSE", cycle=None)
        self.complete(pause)
        self.complete(self.body("RESUME", {"pause_command_id": pause["command_id"]}, cycle=None))
        self.assertEqual(self.adapter.status()["state"], "IDLE")

    def test_lost_response_is_unknown_without_replaying_physical_move(self):
        self.device.fail_send = True
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.accept(body)
        for _ in range(3): self.adapter.step()
        self.assertEqual(sum(c[0] == "move" for c in self.device.calls), 1)
        self.assert_problem(409, lambda: self.accept(self.body("PICK_UP", cycle=identifier())))

    def test_esp32_reboot_and_expiry_cancel_resume(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        self.device.raw["boot_id"] = identifier()
        self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertEqual(self.adapter.status()["state"], "FAULT")

    def test_incompatible_or_offline_firmware_cannot_advance(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        self.device.raw.pop("boot_id")
        self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertFalse(self.adapter.status()["online"])

    def test_completion_timeout_does_not_resend(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        self.now += 31; self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertEqual(sum(c[0] == "move" for c in self.device.calls), 1)

    def test_process_restart_retains_unknown_and_old_boot_events(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        old_boot = self.adapter.boot_id
        self.adapter.close()
        self.adapter = Adapter(self.device, self.path, 30, clock=lambda: self.now)
        self.adapter.step()
        self.assertNotEqual(self.adapter.boot_id, old_boot)
        self.assertEqual(self.accept(body)[0], 200)
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertTrue(self.adapter.events(old_boot)["events"])
        self.assert_problem(409, lambda: self.accept(self.body("PICK_UP", cycle=identifier())))
        with self.assertRaises(ValueError): self.adapter.confirm_standby_recovery("physical check pending")
        self.device.raw.update(state="ready", position=2, servo_angle=85, fork_moving=False)
        self.adapter.step()
        self.adapter.confirm_standby_recovery("Server reconciled; operator confirmed unloaded standby")
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertEqual(self.adapter.status()["state"], "IDLE")

    def test_abrupt_process_restart_marks_pending_unknown_and_stops(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        self.adapter.db.close()  # Simulate crash without close() or a device stop.
        self.adapter._lease.close()  # Process exit releases the OS journal lease.
        self.adapter = Adapter(self.device, self.path, 30, clock=lambda: self.now)
        self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertEqual(self.device.raw["state"], "stopped")
        self.assertEqual(sum(c[0] == "move" for c in self.device.calls), 1)

    def test_event_replay_cursor_and_outbox_ack_loss(self):
        self.complete(self.body("PICK_UP"))
        self.complete(self.body("DELIVER", {"destination": 3, "inspection_id": identifier()}))
        page = self.adapter.events(self.adapter.boot_id, limit=1)
        second = self.adapter.events(self.adapter.boot_id, limit=1, cursor=page["next_cursor"])
        self.assertNotEqual(page["events"][0]["event_id"], second["events"][0]["event_id"])
        self.assertIsNone(second["next_cursor"])
        self.assert_problem(400, lambda: self.adapter.events(self.adapter.boot_id, after_seq=1, cursor=page["next_cursor"]))
        sent = []

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"event_id": sent[-1]["event_id"], "disposition": "DUPLICATE"}).encode()

        def transmit(request, timeout):
            self.assertEqual(request.headers["Authorization"], "Bearer event-token")
            sent.append(json.loads(request.data))
            if len(sent) == 1: raise OSError("Server recorded event, response lost")
            return Response()

        with patch("device_adapter.urlopen", transmit):
            with self.assertRaises(OSError): self.adapter.deliver_once("http://127.0.0.1/events", "event-token")
            self.adapter.deliver_once("http://127.0.0.1/events", "event-token")
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(len(self.adapter.events(self.adapter.boot_id)["events"]), 2)

    def test_old_status_cannot_complete_a_new_operation(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        self.device.raw.update(state="arrived", position=1, operation_id=identifier())
        self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "RUNNING")
        self.assertFalse(self.adapter.events(self.adapter.boot_id)["events"])

    def test_pause_waits_for_original_queued_operation_to_apply(self):
        body = self.body("PICK_UP")
        old = self.device.status()
        self.accept(body); self.adapter.step()
        applied = self.device.status()
        self.device.raw = old  # HTTP accepted, ESP32 loop has not applied it yet.
        pause = self.body("PAUSE")
        self.accept(pause); self.adapter.step()
        self.assertEqual(len(self.device.calls), 1)
        self.device.raw = applied
        self.adapter.step(); self.adapter.step()
        self.assertEqual(self.adapter.status()["state"], "PAUSED")
        self.assertEqual(sum(c[0] == "pause" for c in self.device.calls), 1)

    def test_revoked_idle_control_is_not_ready_and_boot_mismatch_blocks(self):
        self.device.raw["control_connected"] = False
        self.adapter.step()
        self.assertEqual(self.adapter.status()["state"], "FAULT")
        self.assert_problem(409, lambda: self.accept(self.body("PICK_UP")))

    def test_server_held_event_requires_operator_reconciliation(self):
        self.complete(self.body("PICK_UP"))
        event_id = self.adapter.events(self.adapter.boot_id)["events"][0]["event_id"]

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"event_id": event_id, "disposition": "HELD_FOR_REVIEW"}).encode()

        with patch("device_adapter.urlopen", return_value=Response()):
            self.adapter.deliver_once("http://127.0.0.1/events", "event-token")
        self.assertEqual(self.adapter.status()["state"], "FAULT")
        self.assert_problem(409, lambda: self.accept(self.body("DELIVER", {"destination":3,"inspection_id":identifier()})))

    def test_device_key_and_input_constraints(self):
        wrong = self.body("PICK_UP"); wrong["device_id"] = "ARM"
        # ASSEMBLE is the ARM action; routing a valid ARM command here is forbidden.
        wrong["action"] = "PAUSE"; wrong["cycle_id"] = None
        self.assert_problem(403, lambda: self.accept(wrong))
        bad = self.body("DELIVER", {"destination": 8, "inspection_id": identifier()})
        self.assert_problem(400, lambda: self.accept(bad))
        bad = self.body("PICK_UP"); bad["unexpected"] = True
        self.assert_problem(400, lambda: self.accept(bad))
        self.assert_problem(400, lambda: self.accept(self.body("RESUME")))

    def test_database_failure_prevents_physical_dispatch(self):
        self.adapter.db.execute("CREATE TRIGGER reject_write BEFORE INSERT ON agv_commands BEGIN SELECT RAISE(FAIL,'disk failure'); END")
        self.assert_problem(503, lambda: self.accept(self.body("PICK_UP")))
        self.adapter.step()
        self.assertTrue(all(c[0] == "stop" for c in self.device.calls))

    def test_second_process_cannot_mutate_the_live_journal(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step()
        with self.assertRaises(ValueError): Adapter(self.device, self.path, 30)
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "RUNNING")
        self.assertEqual(sum(c[0] == "move" for c in self.device.calls), 1)

    def test_all_completed_cycles_are_rejected_after_restart_and_migration(self):
        old_cycles = []
        for _ in range(2):
            self.cycle = identifier(); old_cycles.append(self.cycle)
            info = {"destination": 3, "inspection_id": identifier()}
            for action, params in [("PICK_UP", {}),
                                   ("DELIVER", info), ("UNLOAD", info), ("RETURN_STANDBY", {})]:
                self.complete(self.body(action, params))
        self.adapter.close()
        # Simulate a pre-fix journal lacking the persistent cycle tombstones.
        db = sqlite3.connect(self.path)
        try:
            with db: db.execute("DROP TABLE agv_cycles")
        finally:
            db.close()
        self.adapter = Adapter(self.device, self.path, 30, clock=lambda: self.now)
        self.adapter.step()
        count = len(self.device.calls)
        for cycle in old_cycles:
            self.assert_problem(409, lambda: self.accept(self.body("PICK_UP", cycle=cycle)))
        self.assertEqual(len(self.device.calls), count)

    def test_aborted_cycle_cannot_be_reused_after_operator_recovery(self):
        old = self.cycle
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step(); self.adapter._fault("test abort")
        self.device.raw.update(state="ready", position=2, servo_angle=85, fork_moving=False)
        self.adapter.confirm_standby_recovery("Server reconciled and operator checked standby")
        self.assert_problem(409, lambda: self.accept(self.body("PICK_UP", cycle=old)))
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")

    def test_storage_failure_during_motion_remains_queryable_and_worker_survives(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step(); self.device.finish()
        self.adapter.db.execute("CREATE TRIGGER reject_write BEFORE INSERT ON agv_commands BEGIN SELECT RAISE(FAIL,'disk failure'); END")
        polled = threading.Event()
        original_step = self.adapter.step
        calls = []

        def step():
            original_step()
            calls.append(True)
            if len(calls) >= 3: polled.set()

        with patch.object(self.adapter, "step", side_effect=step), patch.object(self.adapter, "deliver_once"):
            self.adapter.start("http://127.0.0.1/unused", "unused-test-token", 0.01)
            self.assertTrue(polled.wait(2), "Polling worker died on the persistent write failure")
            self.assertTrue(self.adapter.threads[0].is_alive())
            result = self.adapter.command(body["command_id"])
            self.assertEqual(result["state"], "UNKNOWN")
            self.assertIsNone(result["result_event_id"])
            validate("CommandResult", result)
            self.assertEqual(self.accept(body)[0], 200)
            self.assert_problem(503, lambda: self.accept(self.body("PICK_UP", cycle=identifier())))
            self.assertTrue(self.device.heartbeat_suspended)
            with self.adapter.lock:
                self.adapter.db.execute("DROP TRIGGER reject_write")
            self.adapter.step()
        result = self.adapter.command(body["command_id"])
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIsNotNone(result["result_event_id"])
        self.assertEqual(sum(c[0] == "move" for c in self.device.calls), 1)
        self.assertEqual([e["kind"] for e in self.adapter.events(self.adapter.boot_id)["events"]], ["DEVICE_FAILED"])

    def test_completion_commit_failure_does_not_publish_false_completion(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step(); self.device.finish(); self.adapter.step()
        self.adapter.step(); self.device.finish()
        self.adapter.db.execute("""CREATE TRIGGER reject_completion BEFORE INSERT ON agv_commands
            WHEN json_extract(NEW.result,'$.state')='COMPLETED'
            BEGIN SELECT RAISE(FAIL,'commit failure'); END""")
        self.adapter.step()
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "UNKNOWN")
        self.assertIsNone(self.adapter.status()["last_completed_command_id"])
        self.assertEqual(self.adapter.lane["phase"], "READY")
        self.assertEqual([e["kind"] for e in self.adapter.events(self.adapter.boot_id)["events"]], ["DEVICE_FAILED"])

    def test_recovery_rejects_accepted_work_and_normal_idle(self):
        with self.assertRaises(ValueError): self.adapter.confirm_standby_recovery("not faulted")
        body = self.body("PICK_UP")
        self.accept(body)
        with self.assertRaises(ValueError): self.adapter.confirm_standby_recovery("premature reset")
        self.assertEqual(self.adapter.lane["cycle_id"], self.cycle)
        self.assertEqual(self.adapter.command(body["command_id"])["state"], "ACCEPTED")
        self.assertFalse(self.device.calls)

    def test_recovery_reads_fresh_device_state_before_clearing_interlock(self):
        body = self.body("PICK_UP")
        self.accept(body); self.adapter.step(); self.adapter._fault("test abort")
        self.adapter.raw.update(state="ready", position=2, servo_angle=85, fork_moving=False)
        self.device.raw.update(state="moving", position=0)
        with self.assertRaises(ValueError): self.adapter.confirm_standby_recovery("stale cached standby")
        self.assertTrue(self.adapter.lane["recovery"])

    def test_http_auth_boot_and_response_dtos(self):
        token = "controller-token-" + "a" * 32
        server = serve(self.adapter, token, ("127.0.0.1", 0))
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()

        def call(path, body=None, authorized=True, boot=None):
            headers = {"Content-Type": "application/json"}
            if authorized: headers["Authorization"] = "Bearer " + token
            if boot: headers["X-Expected-Boot-Id"] = boot
            request = Request("http://127.0.0.1:" + str(server.server_port) + path,
                              data=json.dumps(body).encode() if body is not None else None, headers=headers)
            try:
                with urlopen(request, timeout=2) as response: return response.status, json.loads(response.read())
            except HTTPError as error: return error.code, json.loads(error.read())

        try:
            status, error = call("/device/v1/status", authorized=False)
            self.assertEqual(status, 401); validate("Error", error)
            status, value = call("/device/v1/status")
            self.assertEqual(status, 200); validate("DeviceStatus", value)
            body = self.body("PICK_UP")
            self.assertEqual(call("/device/v1/commands", body)[0], 400)
            self.assertEqual(call("/device/v1/commands", body, boot=identifier())[0], 409)
            status, value = call("/device/v1/commands", body, boot=self.adapter.boot_id)
            self.assertEqual(status, 202); validate("CommandResult", value)
            self.assertEqual(call("/device/v1/commands/" + body["command_id"])[0], 200)
            self.assertEqual(call("/device/v1/events?boot_id=" + self.adapter.boot_id)[0], 200)
            self.assertEqual(call("/device/v1/events?boot_id=" + self.adapter.boot_id + "&limit=101")[0], 400)
            self.assertEqual(call("/device/v1/events")[0], 400)
            self.assertEqual(call("/device/v1/commands/" + identifier())[0], 404)
            self.assertEqual(call("/device/v1/commands/invalid")[0], 400)
        finally:
            server.shutdown(); server.server_close(); worker.join()


    def test_cancelled_idle_or_legacy_inspection_blocks_delivery(self):
        for raw_state in ("stopped", "line_lost", "inspecting"):
            with self.subTest(raw_state=raw_state):
                self.complete(self.body("PICK_UP"))
                self.device.raw.update(state=raw_state, position=0)
                self.adapter.step()
                self.assertTrue(self.adapter.lane["recovery"])
                before = len(self.device.calls)
                self.assert_problem(409, lambda: self.accept(self.body("DELIVER", {"destination":3,"inspection_id":identifier()})))
                self.assertEqual(len(self.device.calls), before)
                self.adapter.close()
                self.path = str(Path(self.tmp.name) / (identifier()+".sqlite"))
                self.device = Device()
                self.adapter = Adapter(self.device, self.path, 30, clock=lambda:self.now)
                self.adapter.step();self.cycle=identifier()

    def test_darkroom_action_rejected_without_motion_or_fake_event(self):
        self.complete(self.body("PICK_UP"))
        before = len(self.device.calls)
        self.assert_problem(422, lambda:self.accept(self.body("GO_TO_INSPECTION")))
        self.assertEqual(len(self.device.calls), before)
        self.assertFalse(any(e["kind"]=="INSPECTION_ARRIVED" for e in self.adapter.events(self.adapter.boot_id)["events"]))


if __name__ == "__main__": unittest.main()
