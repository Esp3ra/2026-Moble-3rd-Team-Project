import hashlib
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

import test_factory_v1 as fixtures
from app import create_app
from factory_v1.common import uid
from orders import connect
from scripts.prepare_lan_test import prepare
from wireless_pi.__main__ import validate_server_url,NoRedirect


class LanTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.FactoryTests('test_stock_shared_and_public_privacy');self.case.setUp();self.addCleanup(self.case.doCleanups)

    def lan_app(self):
        self.case.engine.close();token='private-lan-test-token'
        settings=dict(enabled=True,transport='LAN_HTTP_TEST',token_hash=hashlib.sha256(token.encode()).hexdigest())
        (self.case.path.parent/'wireless-test.json').write_text(json.dumps(settings))
        with patch.dict(os.environ,{'MOBOT_DB':str(self.case.path),'MOBOT_V1_MODE':'SIMULATION','MOBOT_V1_BACKGROUND':'0'}):app=create_app()
        self.addCleanup(app.extensions['factory_v1'].close)
        return app,app.test_client(),{'Authorization':'Bearer '+token}

    def test_http_requires_explicit_lan_mode_and_numeric_private_ip(self):
        for url in ('http://192.168.0.154:8000','http://10.0.0.5:8000','http://127.0.0.1:8000'):
            self.assertEqual(validate_server_url(dict(server_url=url,transport='LAN_HTTP_TEST')),url)
        for url in ('http://8.8.8.8:8000','http://0.0.0.0:8000','http://169.254.1.1:8000','http://example.com','http://u:p@192.168.0.154','http://192.168.0.154/path','http://192.168.0.154?token=bad'):
            with self.assertRaises(ValueError):validate_server_url(dict(server_url=url,transport='LAN_HTTP_TEST'))
        with self.assertRaises(ValueError):validate_server_url(dict(server_url='http://192.168.0.154:8000'))
        self.assertEqual(validate_server_url(dict(server_url='https://aggregate-siamese-unwrapped.ngrok-free.dev')),'https://aggregate-siamese-unwrapped.ngrok-free.dev')

    def test_probe_auth_private_scope_and_no_poll_or_process_side_effects(self):
        app,client,auth=self.lan_app();probe='/wireless-test/v1/probe';data=dict(device_id='LINE',message='test')
        self.assertEqual(client.get('/ping').json['transport'],'LAN_HTTP_TEST')
        self.assertEqual(client.post(probe,json=data).status_code,401)
        self.assertEqual(client.post(probe,json=data,headers=auth).status_code,200)
        public={**auth,'X-Forwarded-For':'8.8.8.8'}
        self.assertEqual(client.post(probe,json=data,headers=public).status_code,403)
        self.assertEqual(client.get('/ping',headers=public).status_code,403)
        bridge=app.extensions['wireless_bench']
        with bridge.tx() as db:
            self.assertIsNone(db.execute('SELECT boot FROM device').fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM commands').fetchone()[0],0)
        bad=dict(event_id=uid(),device_id='LINE',boot_id=uid(),event_seq=1,body=dict(event_type='TEST_PING'))
        self.assertEqual(client.post('/api/v1/device-events',json=bad,headers=auth).status_code,400)
        packet=dict(boot_id=uid(),sequence=1,status=dict(serial_connected=False,serial_ready=False,protocol='NETWORK_ONLY',message='test',lines=[]),results=[])
        self.assertEqual(client.post('/wireless-test/v1/poll',json=packet,headers=public).status_code,403)
        self.assertEqual(client.post('/wireless-test/v1/poll',json=packet,headers=auth).status_code,200)
        snapshot=client.get('/api/v1/wireless-test/status',headers=self.case.auth).json
        self.assertEqual(snapshot['transport'],'LAN_HTTP_TEST');self.assertTrue(snapshot['online'])
        with closing(connect(self.case.path)) as db:
            for table in ('V1_CYCLES','V1_COMMANDS','V1_ORDERS'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_packaging_preserves_existing_key_ngrok_settings_and_order_data(self):
        self.case.order();folder=self.case.path.parent;token='existing-test-only-token'
        pc=folder/'wireless-test.json';ngrok=folder/'wireless-test-pi-config.json'
        pc.write_text(json.dumps(dict(enabled=True,token_hash=hashlib.sha256(token.encode()).hexdigest())))
        original=dict(server_url='https://test.ngrok-free.dev',device_token=token,serial_port='/dev/ttyUSB0')
        ngrok.write_text(json.dumps(original));old=ngrok.read_bytes()
        target=folder/'lan.zip';private=prepare(self.case.path,'http://192.168.0.154:8000',target)
        config=json.loads(private.read_text());self.assertEqual(config['device_token'],token)
        self.assertEqual(config['transport'],'LAN_HTTP_TEST');self.assertEqual(config['serial_port'],'/dev/ttyUSB0')
        self.assertEqual(ngrok.read_bytes(),old)
        import zipfile
        with zipfile.ZipFile(target) as archive:
            self.assertIn('test_lan_connection.py',archive.namelist());self.assertIn('LAN_TEST.md',archive.namelist())
            self.assertEqual(json.loads(archive.read('wireless_pi/config.json'))['device_token'],token)
        with closing(connect(self.case.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM V1_ORDERS').fetchone()[0],1)

    def test_missing_original_key_does_not_rotate_or_overwrite_pc_settings(self):
        pc=self.case.path.parent/'wireless-test.json';pc.write_text(json.dumps(dict(enabled=True,token_hash='a'*64)))
        old=pc.read_bytes()
        with self.assertRaises(ValueError):prepare(self.case.path,'http://192.168.0.154:8000',self.case.path.parent/'lan.zip')
        self.assertEqual(pc.read_bytes(),old)

    def test_redirect_never_forwards_device_token(self):
        import urllib.error
        import urllib.request
        request=urllib.request.Request('http://127.0.0.1:8000')
        with self.assertRaises(urllib.error.HTTPError):NoRedirect().redirect_request(request,None,302,'Moved',{},'http://8.8.8.8')
