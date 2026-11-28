import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from watch_diagnostics import read_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_observer_only_reads_diagnostics_and_does_not_log_arbitrary_fields(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append((self.command, self.path, dict(self.headers)))
                body = json.dumps({'boot_number': 2, 'password': 'must-not-log', 'events': [
                    {'kind': 'WIFI_DISCONNECTED', 'reason': 201, 'rssi_dbm': -56, 'secret': 'must-not-log'}]}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            result = read_diagnostics(f'http://127.0.0.1:{server.server_port}', 'test-' + 'a' * 40)
            self.assertEqual([(m, p) for m, p, _ in calls], [('GET', '/diagnostics')])
            headers = {k.lower(): v for k, v in calls[0][2].items()}
            self.assertNotIn('x-agv-controller', headers)
            self.assertNotIn('x-agv-control', headers)
            self.assertNotIn('must-not-log', json.dumps(result))
            self.assertEqual(result['events'][0]['reason'], 201)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_embedded_credentials_and_non_device_urls_are_rejected(self):
        for url in ('http://user:password@127.0.0.1', 'http://127.0.0.1/path', 'file:///tmp/device', 'http://127.0.0.1?key=x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                read_diagnostics(url, 'unused')


if __name__ == '__main__': unittest.main()
