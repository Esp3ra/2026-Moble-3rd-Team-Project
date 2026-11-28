"""Full workflow through real adapter HTTP, with inert AGV and isolated DB."""
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from werkzeug.serving import make_server, WSGIRequestHandler
from factory_v1.devices import HttpDevice
from orders import connect
import test_factory_v1 as factory_tests

AGV = Path(__file__).resolve().parents[3] / 'AGV'
sys.path.insert(0, str(AGV))
sys.path.insert(0, str(AGV / 'tests'))
from device_adapter import Adapter, serve
import test_device_adapter as adapter_tests


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args):
        pass


class AgvHttpIntegrationTests(unittest.TestCase):
    def test_order_completion_reaches_database_and_wpf_api_through_adapter_http(self):
        harness = factory_tests.FactoryTests('test_full_cycle_has_qr_arm_inspection_unload_and_final_return')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        device = adapter_tests.Device()
        adapter = Adapter(device, str(Path(temporary.name) / 'journal.sqlite3'), 30,
                          clock=lambda: harness.now)
        self.addCleanup(adapter.close)
        adapter.step()
        token = 'test-controller-' + 'x' * 32
        server = serve(adapter, token, ('127.0.0.1', 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        callback = make_server('127.0.0.1', 0, harness.app, threaded=True, request_handler=QuietHandler)
        callback_thread = threading.Thread(target=callback.serve_forever, daemon=True)
        callback_thread.start()
        def close_servers():
            server.shutdown(); callback.shutdown()
            server.server_close(); callback.server_close()
            thread.join(); callback_thread.join()
        self.addCleanup(close_servers)
        harness.engine.clients['AGV'] = HttpDevice('AGV', {
            'base_url': f'http://127.0.0.1:{server.server_port}',
            'controller_token': token, 'request_timeout_seconds': 2})
        harness.tick()
        order, _ = harness.order()
        harness.accept(order)
        harness.start()
        for _ in range(120):
            harness.tick()
            adapter.step()
            if device.last is not None:
                device.finish()
            adapter.deliver_once(f'http://127.0.0.1:{callback.server_port}/api/v1/device-events', 'agv-test')
        detail = harness.client.get('/api/v1/orders/' + order['order_id'], headers=harness.auth).json
        self.assertEqual(detail['factory_status'], 'COMPLETED', detail)
        self.assertEqual(detail['dispatch_station'], 3)
        self.assertEqual((device.raw['position'], device.raw['servo_angle']), (2, 85))
        with closing(connect(harness.path)) as db:
            rows = db.execute("SELECT action,state FROM V1_COMMANDS WHERE device_id='AGV' ORDER BY rowid").fetchall()
            self.assertEqual([tuple(row) for row in rows], [(action, 'COMPLETED') for action in
                             ('PICK_UP', 'DELIVER', 'UNLOAD', 'RETURN_STANDBY')])
            self.assertEqual(db.execute('SELECT count(*) FROM V1_INSPECTIONS').fetchone()[0], 1)
        events = adapter.events(adapter.boot_id)['events']
        self.assertEqual([e['kind'] for e in events],
                         ['LIFT_FINISHED', 'DESTINATION_ARRIVED', 'UNLOAD_FINISHED', 'STANDBY_RETURNED'])
        calls = len(device.calls)
        duplicate = harness.client.post('/api/v1/device-events', json=events[2],
                                       headers={'Authorization': 'Bearer agv-test'})
        self.assertEqual(duplicate.json['disposition'], 'DUPLICATE')
        self.assertEqual(len(device.calls), calls)
        history = harness.client.get('/api/v1/orders/' + order['order_id'] + '/history', headers=harness.auth)
        self.assertEqual(history.status_code, 200)
        self.assertIn('UNLOAD_FINISHED', json.dumps(history.json))


if __name__ == '__main__':
    unittest.main()
