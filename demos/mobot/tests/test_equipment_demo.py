import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from werkzeug.security import generate_password_hash

from app import ROOT, create_app
from auth import token_hash
from equipment import EquipmentDemo
from orders import connect


PASSWORD_HASH = generate_password_hash('test-password')


class EquipmentDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'factory.sqlite3'
        with patch.dict(os.environ, {'MOBOT_DB': str(self.path), 'MOBOT_ADMIN_TOKEN': ''}):
            self.app = create_app()
        with closing(connect(self.path)) as db, db:
            db.executemany('INSERT INTO ACCOUNTS(role_id,login_id,password_hash,name) VALUES(2,?,?,?)',
                           [('operator-a', PASSWORD_HASH, '운영자 A'), ('operator-b', PASSWORD_HASH, '운영자 B')])
        self.now = 100.
        self.device = EquipmentDemo(self.path, self.root / 'runs', ROOT / 'pallet_qrs', clock=lambda: self.now)
        self.addCleanup(self.device.close)
        self.app.extensions['uno_demo'] = self.device
        self.client = self.app.test_client()
        token = self.client.post('/api/auth/login', json={'login_id': 'operator-a', 'password': 'test-password'}).json['token']
        other = self.client.post('/api/auth/login', json={'login_id': 'operator-b', 'password': 'test-password'}).json['token']
        self.headers = {'Authorization': 'Bearer ' + token}
        self.other_headers = {'Authorization': 'Bearer ' + other}
        self.owner = token_hash(token)

    def order(self):
        response = self.client.post('/api/orders', json={
            'company': '데모 검증', 'phone': '010-0000-0000', 'address': '서울특별시 테스트',
            'head': 'mono', 'left': 'weld', 'right': 'fasten', 'request_id': str(uuid4()),
        })
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def start(self, job_id=None):
        data = {'job_id': job_id or self.order(), 'pallet_id': 'PALLET-01', 'position_ready': True, 'request_id': str(uuid4())}
        response = self.client.post('/api/equipment/start', json=data, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        return data, response.json

    def advance(self, elapsed, heartbeat=True):
        self.now = 100 + elapsed
        if heartbeat:
            self.device.bench.heartbeat(self.owner)
        self.device.step()

    def recognize(self):
        for elapsed in (.2, 1.6, 1.8):
            self.advance(elapsed)

    def finish_machine(self):
        for elapsed in (7.4, 10.4, 10.9, 12.9, 13.2):
            self.advance(elapsed)

    def test_selected_order_is_bound_at_qr_before_machine_completion(self):
        older = self.order()
        selected = self.order()
        self.start(selected)
        self.recognize()
        state = self.device.snapshot(self.owner)
        self.assertTrue(state['trial']['qr_ok'])
        self.assertTrue(state['trial']['qr_ack'])
        self.assertFalse(state['trial']['machine_done'])
        self.assertEqual(state['run']['database_status'], 'mapped')
        with closing(connect(self.path)) as db:
            mapping = db.execute('SELECT * FROM JOB_PALLET_MAPPINGS').fetchone()
            self.assertEqual((mapping['job_id'], mapping['pallet_id']), (selected, 'PALLET-01'))
            self.assertEqual(db.execute('SELECT status FROM ORDERS WHERE job_id=?', (older,)).fetchone()[0], 'PENDING')
            self.assertEqual(db.execute('SELECT status FROM ORDERS WHERE job_id=?', (selected,)).fetchone()[0], 'IN_PROGRESS')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_confirmation_requires_all_conditions_and_is_idempotent(self):
        data, _ = self.start()
        self.assertEqual(self.client.post('/api/equipment/confirm-ejection', json={}, headers=self.headers).status_code, 409)
        self.recognize()
        self.finish_machine()
        self.assertTrue(self.device.snapshot(self.owner)['can_confirm'])
        for _ in range(2):
            response = self.client.post('/api/equipment/confirm-ejection', json={}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['run']['state'], 'SUCCESS')
            self.assertIsNotNone(response.json['run']['operator_confirmed_at'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM EQUIPMENT_RUNS').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM JOB_PALLET_MAPPINGS').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT status FROM ORDERS WHERE job_id=?', (data['job_id'],)).fetchone()[0], 'IN_PROGRESS')

    def test_missing_qr_never_maps_or_confirms(self):
        self.start()
        self.device.qr_image = np.full((232, 232, 3), 238, np.uint8)
        self.recognize()
        self.finish_machine()
        self.assertEqual(self.device.bench.state, 'QR_UNCONFIRMED')
        self.assertFalse(self.device.snapshot(self.owner)['can_confirm'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM JOB_PALLET_MAPPINGS').fetchone()[0], 0)

    def test_db_retry_does_not_repeat_start_command(self):
        with patch.object(self.device.bench, '_write', wraps=self.device.bench._write) as write:
            self.start()
            self.advance(.2)
            self.advance(1.6)
            with patch.object(self.device, '_bind_order', side_effect=sqlite3.OperationalError('test DB failure')):
                self.advance(1.8)
            self.finish_machine()
            self.assertEqual(self.client.post('/api/equipment/confirm-ejection', json={}, headers=self.headers).status_code, 409)
            response = self.client.post('/api/equipment/database-retry', json={}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['run']['database_status'], 'mapped')
            self.assertEqual(sum(call.args[0] == 'a' for call in write.call_args_list), 1)
            self.assertEqual(self.client.post('/api/equipment/confirm-ejection', json={}, headers=self.headers).status_code, 200)

    def test_request_retry_owner_and_cancel_guards(self):
        data, first = self.start()
        duplicate = self.client.post('/api/equipment/start', json=data, headers=self.headers)
        self.assertEqual(duplicate.json['run']['run_id'], first['run']['run_id'])
        self.assertEqual(self.client.post('/api/equipment/start', json={**data, 'request_id': str(uuid4())}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/api/equipment/heartbeat', json={}, headers=self.other_headers).status_code, 409)
        self.assertEqual(self.client.post('/api/equipment/confirm-ejection', json={}, headers=self.other_headers).status_code, 409)
        self.assertEqual(self.client.post('/api/orders/' + data['job_id'] + '/cancel', headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/api/equipment/start', json=data).status_code, 401)

    def test_heartbeat_timeout_stops_and_does_not_restart(self):
        self.start()
        self.advance(.2)
        self.advance(5, heartbeat=False)
        self.assertEqual(self.device.bench.state, 'ERROR')
        self.advance(8, heartbeat=False)
        with closing(connect(self.path)) as db:
            run = db.execute('SELECT * FROM EQUIPMENT_RUNS').fetchone()
            self.assertIsNotNone(run['finished_at'])
            self.assertEqual(run['state'], 'ERROR')

    def test_server_restart_marks_run_interrupted_and_keeps_mapping(self):
        self.start()
        self.recognize()
        with closing(connect(self.path)) as db, db:
            db.execute('UPDATE EQUIPMENT_DEVICE_LEASES SET heartbeat_at = 0')
        restarted = EquipmentDemo(self.path, self.root / 'restarted', ROOT / 'pallet_qrs')
        self.addCleanup(restarted.close)
        state = restarted.snapshot(self.owner)
        self.assertEqual(state['run']['state'], 'INTERRUPTED')
        self.assertIsNone(restarted.bench.trial)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM JOB_PALLET_MAPPINGS').fetchone()[0], 1)

    def test_second_device_process_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '다른 중앙 서버'):
            EquipmentDemo(self.path, self.root / 'second', ROOT / 'pallet_qrs')

    def test_lost_device_lease_blocks_further_start(self):
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE EQUIPMENT_DEVICE_LEASES SET instance_id = 'replacement'")
        self.device.step()
        self.assertFalse(self.device.snapshot(self.owner)['can_start'])
        response = self.client.post('/api/equipment/start', json={
            'job_id': self.order(), 'pallet_id': 'PALLET-01',
            'position_ready': True, 'request_id': str(uuid4()),
        }, headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_confirm_rechecks_active_mapping(self):
        self.start()
        self.recognize()
        self.finish_machine()
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE JOB_PALLET_MAPPINGS SET is_active = 0, unmapped_at = CURRENT_TIMESTAMP")
        self.assertEqual(self.client.post('/api/equipment/confirm-ejection', json={}, headers=self.headers).status_code, 409)

    def test_timing_validation_and_jpeg(self):
        values = {'ir1_delay_ms': 500, 'robot_wait_ms': 1000, 'ir2_delay_ms': 1500}
        response = self.client.post('/api/equipment/timings', json={'values': values}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['timings']['values'], values)
        self.assertEqual(self.client.post('/api/equipment/timings', json={'values': {**values, 'ir1_delay_ms': 1}}, headers=self.headers).status_code, 409)
        response = self.client.get('/api/equipment/frame.jpg', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        self.assertTrue(response.data.startswith(b'\xff\xd8'))

    def test_concurrent_start_only_creates_one_run(self):
        first, second = self.order(), self.order()
        def send(job_id):
            with self.app.test_client() as client:
                return client.post('/api/equipment/start', json={'job_id': job_id, 'pallet_id': 'PALLET-01', 'position_ready': True, 'request_id': str(uuid4())}, headers=self.headers).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [first, second]))
        self.assertEqual(sorted(results), [200, 409])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM EQUIPMENT_RUNS').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
