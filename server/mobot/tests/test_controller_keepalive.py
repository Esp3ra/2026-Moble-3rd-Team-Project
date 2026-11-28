import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from factory_v1.keepalive import DeviceKeepalives
from factory_v1.common import Fault


class KeepaliveTests(unittest.TestCase):
    def engine(self, interval=.02):
        app=Flask(__name__)
        app.config['V1_SETTINGS']={'devices':{'LINE':{'controller_keepalive_seconds':interval,'base_url':'http://127.0.0.1:9','controller_token':'test-only'}}}
        return SimpleNamespace(app=app,acquire=lambda:None)

    def test_status_get_is_independent_and_stops_with_the_controller(self):
        seen=threading.Event();owned=threading.Event()
        engine=self.engine();engine.acquire=owned.set
        with patch('factory_v1.keepalive.HttpDevice') as client,patch('factory_v1.keepalive.contracts.device_status'):
            client.return_value.status.side_effect=lambda:seen.set()
            worker=DeviceKeepalives(engine)
            try:
                worker.start()
                self.assertTrue(seen.wait(2),'Independent status worker did not run')
                self.assertTrue(owned.is_set())
                client.return_value.submit.assert_not_called()
                self.assertEqual(client.call_args.args[1]['request_timeout_seconds'],.4)
            finally:worker.close()
            self.assertFalse(any(t.is_alive() for t in worker.threads))

    def test_lost_server_ownership_never_touches_hardware(self):
        attempted=threading.Event();engine=self.engine()
        def lost():
            attempted.set();raise RuntimeError('controller lease lost')
        engine.acquire=lost
        with patch('factory_v1.keepalive.HttpDevice') as client:
            worker=DeviceKeepalives(engine)
            try:
                worker.start();self.assertTrue(attempted.wait(2))
                client.return_value.status.assert_not_called()
            finally:worker.close()
        with self.assertRaises(Fault):DeviceKeepalives(self.engine(2))


if __name__=='__main__':unittest.main()
