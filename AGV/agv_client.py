"""Authenticated AGV HTTP client for the central server. No device I/O on import."""

import json
import math
import threading
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AGVClient:
    def __init__(self, base_url, key, controller_id=None, heartbeat_seconds=0.5, request_timeout=1.0):
        if (not math.isfinite(heartbeat_seconds) or not math.isfinite(request_timeout)
                or heartbeat_seconds <= 0 or request_timeout <= 0):
            raise ValueError("Positive timing profile required")
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.controller_id = controller_id or "server-" + uuid.uuid4().hex
        self.heartbeat_seconds = heartbeat_seconds
        self.request_timeout = request_timeout
        self.heartbeat_error = None
        self._stop = threading.Event()
        self._thread = None

    def _request(self, path, method="GET", operation_id=None, **parameters):
        if parameters:
            path += "?" + urlencode(parameters)
        headers = {
            "Authorization": "Bearer " + self.key,
            "X-AGV-Controller": self.controller_id,
            "X-AGV-Control": "1",
        }
        if operation_id:
            headers["X-AGV-Operation"] = operation_id
        request = Request(
            self.base_url + path,
            data=b"" if method == "POST" else None,
            headers=headers,
            method=method,
        )
        with urlopen(request, timeout=self.request_timeout) as response:
            body = response.read()
        return json.loads(body) if body else None

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
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                self._request("/heartbeat", "POST")
                self.heartbeat_error = None
            except Exception as error:
                # Do not reclaim an expired session or retry motion commands.
                self.heartbeat_error = error

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
