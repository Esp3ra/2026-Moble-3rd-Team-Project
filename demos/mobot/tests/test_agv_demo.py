import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from werkzeug.security import generate_password_hash

from agv import AgvDemo, delivery_steps
from app import ROOT, create_app
from auth import token_hash
from equipment import EquipmentDemo
from orders import SCHEMA_PATH, connect, destination_for, initialize_orders

HASH = generate_password_hash('agv-test')


class DestinationTests(unittest.TestCase):
    def test_address_points(self):
        for address, point in [('서울특별시 중구',3), ('(04500) 서울 중구',3),
                               ('경기도 평택시 중앙로',4), ('평택시 중앙로',4),
                               ('12345 경기도 수원시 영통구',5), ('수원시 영통구',5),
                               ('경기도 성남시',None), ('부산광역시',None), ('천안시',None)]:
            self.assertEqual(destination_for(address),point,address)

    def test_old_schema_migration_preserves_children_and_idempotency(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'old.sqlite3'
            schema = SCHEMA_PATH.read_text(encoding='utf-8-sig').replace('target_station IN (3, 4, 5, 6, 7)', 'target_station IN (1, 2, 3)')
            with closing(connect(path)) as db, db:
                db.executescript(schema)
                db.executemany("INSERT INTO ORDERS(job_id,model_name,target_head_id,target_arm_l_id,target_arm_r_id,address,target_station,web_request_id) VALUES(?,'A1a','head-A','arm-a','arm-1',?,?,?)", [
                    ('mobot-1','서울특별시 중구',2,'request-one'),
                    ('mobot-2','경기도 평택시 중앙로',2,'request-two'),
                    ('mobot-3','부산광역시',3,'request-three')])
                db.execute("INSERT INTO JOB_PALLET_MAPPINGS(job_id,pallet_id) VALUES('mobot-1','PALLET-01')")
            for _ in range(2):
                initialize_orders(path)
            with closing(connect(path)) as db:
                self.assertEqual([r[0] for r in db.execute('SELECT target_station FROM ORDERS ORDER BY job_id')],[3,4,None])
                self.assertEqual(db.execute('SELECT job_id FROM JOB_PALLET_MAPPINGS').fetchone()[0],'mobot-1')
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE ORDERS SET target_station=1")
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE ORDERS SET web_request_id='request-one' WHERE job_id='mobot-2'")


class AgvTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root/'test.sqlite3'
        with patch.dict(os.environ, {'MOBOT_DB':str(self.path),'MOBOT_ADMIN_TOKEN':''}):
            self.app = create_app()
        with closing(connect(self.path)) as db, db:
            self.account = db.execute("INSERT INTO ACCOUNTS(role_id,login_id,password_hash,name) VALUES(2,'test',?,'검증')",(HASH,)).lastrowid
        self.client = self.app.test_client()
        token = self.client.post('/api/auth/login',json={'login_id':'test','password':'agv-test'}).json['token']
        self.owner = token_hash(token)
        self.headers = {'Authorization':'Bearer '+token}
        self.now = 100.
        self.uno = EquipmentDemo(self.path,self.root/'uno',ROOT/'pallet_qrs',clock=lambda:self.now)
        self.addCleanup(self.uno.close)
        self.demo = AgvDemo(self.path,clock=lambda:self.now)
        self.addCleanup(self.demo.close)
        self.app.extensions['agv_demo'] = self.demo
        self.job = self.client.post('/api/orders',json=dict(company='AGV 검증',phone='010-0000-0000',address='경기도 수원시 테스트',head='mono',left='weld',right='fasten',request_id=str(uuid4()))).json['id']

    def prepare(self):
        self.uno.start(self.owner,self.account,dict(job_id=self.job,pallet_id='PALLET-01',position_ready=True,request_id=str(uuid4())))
        for elapsed in (.2,1.6,1.8,7.4,10.4,10.9,12.9,13.2):
            self.now=100+elapsed
            self.uno.heartbeat(self.owner)
            self.uno.step()
        self.uno.confirm(self.owner)

    def start(self,point=5):
        data=dict(job_id=self.job,destination=point,request_id=str(uuid4()))
        result=self.client.post('/api/agv/start',json=data,headers=self.headers)
        self.assertEqual(result.status_code,200,result.json)
        return data,result.json

    def finish(self):
        for _ in range(14):
            self.now += 1
            self.demo.heartbeat(self.owner)
            self.demo.step()

    def test_full_sequence_waits_for_completion_and_retains_mapping(self):
        self.prepare()
        self.start()
        self.finish()
        state=self.demo.snapshot(self.owner)
        self.assertEqual(state['task']['state'],'SUCCESS')
        self.assertEqual((state['device']['position'],state['device']['servo_angle'],state['device']['fork_moving']),(2,85,False))
        self.assertEqual(self.demo.vehicle.commands,delivery_steps(5))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM AGV_TASK_EVENTS').fetchone()[0],14)
            self.assertEqual(db.execute('SELECT status,target_station FROM ORDERS').fetchone()[:],('IN_PROGRESS',5))
            self.assertEqual(db.execute('SELECT is_active FROM JOB_PALLET_MAPPINGS').fetchone()[0],1)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_cannot_dispatch_before_uno_confirm(self):
        response=self.client.post('/api/agv/start',json=dict(job_id=self.job,destination=3,request_id=str(uuid4())),headers=self.headers)
        self.assertEqual(response.status_code,409)

    def test_uuid_retry_and_duplicate_dispatch(self):
        self.prepare()
        data,state=self.start()
        again=self.client.post('/api/agv/start',json=data,headers=self.headers)
        self.assertEqual(again.json['task']['task_id'],state['task']['task_id'])
        self.finish()
        duplicate=self.client.post('/api/agv/start',json={**data,'request_id':str(uuid4())},headers=self.headers)
        self.assertEqual(duplicate.status_code,409)
        self.assertEqual(len(self.demo.vehicle.commands),7)

    def test_heartbeat_failure_stops_without_redispatch(self):
        self.prepare()
        self.start()
        self.demo.step()
        self.now+=5
        self.demo.step()
        self.assertEqual(self.demo.snapshot(self.owner)['task']['state'],'ERROR')
        self.demo.step()
        self.assertEqual(len(self.demo.vehicle.commands),1)

    def test_fault_and_stop_are_terminal(self):
        self.prepare()
        self.start()
        self.demo.vehicle.status['servo_angle']=-1
        self.demo.step()
        self.assertEqual(self.demo.snapshot(self.owner)['task']['state'],'ERROR')
        self.assertEqual(self.demo.vehicle.commands,[])

    def test_error_destination_saved_in_order(self):
        self.prepare()
        self.start(7)
        self.finish()
        self.assertEqual(self.demo.vehicle.commands,delivery_steps(7))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT target_station FROM ORDERS').fetchone()[0],7)

    def test_auth_owner_and_stop(self):
        self.assertEqual(self.client.get('/api/agv/state').status_code,401)
        self.prepare()
        self.start()
        with self.assertRaises(ValueError):self.demo.heartbeat('other-owner')
        response=self.client.post('/api/agv/stop',json={},headers=self.headers)
        self.assertEqual(response.json['task']['state'],'STOPPED')

    def test_concurrent_dispatch_one_task(self):
        self.prepare()
        def send(_):
            with self.app.test_client() as client:
                return client.post('/api/agv/start',json=dict(job_id=self.job,destination=3,request_id=str(uuid4())),headers=self.headers).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(send,range(2))),[200,409])

    def test_restart_interrupts_without_replay(self):
        self.prepare()
        self.start()
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE EQUIPMENT_DEVICE_LEASES SET heartbeat_at=0 WHERE device_id='AGV-DEMO'")
        restarted=AgvDemo(self.path,clock=lambda:self.now)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.snapshot(self.owner)['task']['state'],'INTERRUPTED')
        restarted.step()
        self.assertEqual(restarted.vehicle.commands,[])

    def test_second_process_rejected(self):
        with self.assertRaises(ValueError):AgvDemo(self.path)

    def test_pending_movement_is_not_reissued(self):
        self.prepare()
        self.start()
        self.demo.step()
        for _ in range(3):
            self.now += .1
            self.demo.step()
        self.assertEqual(self.demo.vehicle.commands,[('go',1)])
        self.assertEqual(self.demo.snapshot(self.owner)['task']['step_index'],0)

    def test_lost_lease_interrupts_task(self):
        self.prepare()
        self.start()
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE EQUIPMENT_DEVICE_LEASES SET instance_id='other' WHERE device_id='AGV-DEMO'")
        self.demo.step()
        state=self.demo.snapshot(self.owner)
        self.assertFalse(state['can_start'])
        self.assertEqual(state['task']['state'],'INTERRUPTED')


if __name__=='__main__':unittest.main()
