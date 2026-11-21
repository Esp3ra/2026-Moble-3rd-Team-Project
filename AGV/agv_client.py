"""Authenticated AGV HTTP client for the central server. No device I/O on import."""

import json
import threading
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AGVClient:
    def __init__(self, base_url, key, controller_id=None):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.controller_id = controller_id or "server-" + uuid.uuid4().hex
        self.heartbeat_error = None
        self._stop = threading.Event()
        self._thread = None

    def _request(self, path, method="GET", **parameters):
        if parameters:
            path += "?" + urlencode(parameters)
        request = Request(
            self.base_url + path,
            data=b"" if method == "POST" else None,
            headers={
                "Authorization": "Bearer " + self.key,
                "X-AGV-Controller": self.controller_id,
                "X-AGV-Control": "1",
            },
            method=method,
        )
        with urlopen(request, timeout=1.0) as response:
            body = response.read()
        return json.loads(body) if body else None

    def connect(self):
        """Claim only an available session. Reconnection does not resume motion."""
        if self._thread and self._thread.is_alive():
            return
        self._request("/control/session", "POST")
        self._stop.clear()
        self.heartbeat_error = None
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="agv-heartbeat", daemon=True
        )
        self._thread.start()

    def _heartbeat_loop(self):
        while not self._stop.wait(0.5):
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

    def move(self, point):
        return self._request("/go", "POST", point=point)

    def inspect(self):
        return self._request("/inspection", "POST")

    def set_fork(self, angle):
        return self._request("/servo", "POST", angle=angle)

    def stop(self):
        return self._request("/stop", "POST")

    def close(self):
        """Stop explicitly, then end heartbeat. Device timeout remains a fallback."""
        self._stop.set()
        try:
            self.stop()
        finally:
            if self._thread:
                self._thread.join(timeout=1.2)
            self._thread = None
