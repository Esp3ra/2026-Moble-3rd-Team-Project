import json
import os
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock,patch

import cv2
import numpy as np
import test_factory_v1 as fixtures
from app import create_app
from orders import connect
from factory_v1.common import Fault,uid,now
from factory_v1.devices import SimDevice,HttpDevice
from scripts.prepare_transport_test import prepare


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.FactoryTests('test_stock_shared_and_public_privacy');self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.source=self.case.path;self.case.engine.close()
        settings=dict(devices={d:dict(base_url='https://'+d.lower()+'.internal',controller_token='test') for d in ('LINE','ARM','AGV')})
        (self.source.parent/'factory-v1.json').write_text(json.dumps(settings))
        self.destination=self.source.parent/'transport-test/mobot.sqlite3';prepare(self.source,self.destination)
        with patch.dict(os.environ,{'MOBOT_DB':str(self.destination),'MOBOT_V1_MODE':'TRANSPORT_TEST','MOBOT_V1_BACKGROUND':'0'}):app=create_app()
        engine=app.extensions['factory_v1'];self.addCleanup(engine.close)
        self.assertEqual(set(engine.clients),{'LINE','AGV'});self.assertTrue(all(isinstance(c,HttpDevice) for c in engine.clients.values()))
        self.assertFalse(engine.vision.simulation)
        self.case.app=app;self.case.path=self.destination;self.case.engine=engine;self.case.client=app.test_client()
        engine.clock=lambda:self.case.now
        engine.clients={d:SimDevice(d,lambda:self.case.now) for d in ('LINE','AGV')}
        qr=cv2.QRCodeEncoder_create().encode('PALLET-01')
        self.qr=cv2.cvtColor(cv2.resize(cv2.copyMakeBorder(qr,4,4,4,4,cv2.BORDER_CONSTANT,value=255),None,fx=12,fy=12,interpolation=cv2.INTER_NEAREST),cv2.COLOR_GRAY2BGR)
        self.available={'cam1':True,'cam4':True}
        engine.vision.frame_data=lambda cam:(self.qr,uid(),now()) if self.available.get(cam) else (None,None,None)
        engine.vision.locate_assembly=Mock(side_effect=AssertionError('Must not locate in transport test'))
        engine.vision.inspect_product=Mock(side_effect=AssertionError('Must not inspect in transport test'))
        self.engine,self.client=engine,self.case.client
        token=self.client.post('/api/v1/auth/login',json={'login_id':'admin','password':'test'}).json['token'];self.case.auth={'Authorization':'Bearer '+token}
        self.case.tick()

    def test_full_transport_preserves_qr_actual_gates_without_quality_pass(self):
        order,_=self.case.order();self.case.accept(order);self.case.start();self.case.tick(90)
        with closing(connect(self.destination)) as db:
            row=db.execute('SELECT * FROM V1_ORDERS').fetchone();self.assertEqual(row['factory_status'],'COMPLETED');self.assertEqual(row['outcome'],'OPEN')
            self.assertEqual(row['pallet_id'],'PALLET-01');self.assertEqual(row['dispatch_station'],row['target_station'])
            self.assertEqual(db.execute('SELECT count(*) FROM V1_INSPECTIONS').fetchone()[0],0)
            actions=[r[0] for r in db.execute('SELECT action FROM V1_COMMANDS ORDER BY rowid')]
            self.assertEqual(actions,['DISPENSE','MOVE_TO_ASSEMBLY','MOVE_TO_PICKUP','PICK_UP','GO_TO_INSPECTION','DELIVER','UNLOAD','RETURN_STANDBY'])
            self.assertEqual([r[0] for r in db.execute('SELECT DISTINCT action FROM V1_VISION_JOBS')],['QR'])
            kinds=[r[0] for r in db.execute('SELECT kind FROM V1_HISTORY')]
            for kind in ('QR_BOUND','ASSEMBLY_STATION_STOPPED','ASSEMBLY_SKIPPED','INSPECTION_ARRIVED','INSPECTION_SKIPPED','STANDBY_RETURNED','TRANSPORT_TEST_COMPLETED'):self.assertIn(kind,kinds)
            self.assertNotIn('INSPECTION_PASS',kinds)
            self.assertEqual(db.execute("SELECT stage FROM V1_PALLETS WHERE pallet_id='PALLET-01'").fetchone()[0],'AWAIT_MANUAL_RETURN')
        self.engine.vision.locate_assembly.assert_not_called();self.engine.vision.inspect_product.assert_not_called()
        with closing(connect(self.source)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM V1_ORDERS').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT available_units FROM V1_STOCK WHERE stock_key='BODY'").fetchone()[0],8)
        result=self.client.post('/api/v1/orders/lookup',json={'order_id':order['order_id']});self.assertEqual(result.json['outcome'],'OPEN');self.assertEqual(result.headers['X-Mobot-Environment'],'TRANSPORT_TEST')

    def test_only_missing_arm_and_cam2_cam3_are_ignored(self):
        self.assertEqual(self.client.get('/api/v1/plant',headers=self.case.auth).json['state'],'READY')
        for camera in ('cam1','cam4'):
            self.available[camera]=False;self.assertEqual(self.client.get('/api/v1/plant',headers=self.case.auth).json['state'],'NOT_READY');self.available[camera]=True
        self.engine.clients.pop('AGV')
        with self.case.app.app_context(),closing(connect(self.destination)) as db,db:
            status=json.loads(db.execute("SELECT status FROM V1_DEVICES WHERE device_id='AGV'").fetchone()[0]);status['online']=False;db.execute("UPDATE V1_DEVICES SET status=? WHERE device_id='AGV'",(json.dumps(status),))
        self.assertEqual(self.client.get('/api/v1/plant',headers=self.case.auth).json['state'],'NOT_READY')

    def test_pause_resume_confirms_both_real_participants(self):
        order,_=self.case.order();self.case.accept(order);self.case.start();self.case.tick()
        pause=self.client.post('/api/v1/plant/actions',headers=self.case.auth,json={'request_id':uid(),'action':'PAUSE'});self.assertEqual(pause.status_code,202)
        self.case.tick(5);self.assertEqual(self.engine.plant()['state'],'PAUSED')
        with closing(connect(self.destination)) as db:self.assertEqual(set(r[0] for r in db.execute("SELECT device_id FROM V1_COMMANDS WHERE action='PAUSE'")),{'LINE','AGV'})
        resume=self.client.post('/api/v1/plant/actions',headers=self.case.auth,json={'request_id':uid(),'action':'RESUME'});self.assertEqual(resume.status_code,202);self.case.tick(90)
        self.assertEqual(self.client.get('/api/v1/orders/'+order['order_id'],headers=self.case.auth).json['factory_status'],'COMPLETED')

    def test_repeated_prepare_keeps_history_and_scope_cannot_cross_modes(self):
        order,_=self.case.order();prepare(self.source,self.destination)
        with closing(connect(self.destination)) as db:self.assertEqual(db.execute('SELECT count(*) FROM V1_ORDERS').fetchone()[0],1)
        for path,mode in ((self.source,'TRANSPORT_TEST'),(self.destination,'HARDWARE')):
            with patch.dict(os.environ,{'MOBOT_DB':str(path),'MOBOT_V1_MODE':mode,'MOBOT_V1_BACKGROUND':'0'}),self.assertRaises(Fault):create_app()
        with self.assertRaises(ValueError):prepare(self.source,self.source)
        page=self.client.get('/order').data.decode();self.assertIn('운송 시험 전용',page)

    def test_qr_failure_and_undelivered_motion_do_not_get_skipped(self):
        self.engine.vision.frame_data=lambda camera:(np.full((200,200,3),255,np.uint8),uid(),now()) if camera in ('cam1','cam4') else (None,None,None)
        order,_=self.case.order();self.case.accept(order);self.case.start();self.case.tick(50)
        plant=self.engine.plant();self.assertEqual(plant['stage'],'WAIT_FEED_QR');self.assertEqual(plant['feed_qr']['state'],'UNREADABLE')
        with closing(connect(self.destination)) as db:self.assertEqual([r[0] for r in db.execute('SELECT action FROM V1_COMMANDS')],['DISPENSE'])


if __name__=='__main__':unittest.main()
