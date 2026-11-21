import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from werkzeug.security import generate_password_hash

from app import ROOT, create_app
from auth import token_hash
from manage_devices import provision
from orders import connect, initialize_orders, SCHEMA_PATH
from pallet_system_uno_sim.app import demo_step
from pallet_system_uno_sim.controller import Bench
from raspi_agent.agent import Agent
from remote_equipment import RemoteEquipment

HASH=generate_password_hash('test-only')


class RemoteTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.database=self.root/'test.sqlite3'
        with patch.dict(os.environ,{'MOBOT_DB':str(self.database),'MOBOT_ADMIN_TOKEN':''}):self.app=create_app()
        self.app.config.update(LEGACY_API_ENABLED=True,V1_BACKGROUND=False)
        config=self.root/'pi.json'
        provision(self.database,'uno-pi','DEMO','https://example.invalid',config)
        self.config=json.loads(config.read_text())
        self.now=100.
        self.remote=RemoteEquipment(self.database,'uno-pi',clock=lambda:self.now)
        self.addCleanup(self.remote.close)
        self.app.extensions['remote_equipment']=self.remote
        self.app.config['EQUIPMENT_MODE']='REMOTE'
        with closing(connect(self.database)) as db, db:
            db.execute("INSERT INTO ACCOUNTS(role_id,login_id,password_hash,name) VALUES(2,'test',?,'검증')",(HASH,))
        self.client=self.app.test_client()
        token=self.client.post('/api/auth/login',json={'login_id':'test','password':'test-only'}).json['token']
        self.headers={'Authorization':'Bearer '+token}
        self.owner=token_hash(token)
        self.agent=Agent(self.config,self.root/'agent')
        self.agent.bench=Bench(self.root/'runs',demo=True,clock=lambda:self.now)
        self.addCleanup(self.agent.bench.shutdown)
        self.agent.bench.connect('DEMO')
        self.blank=np.full((480,640,3),238,np.uint8)
        for _ in range(3):self.agent.bench.frame([],self.blank,self.now)
        self.seq=0
        self.exchange()
        self.job=self.client.post('/api/orders',json=dict(company='원격 검증',phone='010-0000-0000',address='서울특별시',head='mono',left='weld',right='fasten',request_id=str(uuid4()))).json['id']

    def body(self):
        self.seq+=1
        return dict(boot_id=self.agent.boot,sequence=self.seq,run_id=self.agent.run_id,status=self.agent.bench.snapshot(),results=self.agent.results)

    def exchange(self, execute=True):
        body=self.body()
        reply=self.remote.poll(body)
        self.agent.results=[]
        if reply['command'] and execute:
            self.agent.results=[self.agent.execute(reply['command'],reply['server_time'],0)]
        return reply

    def start(self):
        payload=dict(job_id=self.job,pallet_id='PALLET-01',position_ready=True,request_id=str(uuid4()))
        response=self.client.post('/api/equipment/start',json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        return payload,response.json

    def advance(self, elapsed):
        self.now=100+elapsed
        self.remote.heartbeat(self.owner)
        self.agent.bench.heartbeat(self.agent.run_id)
        demo_step(self.agent.bench)
        self.agent.bench.frame(['PALLET-01'],self.blank,self.now)
        self.agent.bench.tick()
        self.exchange()

    def test_full_remote_qr_mapping_and_confirm(self):
        self.start()
        self.exchange()
        self.assertEqual(self.agent.bench.state,'STARTING')
        for elapsed in (.2,1.6,1.8,7.4,10.4,10.9,12.9,13.2):self.advance(elapsed)
        state=self.remote.snapshot(self.owner)
        self.assertEqual(state['run']['database_status'],'mapped')
        self.assertTrue(state['can_confirm'])
        response=self.client.post('/api/equipment/confirm-ejection',json={},headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        self.exchange()
        self.exchange()
        self.assertEqual(self.remote.snapshot(self.owner)['run']['state'],'SUCCESS')
        self.assertEqual(self.client.post('/api/equipment/heartbeat',json={},headers=self.headers).status_code,200)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT status FROM ORDERS').fetchone()[0],'IN_PROGRESS')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_lost_poll_response_does_not_redeliver_start(self):
        self.start()
        reply=self.exchange(execute=False)
        self.assertEqual(reply['command']['action'],'start')
        self.assertIsNone(self.exchange()['command'])
        self.now+=3.1
        self.remote.heartbeat(self.owner)
        self.exchange()
        self.assertEqual(self.remote.snapshot(self.owner)['run']['state'],'ERROR')
        self.assertIsNone(self.agent.bench.trial)

    def test_agent_journal_prevents_second_motion(self):
        self.start()
        reply=self.exchange(execute=False)
        command=reply['command']
        self.assertTrue(self.agent.execute(command,100,0)['ok'])
        trial=self.agent.bench.trial['id']
        self.assertTrue(self.agent.execute(command,100,0)['ok'])
        self.assertEqual(self.agent.bench.trial['id'],trial)
        restarted=Agent(self.config,self.root/'agent')
        self.assertTrue(restarted.execute(command,100,0)['ok'])
        self.assertIsNone(restarted.bench.trial)

    def test_device_auth_and_mode(self):
        data=self.body()
        self.assertEqual(self.client.post('/api/devices/uno-pi/poll',json=data).status_code,401)
        auth={'Authorization':'Bearer '+self.config['device_token']}
        self.assertEqual(self.client.post('/api/devices/uno-pi/poll',json=data,headers=auth).status_code,200)
        data['sequence']+=1
        data['status']['mode']='LIVE'
        self.assertEqual(self.client.post('/api/devices/uno-pi/poll',json=data,headers=auth).status_code,409)

    def test_serial_only_report_reaches_wpf_without_allowing_start(self):
        from raspi_agent.serial_probe import report
        with closing(connect(self.database)) as db, db:
            db.execute("UPDATE REMOTE_DEVICES SET hardware_mode='LIVE' WHERE device_id='uno-pi'")
        body=self.body()
        body['status']=report([dict(at='2026-11-21T00:00:00Z',event='UNO',text='PI_TEST:001')])
        auth={'Authorization':'Bearer '+self.config['device_token']}
        response=self.client.post('/api/devices/uno-pi/poll',json=body,headers=auth)
        self.assertEqual(response.status_code,200,response.json)
        state=self.client.get('/api/equipment/state',headers=self.headers).json
        self.assertEqual(state['events'][-1]['text'],'PI_TEST:001')
        self.assertFalse(state['can_start'])
        self.assertIsNone(state['run'])

    def test_usb_free_wireless_messages_reach_admin_api(self):
        from raspi_agent.wireless_test import make_report
        body=make_report({'demo':True},self.agent.boot,self.seq+1,
                         [dict(at='2026-11-21T00:00:00Z',event='UNO TEST',text='IR:CLEAR')],[])
        auth={'Authorization':'Bearer '+self.config['device_token']}
        response=self.client.post('/api/devices/uno-pi/poll',json=body,headers=auth)
        self.assertEqual(response.status_code,200,response.json)
        state=self.client.get('/api/equipment/state',headers=self.headers).json
        self.assertEqual(state['events'][-1]['event'],'UNO TEST')
        self.assertEqual(state['events'][-1]['text'],'IR:CLEAR')
        self.assertFalse(state['can_start'])
        self.assertFalse(state['connected'])
        self.assertIsNone(state['run'])

    def test_owner_heartbeat_loss_revokes_permission(self):
        self.start()
        self.exchange()
        self.now+=4.1
        reply=self.exchange()
        self.assertFalse(reply['allow_run'])
        self.assertEqual(self.remote.snapshot(self.owner)['run']['state'],'ERROR')

    def test_sequence_replay_does_not_change_state(self):
        body=self.body()
        self.remote.poll(body)
        body['status']['state']='ERROR'
        reply=self.remote.poll(body)
        self.assertIsNone(reply['command'])
        self.assertNotEqual(self.remote.snapshot(self.owner)['state'],'ERROR')

    def test_start_uuid_retry_and_second_agent_blocked(self):
        payload,first=self.start()
        second=self.client.post('/api/equipment/start',json=payload,headers=self.headers)
        self.assertEqual(first['run']['run_id'],second.json['run']['run_id'])
        body=self.body()
        body['boot_id']=str(uuid4())
        with self.assertRaises(ValueError):self.remote.poll(body)

    def test_expired_command_never_touches_hardware(self):
        self.start()
        command=self.exchange(execute=False)['command']
        result=self.agent.execute(command,100,4)
        self.assertFalse(result['ok'])
        self.assertIsNone(self.agent.bench.trial)

    def test_local_watchdog_stops_without_network_response(self):
        self.start()
        self.exchange()
        self.agent.last_contact=-100
        self.agent.check_link()
        self.assertEqual(self.agent.bench.state,'ERROR')
        self.agent.check_link()
        self.assertEqual(self.agent.bench.state,'ERROR')

    def test_frame_upload_requires_device_boot_and_admin_read(self):
        import cv2
        encoded=cv2.imencode('.jpg',self.blank)[1].tobytes()
        auth={'Authorization':'Bearer '+self.config['device_token'],'Content-Type':'image/jpeg','X-Device-Boot':self.agent.boot}
        with patch('remote_equipment.time.time',return_value=self.now):
            result=self.client.post('/api/devices/uno-pi/frame',data=encoded,headers=auth)
        self.assertEqual(result.status_code,204)
        self.assertEqual(self.client.get('/api/equipment/frame.jpg',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/equipment/frame.jpg').status_code,401)
        self.now+=4
        self.assertEqual(self.client.get('/api/equipment/frame.jpg',headers=self.headers).status_code,503)

    def test_remote_schema_preserves_referenced_run(self):
        self.start()
        self.remote.close()
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT mode FROM EQUIPMENT_RUNS").fetchone()[0],'REMOTE')
        # Reproduce the old CHECK and exercise the rebuild with FK children.
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'old.sqlite3'
            schema=SCHEMA_PATH.read_text(encoding='utf-8-sig').replace("CHECK (mode IN ('DEMO', 'REMOTE'))","CHECK (mode = 'DEMO')")
            with closing(connect(path)) as db, db:
                db.executescript(schema)
                db.execute("INSERT INTO ORDERS(job_id,model_name,target_head_id,target_arm_l_id,target_arm_r_id) VALUES('mobot-1','A1a','head-A','arm-a','arm-1')")
                mapping=db.execute("INSERT INTO JOB_PALLET_MAPPINGS(job_id,pallet_id) VALUES('mobot-1','PALLET-01')").lastrowid
                db.execute("INSERT INTO EQUIPMENT_RUNS(run_id,request_id,job_id,expected_pallet_id,mapping_id,owner_hash,state,finished_at) VALUES('old-run','old-request','mobot-1','PALLET-01',?,'hash','SUCCESS',CURRENT_TIMESTAMP)",(mapping,))
                db.execute("INSERT INTO AGV_TASKS(task_id,request_id,equipment_run_id,job_id,pallet_id,mapping_id,destination,owner_hash,state) VALUES('task','request','old-run','mobot-1','PALLET-01',?,3,'hash','SUCCESS')",(mapping,))
            initialize_orders(path)
            initialize_orders(path)
            with closing(connect(path)) as db:
                self.assertIn("'REMOTE'",db.execute("SELECT sql FROM sqlite_master WHERE name='EQUIPMENT_RUNS'").fetchone()[0])
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
                self.assertEqual(db.execute('SELECT equipment_run_id FROM AGV_TASKS').fetchone()[0],'old-run')


if __name__=='__main__':unittest.main()
