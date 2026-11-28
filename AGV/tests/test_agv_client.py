"""Exercise the server helper against a local fake HTTP device only."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "agv_client", Path(__file__).resolve().parents[1] / "agv_client.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.heartbeat = threading.Event()
        self.reject_heartbeat = False
        self.reject_stop = False
        self.source_ports = []
        self.heartbeat_delay = 0
        self.hold_status = False
        self.status_entered = threading.Event()
        self.release_status = threading.Event()
        self.heartbeat_times = []
        self.active_requests = 0
        self.maximum_active_requests = 0
        self.record_lock = threading.Lock()
        test = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def handle(self):
                try:
                    super().handle()
                except ConnectionError:
                    pass  # Expected when a test discards a failed connection.
            def log_message(self, *args):
                pass

            def do_GET(self):
                with test.record_lock:
                    test.calls.append((self.command, self.path, dict(self.headers)))
                    test.source_ports.append(self.client_address[1])
                    test.active_requests += 1
                    test.maximum_active_requests = max(test.maximum_active_requests, test.active_requests)
                if self.path == "/status" and test.hold_status:
                    test.status_entered.set()
                    test.release_status.wait(3)
                if self.path == "/heartbeat":
                    test.heartbeat_times.append(time.monotonic())
                    test.heartbeat.set()
                    time.sleep(test.heartbeat_delay)
                code = 409 if self.path == "/heartbeat" and test.reject_heartbeat else 200
                if self.path == "/stop" and test.reject_stop: code = 503
                body = json.dumps({"state": "ready"}).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except ConnectionError:
                    pass
                finally:
                    with test.record_lock:
                        test.active_requests -= 1

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.client = module.AGVClient(
            "http://127.0.0.1:" + str(self.server.server_port), "a" * 64, "test-server"
        )

    def tearDown(self):
        self.release_status.set()
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def test_reuses_http_connection(self):
        self.client.status()
        self.client.status()
        self.client.move(3)
        self.assertEqual(len(set(self.source_ports)), 1)

    def test_pending_heartbeat_precedes_queued_status_and_requests_are_serial(self):
        self.hold_status = True
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.client.status)
            self.assertTrue(self.status_entered.wait(1))
            second = pool.submit(self.client.status)
            heartbeat = pool.submit(self.client._request, "/heartbeat", "POST")
            try:
                deadline = time.monotonic() + 1
                while self.client._heartbeats_waiting == 0 and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(self.client._heartbeats_waiting, 1)
            finally:
                self.release_status.set()
            for future in (first, second, heartbeat):
                future.result(timeout=2)
        self.assertEqual([path for _, path, _ in self.calls], ["/status", "/heartbeat", "/status"])
        self.assertEqual(self.maximum_active_requests, 1)

    def test_lost_move_response_is_not_replayed_and_next_read_reconnects(self):
        self.client.status()
        connection = self.client._connection
        receive = connection.getresponse
        def lose_response():
            response = receive()
            response.read()  # Device really accepted it; the caller loses the result.
            raise TimeoutError("lost response")
        with patch.object(connection, "getresponse", side_effect=lose_response):
            with self.assertRaises(TimeoutError):
                self.client.move(3)
        self.assertIsNone(self.client._connection)
        self.client.status()
        self.assertIsNot(self.client._connection, connection)
        self.assertEqual(sum(path == "/go?point=3" for _, path, _ in self.calls), 1)
        traces=self.client.transport_diagnostics()
        failed=next(row for row in traces if row['path']=='/go')
        self.assertEqual(failed['outcome'],'TimeoutError')
        self.assertEqual(failed['phase'],'headers')
        self.assertNotIn('point=3',json.dumps(traces))
        self.assertNotIn(self.client.key,json.dumps(traces))

    def test_heartbeat_period_does_not_include_response_duration(self):
        self.client.heartbeat_seconds = .2
        self.heartbeat_delay = .12
        self.client.connect()
        deadline = time.monotonic() + 2
        while len(self.heartbeat_times) < 4 and time.monotonic() < deadline:
            time.sleep(.01)
        self.client.suspend_heartbeat()
        self.assertGreaterEqual(len(self.heartbeat_times), 4)
        elapsed = self.heartbeat_times[3] - self.heartbeat_times[0]
        # Fixed-delay scheduling needs >= .96s; a monotonic schedule needs .6s.
        self.assertLess(elapsed, .88)

    def test_suspended_queued_heartbeat_is_not_sent(self):
        self.hold_status = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.client.status)
            self.assertTrue(self.status_entered.wait(1))
            heartbeat = pool.submit(self.client._request, "/heartbeat", "POST")
            self.client.suspend_heartbeat()
            self.release_status.set()
            first.result(timeout=2)
            heartbeat.result(timeout=2)
        self.assertFalse(any(path == "/heartbeat" for _, path, _ in self.calls))

    def test_commands_and_independent_heartbeat(self):
        self.client.connect()
        self.client.connect()  # Do not claim twice or create another worker.
        self.assertTrue(self.heartbeat.wait(1.5))
        self.assertEqual(self.client.status()["state"], "ready")
        self.client.set_mode("auto")
        self.client.set_position(2)
        self.client.inspect()
        self.client.move(3)
        self.client.set_fork(60)
        self.client.pause("pause-operation")
        self.client.resume("resume-operation")
        self.client.close()
        paths = [item[1] for item in self.calls]
        self.assertEqual(paths.count("/control/session"), 1)
        for path in ("/heartbeat", "/inspection", "/go?point=3", "/servo?angle=60", "/pause", "/resume", "/stop"):
            self.assertIn(path, paths)
        for method, path, headers in self.calls:
            headers = {key.lower(): value for key, value in headers.items()}
            self.assertEqual(headers["authorization"], "Bearer " + "a" * 64)
            self.assertEqual(headers["x-agv-controller"], "test-server")
            self.assertEqual(method, "GET" if path == "/status" else "POST")
            if path in ("/pause", "/resume"):
                self.assertEqual(headers["x-agv-operation"], path[1:] + "-operation")

    def test_rejected_heartbeat_does_not_reclaim_or_replay_motion(self):
        self.reject_heartbeat = True
        self.client.connect()
        self.client.move(3)
        self.assertTrue(self.heartbeat.wait(1.5))
        # Join the active request before inspecting its recorded failure.
        self.client.close()
        self.assertIsNotNone(self.client.heartbeat_error)
        paths = [item[1] for item in self.calls]
        self.assertEqual(paths.count("/control/session"), 1)
        self.assertEqual(paths.count("/go?point=3"), 1)

    def test_adapter_stop_failure_disables_heartbeat_until_explicit_reconnect(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from device_adapter import Adapter, identifier
        from test_device_adapter import Device
        device = Device()
        self.client.status = device.status
        self.client.move = device.move
        self.client.set_fork = device.set_fork
        with tempfile.TemporaryDirectory() as temporary:
            adapter = Adapter(self.client, str(Path(temporary) / "journal.sqlite"), 30)
            try:
                self.client.connect()
                self.assertTrue(self.heartbeat.wait(1.5))
                adapter.step()
                command = dict(command_id=identifier(), device_id="AGV", cycle_id=identifier(),
                               action="PICK_UP", params={})
                adapter.accept(command, adapter.boot_id); adapter.step()
                self.reject_stop = True
                adapter._fault("injected stop transport failure")
                self.client._thread.join(timeout=1.5)
                self.assertFalse(self.client._thread.is_alive())
                count = sum(path == "/heartbeat" for _, path, _ in self.calls)
                time.sleep(0.6)
                self.assertEqual(sum(path == "/heartbeat" for _, path, _ in self.calls), count)
                self.assertEqual(adapter.command(command["command_id"])["state"], "UNKNOWN")
                self.assertEqual(device.raw["state"], "moving")  # Device timeout is the fallback.
                self.assertEqual(sum(c[0] == "move" for c in device.calls), 1)
                self.reject_stop = False
                self.heartbeat.clear()
                self.client.connect()  # Explicit control recovery, not motion replay.
                self.assertTrue(self.heartbeat.wait(1.5))
                self.assertEqual(sum(c[0] == "move" for c in device.calls), 1)
            finally:
                self.reject_stop = False
                adapter.close()


if __name__ == "__main__":
    unittest.main()
