"""Authenticated AGV HTTP client for the central server. No device I/O on import."""

import json
import http.client
import io
import math
import threading
import time
import uuid
from collections import deque
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit


class AGVClient:
    def __init__(self, base_url, key, controller_id=None, heartbeat_seconds=0.3, request_timeout=1.0):
        if (not math.isfinite(heartbeat_seconds) or not math.isfinite(request_timeout)
                or heartbeat_seconds <= 0 or request_timeout <= 0):
            raise ValueError("Positive timing profile required")
        self.base_url = base_url.rstrip("/")
        address = urlsplit(self.base_url)
        if (address.scheme not in ("http", "https") or not address.hostname
                or address.username or address.password or address.query or address.fragment):
            raise ValueError("An HTTP(S) device URL without credentials is required")
        self._address = address
        self.key = key
        self.controller_id = controller_id or "server-" + uuid.uuid4().hex
        self.heartbeat_seconds = heartbeat_seconds
        self.request_timeout = request_timeout
        self.heartbeat_error = None
        self._stop = threading.Event()
        self._thread = None
        self._http_condition = threading.Condition()
        self._http_busy = False
        self._heartbeats_waiting = 0
        self._connection = None
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_sequence = 0
        self._diagnostics = deque(maxlen=256)

    def transport_diagnostics(self):
        with self._diagnostic_lock:
            return list(self._diagnostics)

    def _acquire_http(self, heartbeat):
        with self._http_condition:
            if heartbeat:
                self._heartbeats_waiting += 1
            try:
                while self._http_busy or (not heartbeat and self._heartbeats_waiting):
                    if heartbeat and self._stop.is_set():
                        return False
                    self._http_condition.wait()
                if heartbeat and self._stop.is_set():
                    return False
                self._http_busy = True
                return True
            finally:
                if heartbeat:
                    self._heartbeats_waiting -= 1
                self._http_condition.notify_all()

    def _drop_connection(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _request(self, path, method="GET", operation_id=None, **parameters):
        entered = time.monotonic()
        if parameters:
            path += "?" + urlencode(parameters)
        headers = {
            "Authorization": "Bearer " + self.key,
            "X-AGV-Controller": self.controller_id,
            "X-AGV-Control": "1",
        }
        if operation_id:
            headers["X-AGV-Operation"] = operation_id
        if not self._acquire_http(path == "/heartbeat"):
            return None
        acquired = time.monotonic()
        phase, outcome, status_code, result = 'request', 'OK', None, None
        try:
            if self._connection is None:
                # HTTPSConnection uses the default verified TLS context.
                connection_type = (http.client.HTTPSConnection if self._address.scheme == "https"
                                   else http.client.HTTPConnection)
                self._connection = connection_type(self._address.hostname, self._address.port,
                                                    timeout=self.request_timeout)
            target = self._address.path.rstrip("/") + path
            self._connection.request(method, target, body=b"" if method == "POST" else None,
                                     headers=headers)
            phase = 'headers'
            response = self._connection.getresponse()
            status_code = response.status
            phase = 'body'
            body = response.read()
            if not 200 <= response.status < 300:
                raise HTTPError(self.base_url + path, response.status, response.reason,
                                response.headers, io.BytesIO(body))
            phase = 'decode'
            result = json.loads(body) if body else None
            if response.will_close:
                self._drop_connection()
            return result
        except Exception as error:
            outcome = type(error).__name__
            # A lost response may already have moved the device. Discard this
            # connection and propagate the failure, without replaying a request.
            self._drop_connection()
            raise
        finally:
            finished = time.monotonic()
            with self._http_condition:
                self._http_busy = False
                self._http_condition.notify_all()
            # Bounded memory only on the control thread; a separate observer may
            # persist this allowlisted telemetry. Never log URLs, keys or query values.
            record=dict(monotonic=finished,path=path.split('?')[0],method=method,
                        queue_ms=round((acquired-entered)*1000,2),elapsed_ms=round((finished-acquired)*1000,2),
                        outcome=outcome,phase=phase,http_status=status_code)
            if record['path']=='/status' and isinstance(result,dict):
                record['device']={k:result[k] for k in ('state','position','control_connected','mode','boot_id') if k in result}
            with self._diagnostic_lock:
                self._diagnostic_sequence += 1
                record['sequence'] = self._diagnostic_sequence
                self._diagnostics.append(record)

    def connect(self):
        """Claim only an available session. Reconnection does not resume motion."""
        if self._thread and self._thread.is_alive():
            if not self._stop.is_set():
                return
            self._thread.join(timeout=self.request_timeout + 0.2)
            if self._thread.is_alive():
                raise RuntimeError("Previous heartbeat request is still exiting")
        self._request("/control/session", "POST")
        self._stop.clear()
        self.heartbeat_error = None
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="agv-heartbeat", daemon=True
        )
        self._thread.start()

    def _heartbeat_loop(self):
        deadline = time.monotonic() + self.heartbeat_seconds
        while not self._stop.wait(max(0, deadline - time.monotonic())):
            try:
                self._request("/heartbeat", "POST")
                self.heartbeat_error = None
            except Exception as error:
                # Do not reclaim an expired session or retry motion commands.
                self.heartbeat_error = error
            deadline += self.heartbeat_seconds
            now = time.monotonic()
            if deadline <= now:
                # Skip missed slots instead of sending a burst of late renewals.
                deadline += (math.floor((now - deadline) / self.heartbeat_seconds) + 1) * self.heartbeat_seconds

    def status(self):
        return self._request("/status")

    def set_position(self, point):
        return self._request("/position", "POST", point=point)

    def set_mode(self, value):
        return self._request("/mode", "POST", value=value)

    def move(self, point, operation_id=None):
        return self._request("/go", "POST", operation_id=operation_id, point=point)

    def inspect(self, operation_id=None):
        return self._request("/inspection", "POST", operation_id=operation_id)

    def set_fork(self, angle, operation_id=None):
        return self._request("/servo", "POST", operation_id=operation_id, angle=angle)

    def pause(self, operation_id=None):
        return self._request("/pause", "POST", operation_id=operation_id)

    def resume(self, operation_id=None):
        return self._request("/resume", "POST", operation_id=operation_id)

    def stop(self):
        return self._request("/stop", "POST")

    def suspend_heartbeat(self):
        """Revoke renewal immediately; an in-flight request may finish once.

        No subsequent heartbeat will be sent. Even if /stop is unreachable,
        the firmware lease expires after its last received heartbeat.
        Reconnection requires an explicit connect(), never a motion replay.
        """
        self._stop.set()
        with self._http_condition:
            self._http_condition.notify_all()

    def close(self):
        """Stop explicitly, then end heartbeat. Device timeout remains a fallback."""
        self.suspend_heartbeat()
        try:
            self.stop()
        finally:
            if self._thread:
                self._thread.join(timeout=self.request_timeout + 0.2)
                if not self._thread.is_alive():
                    self._thread = None
            with self._http_condition:
                while self._http_busy:
                    self._http_condition.wait()
                self._drop_connection()
