"""Exercise the server helper against a local fake HTTP device only."""

import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

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
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                test.calls.append((self.command, self.path, dict(self.headers)))
                if self.path == "/heartbeat":
                    test.heartbeat.set()
                code = 409 if self.path == "/heartbeat" and test.reject_heartbeat else 200
                self.send_response(code)
                self.end_headers()
                self.wfile.write(json.dumps({"state": "ready"}).encode())

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.client = module.AGVClient(
            "http://127.0.0.1:" + str(self.server.server_port), "a" * 64, "test-server"
        )

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

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
        self.client.close()
        paths = [item[1] for item in self.calls]
        self.assertEqual(paths.count("/control/session"), 1)
        for path in ("/heartbeat", "/inspection", "/go?point=3", "/servo?angle=60", "/stop"):
            self.assertIn(path, paths)
        for method, path, headers in self.calls:
            self.assertEqual(headers["Authorization"], "Bearer " + "a" * 64)
            self.assertEqual(headers["X-Agv-Controller"], "test-server")
            self.assertEqual(method, "GET" if path == "/status" else "POST")

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


if __name__ == "__main__":
    unittest.main()
