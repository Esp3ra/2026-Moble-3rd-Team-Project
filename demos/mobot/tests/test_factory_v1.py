import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from app import create_app
from orders import connect
from factory_v1.common import uid,dump,now
from factory_v1.devices import SimDevice

HASH=generate_password_hash('test')


class FactoryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'test.sqlite3'
        with patch.dict(os.environ,{'MOBOT_DB':str(self.path),'MOBOT_V1_MODE':'SIMULATION'}):self.app=create_app()
        self.app.config.update(V1_BACKGROUND=False,V1_DEVICE_TOKENS={'LINE':'line-test','ARM':'arm-test','AGV':'agv-test','VISION':'vision-test'},V1_CONTROLLER_TOKEN='control-test')
        self.engine=self.app.extensions['factory_v1'];self.addCleanup(self.engine.close)
        self.now=100.
        self.engine.clock=lambda:self.now
        self.engine.clients={device:SimDevice(device,clock=lambda:self.now) for device in ('LINE','ARM','AGV')}
        with closing(connect(self.path)) as db,db:
            db.executemany('INSERT INTO ACCOUNTS(role_id,login_id,name,password_hash) VALUES(?,?,?,?)',[(1,'super','최상위',HASH),(2,'admin','관리자',HASH)])
        self.client=self.app.test_client()
        token=self.client.post('/api/v1/auth/login',json={'login_id':'admin','password':'test'}).json['token']
        super_token=self.client.post('/api/v1/auth/login',json={'login_id':'super','password':'test'}).json['token']
        self.auth={'Authorization':'Bearer '+token};self.super_auth={'Authorization':'Bearer '+super_token}
        self.tick()

    def tick(self,n=1):
        for _ in range(n):self.now+=.25;self.engine.tick()

    def order(self,address='경기도 광주시',head='H1',left='L1',right='R1',request=None):
        data=dict(request_id=request or uid(),company='시험',phone='010-0000-0000',address=address,spec=dict(target_head_id=head,target_arm_l_id=left,target_arm_r_id=right),payment_method='card')
        response=self.client.post('/api/v1/orders',json=data)
        self.assertEqual(response.status_code,201,response.json)
        return response.json,data

    def accept(self,order):
        result=self.client.post('/api/v1/orders/'+order['order_id']+'/accept',json={'request_id':uid()},headers=self.auth)
        self.assertEqual(result.status_code,200,result.json)
        return result.json

    def start(self):
        result=self.client.post('/api/v1/plant/actions',json={'request_id':uid(),'action':'START'},headers=self.auth)
        self.assertEqual(result.status_code,202,result.json)
        return result.json

    def test_stock_shared_and_public_privacy(self):
        order,_=self.order()
        self.assertEqual(set(order),{'order_id','spec','target_station','progress','outcome','updated_at'})
        self.assertEqual(order['target_station'],3)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT available_units FROM V1_STOCK WHERE stock_key='ARM_1'").fetchone()[0],6)
        result=self.client.post('/api/v1/orders/lookup',json={'order_id':order['order_id']})
        self.assertNotIn('company',result.json)

    def test_address_regions_and_ambiguous_rejection(self):
        for address,hub in [('광주광역시 서구',5),('부산광역시',4),('경기도 수원시',3),('제주특별자치도',3)]:
            result,_=self.order(address,left='L2',right='R3')
            self.assertEqual(result['target_station'],hub)
        _,data=self.order('서울특별시',head='H2',left='L3',right='R2')
        data.update(request_id=uid(),address='알 수 없는 지역')
        self.assertEqual(self.client.post('/api/v1/orders',json=data).status_code,422)

    def test_idempotency_cancel_and_atomic_stock_failure(self):
        order,data=self.order()
        again=self.client.post('/api/v1/orders',json=data)
        self.assertEqual(again.status_code,200)
        self.assertEqual(again.json['order_id'],order['order_id'])
        changed={**data,'company':'다른 회사'}
        self.assertEqual(self.client.post('/api/v1/orders',json=changed).status_code,409)
        cancel=dict(order_id=order['order_id'],request_id=uid())
        for _ in range(2):self.assertEqual(self.client.post('/api/v1/orders/cancel',json=cancel).status_code,200)
        with closing(connect(self.path)) as db,db:
            self.assertEqual(db.execute("SELECT available_units FROM V1_STOCK WHERE stock_key='ARM_1'").fetchone()[0],8)
            db.execute("UPDATE V1_STOCK SET available_units=1 WHERE stock_key='ARM_1'")
        data['request_id']=uid()
        self.assertEqual(self.client.post('/api/v1/orders',json=data).status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute("SELECT available_units FROM V1_STOCK WHERE stock_key='BODY'").fetchone()[0],8)

    def test_full_cycle_has_qr_arm_inspection_unload_and_final_return(self):
        order,_=self.order();self.accept(order);self.start()
        self.tick(80)
        result=self.client.get('/api/v1/orders/'+order['order_id'],headers=self.auth).json
        self.assertEqual(result['factory_status'],'COMPLETED',result)
        self.assertEqual(result['dispatch_station'],3)
        with closing(connect(self.path)) as db:
            actions=[r[0] for r in db.execute('SELECT action FROM V1_COMMANDS ORDER BY rowid')]
            self.assertEqual(actions,['DISPENSE','MOVE_TO_ASSEMBLY','ASSEMBLE','MOVE_TO_PICKUP','PICK_UP','GO_TO_INSPECTION','DELIVER','UNLOAD','RETURN_STANDBY'])
            self.assertEqual(db.execute('SELECT count(*) FROM V1_INSPECTIONS').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT stage FROM V1_PALLETS WHERE pallet_id=?",(result['pallet_id'],)).fetchone()[0],'AWAIT_MANUAL_RETURN')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_acceptance_order_and_no_next_pallet_before_return(self):
        a,_=self.order(left='L2',right='R3');b,_=self.order(head='H2',left='L3',right='R2')
        self.accept(b);self.accept(a);self.start();self.tick(4)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT order_id FROM V1_CYCLES').fetchone()[0],b['order_id'])
            self.assertEqual(db.execute("SELECT count(*) FROM V1_COMMANDS WHERE action='DISPENSE'").fetchone()[0],1)
        self.tick(100)
        with closing(connect(self.path)) as db:self.assertEqual([r[0] for r in db.execute('SELECT order_id FROM V1_CYCLES ORDER BY rowid')],[b['order_id'],a['order_id']])

    def test_three_qr_failures_then_same_pallet_retry(self):
        self.engine.vision.qr_inputs=[None,None,None,'PALLET-03']
        order,_=self.order();self.accept(order);self.start();self.tick(30)
        plant=self.client.get('/api/v1/plant',headers=self.auth).json
        self.assertEqual(plant['stage'],'WAIT_FEED_QR')
        self.assertEqual(plant['feed_qr']['state'],'UNREADABLE')
        with closing(connect(self.path)) as db:self.assertEqual([r[0] for r in db.execute('SELECT action FROM V1_COMMANDS')],['DISPENSE'])
        retry=self.client.post('/api/v1/cycles/'+plant['cycle_id']+'/qr-retry',json=dict(request_id=uid(),action='READ_QR',previous_read_id=plant['feed_qr']['read_id']),headers=self.auth)
        self.assertEqual(retry.status_code,202,retry.json)
        self.tick(80)
        result=self.client.get('/api/v1/orders/'+order['order_id'],headers=self.auth).json
        self.assertEqual(result['pallet_id'],'PALLET-03')
        with closing(connect(self.path)) as db:self.assertEqual(db.execute("SELECT count(*) FROM V1_COMMANDS WHERE action='DISPENSE'").fetchone()[0],1)

    def test_pause_requires_all_three_and_resume_continues_without_new_dispense(self):
        order,_=self.order();self.accept(order);self.start();self.tick()
        pause=self.client.post('/api/v1/plant/actions',json={'request_id':uid(),'action':'PAUSE'},headers=self.auth)
        self.assertEqual(pause.status_code,202)
        self.tick(5)
        self.assertEqual(self.client.get('/api/v1/plant',headers=self.auth).json['state'],'PAUSED')
        resume=self.client.post('/api/v1/plant/actions',json={'request_id':uid(),'action':'RESUME'},headers=self.auth)
        self.assertEqual(resume.status_code,202,resume.json)
        self.tick(100)
        self.assertEqual(self.client.get('/api/v1/orders/'+order['order_id'],headers=self.auth).json['factory_status'],'COMPLETED')
        with closing(connect(self.path)) as db:self.assertEqual(db.execute("SELECT count(*) FROM V1_COMMANDS WHERE action='DISPENSE'").fetchone()[0],1)

    def test_missing_part_has_priority_over_qr_mismatch(self):
        def alter(data):
            data['left']=dict(observation='MISSING',observed_id=None,confidence=None)
            data['qr_read']='PALLET-08'
        self.engine.vision.inspection_override=alter
        order,_=self.order();self.accept(order);self.start();self.tick(90)
        result=self.client.get('/api/v1/orders/'+order['order_id'],headers=self.auth).json
        self.assertEqual((result['factory_status'],result['dispatch_station']),('ERROR_DELIVERED',6))

    def test_roles_session_and_external_control_denied(self):
        self.assertEqual(self.client.get('/api/v1/accounts',headers=self.auth).status_code,403)
        self.assertEqual(self.client.get('/api/v1/accounts',headers=self.super_auth).status_code,200)
        self.assertEqual(self.client.get('/api/v1/plant',headers=self.auth,environ_overrides={'REMOTE_ADDR':'203.0.113.10'}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/plant',headers={**self.auth,'X-Forwarded-For':'203.0.113.10'}).status_code,403)
        with closing(connect(self.path)) as db,db:db.execute("UPDATE V1_SESSIONS SET created_at='2000-01-01T00:00:00Z'")
        self.assertEqual(self.client.get('/api/v1/auth/me',headers=self.auth).status_code,200)

    def test_db_binding_retry_does_not_reread_or_dispense_again(self):
        import sqlite3
        order,_=self.order();self.accept(order);self.start()
        with patch.object(self.engine,'bind',side_effect=sqlite3.OperationalError('injected write failure')):self.tick(6)
        plant=self.client.get('/api/v1/plant',headers=self.auth).json
        self.assertEqual(plant['feed_qr']['state'],'SAVE_FAILED')
        read_id=plant['feed_qr']['read_id']
        response=self.client.post('/api/v1/cycles/'+plant['cycle_id']+'/qr-retry',json=dict(request_id=uid(),action='SAVE_BINDING',previous_read_id=read_id),headers=self.auth)
        self.assertEqual(response.status_code,202,response.json)
        self.tick(80)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM V1_COMMANDS WHERE action='DISPENSE'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM V1_EVENTS WHERE body LIKE '%FEED_QR_READ%'").fetchone()[0],1)

    def test_lost_response_is_queried_then_operator_reconciled(self):
        order,_=self.order();self.accept(order);self.start()
        client=self.engine.clients['LINE'];original=client.submit
        def lose(data,boot):original(data,boot);raise TimeoutError('accepted, response lost')
        with patch.object(client,'submit',side_effect=lose):self.tick()
        self.tick(4)
        self.assertEqual(self.client.get('/api/v1/plant',headers=self.auth).json['state'],'RECOVERY_REQUIRED')
        with closing(connect(self.path)) as db:command=db.execute("SELECT * FROM V1_COMMANDS WHERE action='DISPENSE'").fetchone()
        self.assertEqual(command['state'],'COMPLETED')
        response=self.client.post('/api/v1/commands/'+command['command_id']+'/reconcile',json=dict(request_id=uid(),observation='COMPLETED',reason='모의 장비 완료와 실물 대조 시험'),headers=self.auth)
        self.assertEqual(response.status_code,200,response.json)
        self.assertFalse(response.json['requires_review'])
        self.tick(5)
        self.assertEqual(self.client.get('/api/v1/plant',headers=self.auth).json['state'],'PAUSED')
        self.client.post('/api/v1/plant/actions',json=dict(request_id=uid(),action='RESUME'),headers=self.auth)
        self.tick(90)
        self.assertEqual(self.client.get('/api/v1/orders/'+order['order_id'],headers=self.auth).json['factory_status'],'COMPLETED')
        self.assertEqual(sum(__import__('json').loads(value)['action']=='DISPENSE' for value in client.payloads.values()),1)

    def test_duplicate_and_conflicting_events(self):
        order,_=self.order();self.accept(order);self.start();self.tick(80)
        client=self.engine.clients['AGV'];event=next(e for e in client.log if e['kind']=='INSPECTION_ARRIVED')
        headers={'Authorization':'Bearer agv-test'}
        result=self.client.post('/api/v1/device-events',json=event,headers=headers)
        self.assertEqual(result.json['disposition'],'DUPLICATE')
        altered={**event,'occurred_at':'2020-01-01T00:00:00Z'}
        self.assertEqual(self.client.post('/api/v1/device-events',json=altered,headers=headers).status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM V1_INSPECTIONS').fetchone()[0],1)
        self.assertEqual(self.client.post('/api/v1/device-events',json=event,headers={'Authorization':'Bearer line-test'}).status_code,403)

    def test_out_of_order_events_do_not_skip_the_stage(self):
        order,_=self.order();self.accept(order);self.start();self.tick()
        client=self.engine.clients['LINE'];self.now+=1;client.status()
        original=client.log[0];later={**original,'event_id':uid(),'event_seq':2};client.log.append(later)
        self.assertEqual(self.engine.record(later)['disposition'],'HELD_FOR_REVIEW')
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT stage FROM V1_CYCLES').fetchone()[0],'DISPENSING')
        self.engine.record(original)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM V1_COMMANDS WHERE action='MOVE_TO_ASSEMBLY'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT last_seq FROM V1_DEVICES WHERE device_id='LINE'").fetchone()[0],2)

    def test_inspection_image_atomic_storage_and_delete_permissions(self):
        order,_=self.order();self.accept(order);self.start()
        for _ in range(50):
            self.tick()
            with closing(connect(self.path)) as db:inspection=db.execute('SELECT * FROM V1_INSPECTIONS WHERE body IS NOT NULL').fetchone()
            if inspection:break
        self.assertIsNotNone(inspection)
        image=self.client.get('/api/v1/inspections/'+inspection['inspection_id']+'/image',headers=self.auth)
        self.assertEqual(image.status_code,200)
        self.assertEqual(image.headers['Cache-Control'],'no-store')
        self.assertTrue(image.headers['X-Captured-At'].endswith('Z'))
        url='/api/v1/archive/'+inspection['archive_id']+'/delete';data=dict(request_id=uid(),reason='보관 자료 삭제 검증')
        self.assertEqual(self.client.post(url,json=data,headers=self.auth).status_code,403)
        self.assertEqual(self.client.post(url,json=data,headers=self.super_auth).status_code,409)
        self.tick(50)
        for _ in range(2):self.assertEqual(self.client.post(url,json=data,headers=self.super_auth).status_code,200)
        self.assertEqual(self.client.get('/api/v1/inspections/'+inspection['inspection_id']+'/image',headers=self.auth).status_code,404)

    def test_no_frame_goes_to_error_two_without_fake_image(self):
        order,_=self.order();self.accept(order);self.start()
        with patch.object(self.engine.vision,'inspect_product',return_value=None):self.tick(80)
        with closing(connect(self.path)) as db:
            row=db.execute('SELECT * FROM V1_INSPECTIONS').fetchone()
            result=__import__('json').loads(row['body'])
            self.assertEqual((result['verdict'],result['dispatch_station']),('INDETERMINATE',7))
            self.assertEqual(result['reasons'],['NO_FRAME'])
            self.assertIsNone(row['image']);self.assertIsNone(result['image_ref'])

    def test_controller_lease_and_unapproved_profiles(self):
        from factory_v1.engine import Engine
        from factory_v1.vision import PcVision
        from factory_v1.common import Fault
        other=Engine(self.app,{},PcVision(self.app,True));self.addCleanup(other.close)
        with self.assertRaises(Fault):other.acquire()
        self.assertEqual(self.client.post('/api/v1/manual-actions',json=dict(request_id=uid(),device_id='LINE',procedure_id='raw_a',reason='test'),headers=self.auth).status_code,503)
        self.assertEqual(self.client.put('/api/v1/settings/LINE',json=dict(request_id=uid(),device_id='LINE',validated_profile_id='not-approved',expected_revision=1),headers=self.auth).status_code,403)
        self.assertEqual(self.client.put('/api/v1/settings/LINE',json=dict(request_id=uid(),device_id='LINE',validated_profile_id='not-approved',expected_revision=1),headers=self.super_auth).status_code,503)
        for camera in ('CAM1','CAM2','CAM3','CAM4'):
            result=self.client.get('/api/v1/cameras/'+camera+'/frame',headers=self.auth)
            self.assertEqual(result.status_code,200)
            self.assertTrue(result.headers['X-Captured-At'].endswith('Z'))

    def test_role_change_revokes_session_and_account_request_does_not_store_password(self):
        data=dict(request_id=uid(),login_id='new-user',display_name='새 계정',role='ADMIN',initial_password='unique-test-password')
        a=self.client.post('/api/v1/accounts',json=data,headers=self.super_auth)
        b=self.client.post('/api/v1/accounts',json=data,headers=self.super_auth)
        self.assertEqual((a.status_code,b.status_code),(201,200))
        self.assertEqual(a.json['account_id'],b.json['account_id'])
        with closing(connect(self.path)) as db:
            row=db.execute("SELECT payload_hash,response FROM V1_REQUESTS WHERE request_id=?",(data['request_id'],)).fetchone()
            self.assertTrue(row['payload_hash'].startswith('scrypt:'))
            self.assertNotIn(data['initial_password'],row['response'])
        current=self.client.get('/api/v1/auth/me',headers=self.auth).json['account_id']
        result=self.client.patch('/api/v1/accounts/'+current+'/role',json=dict(request_id=uid(),role='SUPER_ADMIN'),headers=self.super_auth)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.client.get('/api/v1/auth/me',headers=self.auth).status_code,401)


if __name__=='__main__':unittest.main()
