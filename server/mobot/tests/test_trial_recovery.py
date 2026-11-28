from contextlib import closing
from types import SimpleNamespace
import unittest
from werkzeug.security import generate_password_hash
from orders import connect
from factory_v1.common import uid
from factory_v1.trial_recovery import PROCEDURE
import test_mixed_trial as mixed_tests


class TrialRecoveryTests(unittest.TestCase):
    def setup_api(self,app,path):
        client=app.test_client()
        with closing(connect(path)) as db,db:
            db.execute('INSERT INTO ACCOUNTS(role_id,login_id,name,password_hash) VALUES(2,?,?,?)',('admin','시험',generate_password_hash('test-only')))
        auth={'Authorization':'Bearer '+client.post('/api/v1/auth/login',json={'login_id':'admin','password':'test-only'}).json['token']}
        return client,auth

    def test_recovery_is_authenticated_idempotent_and_does_not_fabricate_completion(self):
        with mixed_tests.MixedTrialTests().fixture() as (app,engine,path,adapter,device,tick):
            tick();client,auth=self.setup_api(app,path);calls=[]
            device.raw['state']='line_lost';adapter.step()
            with app.app_context():engine.refresh_devices()
            def restore(reason):
                calls.append(reason);device.raw.update(state='ready',position=2,servo_angle=85,control_connected=True)
                adapter.step()
            recovery=app.extensions['mixed_trial_recovery'];recovery.host=SimpleNamespace(recover_standby=restore)
            old=uid()
            with closing(connect(path)) as db,db:
                db.execute("UPDATE V1_PLANT SET state='RECOVERY_REQUIRED',auto_enabled=0")
                db.execute("INSERT INTO V1_COMMANDS(command_id,device_id,cycle_id,action,params,expected_boot_id,state,attempted,updated_at) VALUES(?,'LINE',NULL,'PAUSE','{}',?,'UNKNOWN',1,'2026-11-28T00:00:00Z')",(old,uid()))
            data=dict(request_id=uid(),device_id='AGV',procedure_id=PROCEDURE,reason='Operator confirmed unloaded point 2 and empty stopped conveyor')
            self.assertEqual(client.post('/api/v1/manual-actions',json=data).status_code,401)
            first=client.post('/api/v1/manual-actions',json=data,headers=auth)
            self.assertEqual(first.status_code,202,first.json)
            recovery.thread.join(timeout=3)
            repeat=client.post('/api/v1/manual-actions',json=data,headers=auth)
            self.assertEqual(repeat.json['command_id'],first.json['command_id'])
            self.assertEqual(len(calls),1)
            result=client.get('/api/v1/manual-actions/'+first.json['command_id'],headers=auth)
            self.assertEqual(result.json['state'],'COMPLETED',result.json)
            with closing(connect(path)) as db:
                self.assertEqual(tuple(db.execute('SELECT state,result_event_id FROM V1_COMMANDS WHERE command_id=?',(old,)).fetchone()),('STOPPED',None))
                self.assertEqual(db.execute('SELECT count(*) FROM V1_EVENTS').fetchone()[0],0)
                self.assertEqual(tuple(db.execute('SELECT state,auto_enabled FROM V1_PLANT').fetchone()),('READY',0))
                self.assertIsNotNone(db.execute("SELECT 1 FROM V1_AUDIT WHERE action='MIXED_TRIAL_STANDBY_RECOVERY'").fetchone())
            self.assertEqual(device.calls,[],'Test recovery host must not issue any device motion')

    def test_pending_work_blocks_reset_and_failures_remain_unknown(self):
        with mixed_tests.MixedTrialTests().fixture() as (app,engine,path,adapter,device,tick):
            tick();client,auth=self.setup_api(app,path);calls=[]
            recovery=app.extensions['mixed_trial_recovery']
            def fail(reason):calls.append(reason);raise OSError('response lost')
            recovery.host=SimpleNamespace(recover_standby=fail)
            data=dict(request_id=uid(),device_id='AGV',procedure_id=PROCEDURE,reason='Operator confirmed stopped hardware')
            with closing(connect(path)) as db,db:db.execute('UPDATE V1_PLANT SET auto_enabled=1')
            self.assertEqual(client.post('/api/v1/manual-actions',json=data,headers=auth).status_code,409)
            with closing(connect(path)) as db,db:
                db.execute('UPDATE V1_PLANT SET auto_enabled=0')
                db.execute("INSERT INTO V1_COMMANDS(command_id,device_id,cycle_id,action,params,expected_boot_id,state,attempted,updated_at) VALUES(?,'AGV',?,'PICK_UP','{}',?,'UNKNOWN',1,'2026-11-28T00:00:00Z')",(uid(),uid(),uid()))
            self.assertEqual(client.post('/api/v1/manual-actions',json=data,headers=auth).status_code,409)
            self.assertEqual(calls,[])
            with closing(connect(path)) as db,db:db.execute("UPDATE V1_COMMANDS SET state='STOPPED'")
            first=client.post('/api/v1/manual-actions',json=data,headers=auth)
            self.assertEqual(first.status_code,202,first.json);recovery.thread.join(timeout=3)
            result=client.get('/api/v1/manual-actions/'+first.json['command_id'],headers=auth)
            self.assertEqual(result.json['state'],'UNKNOWN')
            with closing(connect(path)) as db:self.assertEqual(db.execute('SELECT state FROM V1_PLANT').fetchone()[0],'RECOVERY_REQUIRED')


if __name__=='__main__':unittest.main()
