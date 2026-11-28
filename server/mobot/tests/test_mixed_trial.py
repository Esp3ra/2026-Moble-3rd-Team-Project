"""Isolated regression tests, inert devices only; no physical I/O."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
from werkzeug.security import generate_password_hash
from app import create_app
from orders import connect
from factory_v1.common import Fault,uid
from factory_v1.devices import HttpDevice,SimDevice
from factory_v1.mixed_trial import PROFILE,enabled

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'AGV'),str(ROOT/'AGV/tests')]
from device_adapter import Adapter,serve
from test_device_adapter import Device


class MixedTrialTests(unittest.TestCase):
    def test_explicit_flag_and_separate_scope_are_required_before_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trial.sqlite3'
            settings={'mixed_trial':{'profile':PROFILE},'devices':{'LINE':{},'AGV':{}}}
            with patch.dict(os.environ,{'MOBOT_MIXED_TRIAL':'0'}),self.assertRaises(Fault):
                enabled(settings,'HARDWARE',path,path.with_name('production.sqlite3'))
            with patch.dict(os.environ,{'MOBOT_MIXED_TRIAL':'1','MOBOT_DB':str(path)}):
                with self.assertRaises(Fault):enabled(settings,'HARDWARE',path,path)
                with self.assertRaises(Fault):enabled(settings,'SIMULATION',path,path.with_name('production.sqlite3'))
                self.assertTrue(enabled(settings,'HARDWARE',path,path.with_name('production.sqlite3')))
            path.parent.joinpath('factory-v1.json').write_text(json.dumps(settings),encoding='utf-8')
            with patch.dict(os.environ,{'MOBOT_DB':str(path),'MOBOT_MIXED_TRIAL':'1','MOBOT_V1_MODE':'HARDWARE','MOBOT_V1_BACKGROUND':'0'}):
                app=create_app();app.extensions['factory_v1'].close()
            with closing(connect(path)) as db:
                self.assertEqual(db.execute("SELECT value FROM V1_META WHERE key='database_scope'").fetchone()[0],'MIXED_TRIAL')
            with patch.dict(os.environ,{'MOBOT_DB':str(path),'MOBOT_MIXED_TRIAL':'0','MOBOT_V1_MODE':'HARDWARE'}):
                # A trial DB cannot silently become a production DB, even if the flag/config are removed.
                path.parent.joinpath('factory-v1.json').write_text('{}',encoding='utf-8')
                with self.assertRaises(Fault):create_app()

    def test_real_qr_is_required_and_reads_are_not_auto_assigned(self):
        with self.fixture() as (app,engine,path,adapter,device,tick):
            client=app.test_client()
            self.assertEqual(client.get('/api/v1/catalog').headers['X-Mobot-Trial'],PROFILE)
            engine.vision.real.frames.clear()
            with app.app_context(),closing(connect(path)) as db:
                self.assertFalse(engine.ready(db))
            with patch.object(engine.vision.real,'read_feed_qr',return_value={'pallet_id':None}) as read:
                self.assertIsNone(engine.vision.read_feed_qr('cycle','read',1)['pallet_id'])
                read.assert_called_once()
            self.assertNotIsInstance(engine.clients['AGV'],SimDevice)
            self.assertIsInstance(engine.clients['ARM'],SimDevice)

    from contextlib import contextmanager
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trial.sqlite3'
            settings={'mode':'HARDWARE','mixed_trial':{'profile':PROFILE},'devices':{
                k:{'base_url':'http://127.0.0.1:9','controller_token':'fake-'+k} for k in ('LINE','AGV')}}
            path.with_name('factory-v1.json').write_text(json.dumps(settings),encoding='utf-8')
            with patch.dict(os.environ,{'MOBOT_DB':str(path),'MOBOT_MIXED_TRIAL':'1','MOBOT_V1_MODE':'HARDWARE','MOBOT_V1_BACKGROUND':'0'}):app=create_app()
            engine=app.extensions['factory_v1'];clock=[100.]
            engine.clock=lambda:clock[0]
            engine.clients['LINE']=SimDevice('LINE',clock=lambda:clock[0])
            engine.clients['ARM'].clock=lambda:clock[0]
            device=Device();adapter=Adapter(device,str(Path(directory)/'adapter.sqlite3'),90,clock=lambda:clock[0]);adapter.step()
            http=serve(adapter,'test-controller-'+32*'x',('127.0.0.1',0))
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            engine.clients['AGV']=HttpDevice('AGV',{'base_url':'http://127.0.0.1:'+str(http.server_port),'controller_token':'test-controller-'+32*'x','request_timeout_seconds':2})
            def tick():
                clock[0]+=.25
                engine.vision.real.frames['cam1']=(time.monotonic(),np.zeros((32,32,3),np.uint8),uid(),'2026-11-28T00:00:00.000Z')
                engine.tick()
                adapter.step()
                if device.last is not None:device.finish()
            try:yield app,engine,path,adapter,device,tick
            finally:engine.close();adapter.close();http.shutdown();http.server_close();thread.join()

    def test_three_destinations_preserve_ir2_gate_and_saved_inspection(self):
        for destination,address in ((3,'서울특별시 중구'),(4,'대전광역시 유성구'),(5,'충청남도 천안시')):
            with self.subTest(destination=destination),self.fixture() as (app,engine,path,adapter,device,tick):
                client=app.test_client()
                with closing(connect(path)) as db,db:
                    db.execute('INSERT INTO ACCOUNTS(role_id,login_id,name,password_hash) VALUES(2,?,?,?)',('admin','시험',generate_password_hash('test-only')))
                auth={'Authorization':'Bearer '+client.post('/api/v1/auth/login',json={'login_id':'admin','password':'test-only'}).json['token']}
                # Only QR decoding is stubbed here. The runtime delegates to the actual CAM1 decoder.
                engine.vision.real.read_feed_qr=lambda cycle,read,attempt,sample=None:dict(frame_id=uid(),pallet_id='PALLET-01',attempt=attempt,read_id=read)
                tick()
                order=client.post('/api/v1/orders',json=dict(request_id=uid(),company='시험',phone='010-0000-0000',address=address,
                    spec=dict(target_head_id='H1',target_arm_l_id='L1',target_arm_r_id='R2'),payment_method='card')).json
                self.assertEqual(order['target_station'],destination)
                self.assertEqual(client.post('/api/v1/orders/'+order['order_id']+'/accept',headers=auth,json={'request_id':uid()}).status_code,200)
                self.assertEqual(client.post('/api/v1/plant/actions',headers=auth,json={'request_id':uid(),'action':'START'}).status_code,202)
                saw_ir2_gate=False
                for _ in range(160):
                    tick()
                    with closing(connect(path)) as db:
                        cycle=db.execute('SELECT * FROM V1_CYCLES ORDER BY rowid DESC LIMIT 1').fetchone()
                        if cycle and cycle['stage']=='TO_PICKUP':
                            saw_ir2_gate=True
                            self.assertIsNone(db.execute("SELECT 1 FROM V1_COMMANDS WHERE action='PICK_UP'").fetchone())
                        if cycle and cycle['closed']:break
                self.assertTrue(saw_ir2_gate)
                with closing(connect(path)) as db:
                    result=db.execute('SELECT * FROM V1_ORDERS WHERE order_id=?',(order['order_id'],)).fetchone()
                    self.assertEqual(result['factory_status'],'COMPLETED')
                    self.assertEqual(result['dispatch_station'],destination)
                    rows=db.execute("SELECT action,state,params FROM V1_COMMANDS WHERE device_id='AGV' ORDER BY rowid").fetchall()
                    self.assertEqual([(r['action'],r['state']) for r in rows],[(a,'COMPLETED') for a in ('PICK_UP','DELIVER','UNLOAD','RETURN_STANDBY')])
                    inspection=json.loads(db.execute('SELECT body FROM V1_INSPECTIONS').fetchone()[0])
                    evidence=json.loads(db.execute("SELECT body FROM V1_EVENTS WHERE json_extract(body,'$.kind')='INSPECTION_CAPTURED'").fetchone()[0])
                    self.assertEqual(evidence['data']['model_version'],'MIXED_TRIAL_SIMULATED')
                    self.assertEqual(json.loads(rows[1]['params'])['inspection_id'],inspection['inspection_id'])
                    self.assertEqual(db.execute('SELECT auto_enabled FROM V1_PLANT').fetchone()[0],0)
                    self.assertIsNotNone(db.execute("SELECT 1 FROM V1_HISTORY WHERE kind='MIXED_TRIAL'").fetchone())
                self.assertEqual((device.raw['position'],device.raw['servo_angle']),(2,85))


if __name__=='__main__':unittest.main()
