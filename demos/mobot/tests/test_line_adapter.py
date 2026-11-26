import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory_v1.common import Fault,uid
from factory_v1.contracts import event,device_status
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from devices.line_pi.adapter import LineAdapter
from devices.line_pi.serial_link import run_serial
from devices.line_pi.__main__ import create_app,deliver_once,validate_config


class Wire:
    def __init__(self):self.writes=[];self.incoming=b'';self.partial=False
    def write(self,raw):
        self.writes.append(raw)
        return len(raw)-1 if self.partial else len(raw)
    def read(self,n):value=self.incoming[:n];self.incoming=self.incoming[n:];return value
    def close(self):pass


class LineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=dict(serial_port='/dev/serial/by-id/test-only',journal_path=str(Path(self.temp.name)/'journal.sqlite3'),hardware_profile_approved=True,heartbeat_interval_seconds=.2,serial_response_timeout_seconds=2,controller_timeout_seconds=3,controller_token='c'*40,device_token='e'*40,server_url='https://main.internal',internal_networks=['127.0.0.0/8'],listen_host='127.0.0.1')
        self.time=100.;self.a=LineAdapter(self.config,clock=lambda:self.time);self.addCleanup(self.a.db.close);self.addCleanup(self.a.close)
        self.wire=Wire();self.a.serial=self.wire;self.a.receive(f'V1 OK {self.a.sid}')
        self.cycle=uid();self.app=create_app(self.a,self.config);self.client=self.app.test_client()
        self.auth={'Authorization':'Bearer '+self.config['controller_token'],'X-Expected-Boot-Id':self.a.boot}

    def command(self,action='DISPENSE',cycle=None,params=None):return dict(command_id=uid(),device_id='LINE',cycle_id=cycle or self.cycle,action=action,params=params or {})
    def reply(self,command,state,detail):
        row=self.a.db.execute('SELECT * FROM commands WHERE command_id=?',(command['command_id'],)).fetchone()
        self.a.consume(f"R {row['sid']} {row['cyc']} {row['seq']} {state} {detail}\r\n".encode())

    def test_boot_without_controller_leaves_uno_disarmed(self):
        self.a.serial=None;self.a.synced=False
        self.time+=60
        self.assertEqual(self.client.get('/device/v1/status').status_code,401)
        with patch('serial.Serial') as port, patch.object(self.a.stop,'wait',side_effect=(False,False,True)):
            run_serial(self.a)
        port.assert_not_called()
        self.assertFalse(self.a.fault)
        self.assertEqual(self.a.status()['state'],'UNKNOWN')

    def test_authenticated_controller_can_wait_for_usb_to_appear(self):
        self.a.serial=None;self.a.synced=False
        self.assertEqual(self.client.get('/device/v1/status',headers=self.auth).status_code,200)
        self.wire.incoming=f'V1 OK {self.a.sid}\n'.encode()
        with (
            patch('serial.Serial', side_effect=(OSError('port missing'), self.wire)) as port,
            patch.object(self.a.stop, 'wait', side_effect=(False, False, False, True)),
            self.assertLogs('mobot.line', level='WARNING'),
        ):
            run_serial(self.a)
        self.assertEqual(port.call_count,2)
        self.assertEqual(self.wire.writes[0],f'V1 SYNC {self.a.sid}\n'.encode())
        self.assertFalse(any(line.startswith(b'C ') for line in self.wire.writes))
        self.assertFalse(self.a.fault)
        self.assertEqual(self.a.status()['state'],'IDLE')

    def test_live_completion_sequence_and_qr_gate(self):
        first=self.command();result=self.a.submit(first,self.a.boot);self.assertEqual(result['state'],'ACCEPTED')
        self.a.pump();self.assertEqual(len([r for r in self.wire.writes if r.startswith(b'C ')]),1)
        self.assertEqual(self.a.command(first['command_id'])['state'],'ACCEPTED')
        with self.assertRaises(Fault):self.a.submit(self.command('MOVE_TO_ASSEMBLY'),self.a.boot)
        self.reply(first,'DONE','DISP_TIMER');self.assertEqual(self.a.events(self.a.boot,0)[0]['data'],{'evidence':'TIMER'})
        self.assertFalse(any(b' ASM' in r for r in self.wire.writes))
        for action,detail in [('MOVE_TO_ASSEMBLY','IR1_STOP'),('MOVE_TO_PICKUP','IR2_STOP')]:
            cmd=self.command(action);self.a.submit(cmd,self.a.boot);self.a.pump();self.reply(cmd,'DONE',detail)
        rows=self.a.events(self.a.boot,0);self.assertEqual(len(rows),3)
        for row in rows:event(row)
        device_status(self.a.status(),'LINE');self.assertEqual(self.a.status()['state'],'IDLE')

    def test_duplicates_and_uuid_conflict_no_resend(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump()
        for _ in range(4):self.a.submit(cmd,self.a.boot);self.time+=.5;self.a.last_rx=self.time;self.a.touch();self.a.pump()
        self.assertEqual(sum(r.startswith(b'C ') for r in self.wire.writes),1)
        self.assertTrue(any(r.startswith(b'Q ') for r in self.wire.writes))
        changed=dict(cmd,cycle_id=uid())
        with self.assertRaises(Fault) as conflict:self.a.submit(changed,self.a.boot)
        self.assertEqual(conflict.exception.status,409)
        self.reply(cmd,'DONE','DISP_TIMER');self.reply(cmd,'DONE','DISP_TIMER');self.assertEqual(len(self.a.events(self.a.boot,0)),1)

    def test_pause_requires_output_confirmation_and_resume_parent(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump();self.reply(cmd,'RUN','OK')
        pause=self.command('PAUSE');self.a.submit(pause,self.a.boot);self.a.pump();self.reply(cmd,'HOLD','OK')
        self.assertNotEqual(self.a.status()['state'],'PAUSED')
        self.reply(pause,'DONE','BOTH_PAUSED');self.assertEqual(self.a.status()['state'],'PAUSED')
        with self.assertRaises(Fault):self.a.submit(self.command('RESUME',params={'pause_command_id':uid()}),self.a.boot)
        resume=self.command('RESUME',params={'pause_command_id':pause['command_id']});self.a.submit(resume,self.a.boot);self.a.pump();self.reply(resume,'DONE','RESUMED')
        self.assertEqual(self.a.status()['state'],'BUSY');self.reply(cmd,'DONE','DISP_TIMER')

    def test_wrong_evidence_stale_sid_and_legacy_strings(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump()
        self.a.consume(b'CYCLE_DONE:READY_A\nR 0 1 1 DONE DISP_TIMER\n')
        self.assertEqual(self.a.command(cmd['command_id'])['state'],'ACCEPTED')
        self.reply(cmd,'DONE','IR1_STOP');self.assertEqual(self.a.command(cmd['command_id'])['state'],'UNKNOWN')
        self.assertEqual(self.a.events(self.a.boot,0)[0]['kind'],'DEVICE_FAILED')

    def test_reboot_invalidates_boot_and_pending_motion(self):
        cmd=self.command();old=self.a.boot;self.a.submit(cmd,old);self.a.pump();self.a.receive('READY V1')
        self.assertNotEqual(self.a.boot,old);self.assertEqual(self.a.command(cmd['command_id'])['state'],'UNKNOWN')
        self.assertEqual(self.a.events(old,0)[0]['kind'],'DEVICE_FAILED')
        with self.assertRaises(Fault):self.a.submit(self.command(),old)

    def test_restart_preserves_commands_events_and_unknown(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump()
        second=LineAdapter(self.config);self.addCleanup(second.db.close);self.addCleanup(second.close)
        self.assertEqual(second.command(cmd['command_id'])['state'],'UNKNOWN');self.assertTrue(second.fault)
        self.assertEqual(second.db.execute('SELECT count(*) FROM commands').fetchone()[0],1)
        self.assertEqual(len(second.events(second.boot,0)),1)

    def test_partial_write_durable_intent_no_automatic_repeat(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.wire.partial=True
        with self.assertRaises(OSError):self.a.pump()
        self.wire.partial=False;self.time+=.5;self.a.pump()
        self.assertEqual(sum(r.startswith(b'C ') for r in self.wire.writes),1)
        self.assertEqual(self.a.db.execute('SELECT attempted FROM commands').fetchone()[0],1)

    def test_controller_loss_stops_heartbeat_and_latches_review(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump();self.time+=4;self.a.last_rx=self.time;before=len(self.wire.writes);self.a.pump()
        self.assertEqual(len(self.wire.writes),before);self.assertTrue(self.a.fault)
        self.assertEqual(self.a.command(cmd['command_id'])['state'],'UNKNOWN')

    def test_http_auth_internal_boot_and_pagination(self):
        self.assertEqual(self.client.get('/device/v1/status').status_code,401)
        self.assertEqual(self.client.get('/device/v1/status',headers=self.auth,environ_overrides={'REMOTE_ADDR':'8.8.8.8'}).status_code,403)
        result=self.client.post('/device/v1/commands',json=self.command(),headers=dict(self.auth,**{'X-Expected-Boot-Id':uid()}));self.assertEqual(result.status_code,409)
        for action,detail in [('DISPENSE','DISP_TIMER'),('MOVE_TO_ASSEMBLY','IR1_STOP'),('MOVE_TO_PICKUP','IR2_STOP')]:
            cmd=self.command(action);response=self.client.post('/device/v1/commands',json=cmd,headers=self.auth);self.assertEqual(response.status_code,202,response.json);self.a.pump();self.reply(cmd,'DONE',detail)
        result=self.client.get('/device/v1/events',query_string=dict(boot_id=self.a.boot,after_seq=0,limit=1),headers=self.auth)
        self.assertEqual(len(result.json['events']),1);self.assertIsNotNone(result.json['next_cursor'])
        next_page=self.client.get('/device/v1/events',query_string=dict(boot_id=self.a.boot,after_seq=0,limit=1,cursor=result.json['next_cursor']),headers=self.auth)
        self.assertEqual(next_page.json['events'][0]['event_seq'],2)

    def test_unapproved_profiles_and_insecure_configs_rejected(self):
        self.config['hardware_profile_approved']=False
        with self.assertRaises(Fault):self.a.submit(self.command(),self.a.boot)
        self.assertFalse(self.wire.writes)
        validate_config(self.config)
        for update in ({'server_url':'https://public.ngrok-free.dev'},{'controller_token':'short'},{'listen_host':'0.0.0.0'}):
            with self.assertRaises(Fault):validate_config(dict(self.config,**update))

    def test_event_delivery_retry_keeps_same_uuid(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump();self.reply(cmd,'DONE','DISP_TIMER');rows=self.a.events(self.a.boot,0)
        with patch('urllib.request.urlopen',side_effect=OSError('offline')):deliver_once(self.a,self.config)
        self.assertEqual(self.a.db.execute('SELECT delivered FROM events').fetchone()[0],0)
        response=SimpleNamespace(status=200)
        response.read=lambda:json.dumps({'event_id':rows[0]['event_id']}).encode()
        class Context:
            def __enter__(self):return response
            def __exit__(self,*_):return False
        with patch('urllib.request.urlopen',return_value=Context()) as post:
            deliver_once(self.a,self.config)
            self.assertEqual(json.loads(post.call_args.args[0].data)['event_id'],rows[0]['event_id'])
        self.assertEqual(self.a.db.execute('SELECT delivered FROM events').fetchone()[0],1)

    def test_uno_error_and_oversized_reply_never_complete(self):
        cmd=self.command();self.a.submit(cmd,self.a.boot);self.a.pump()
        row=self.a.db.execute('SELECT * FROM commands').fetchone()
        self.a.consume((f"R {row['sid']} {row['cyc']} {row['seq']} DONE DISP_TIMER"+' '*64+'\n').encode())
        self.assertEqual(self.a.command(cmd['command_id'])['state'],'ACCEPTED')
        self.a.receive('ERR WATCHDOG');self.assertTrue(self.a.fault)
        self.assertEqual(self.a.command(cmd['command_id'])['state'],'UNKNOWN')

    def test_boot_change_between_commands_holds_active_backend_cycle(self):
        # Reboot while waiting for QR is dangerous even after DISPENSE completed.
        import test_factory_v1 as backend
        case=backend.FactoryTests('test_stock_shared_and_public_privacy');case.setUp()
        try:
            order,_=case.order();case.accept(order);case.start();case.tick(3)
            with case.app.app_context():
                from factory_v1.common import transaction
                with transaction() as db:
                    cycle=case.engine.cycle(db);self.assertIsNotNone(cycle)
                    db.execute("UPDATE V1_COMMANDS SET state='COMPLETED' WHERE device_id='LINE'")
                case.engine.clients['LINE'].boot=uid()
                case.engine.refresh_devices()
                self.assertEqual(case.engine.plant()['state'],'RECOVERY_REQUIRED')
        finally:case.doCleanups()


if __name__=='__main__':unittest.main()
