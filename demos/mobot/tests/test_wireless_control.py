import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.common import Fault,uid
from wireless_control import WirelessBench
from wireless_pi.__main__ import Journal,SerialBench


class WirelessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)
        self.time=100.;self.pc=WirelessBench(self.path/'pc.sqlite3',{},clock=lambda:self.time)
        self.boot=uid();self.seq=0
        self.status=dict(serial_connected=True,serial_ready=True,protocol='WIRELESS_BENCH_10S',message='UNO 연결',lines=[])
        self.journal=Journal(self.path/'pi.sqlite3');self.pi=SerialBench({},self.journal)
        class Wire:
            def __init__(self):self.writes=[];self.incoming=b''
            def write(self,data):self.writes.append(data);return len(data)
            def read(self,n):data=self.incoming;self.incoming=b'';return data
            def close(self):pass
        self.wire=Wire();self.pi.serial=self.wire;self.pi.protocol='WIRELESS_BENCH_10S';self.pi.last_contact=time.monotonic()

    def poll(self,results=None):
        self.seq+=1
        return self.pc.poll(dict(boot_id=self.boot,sequence=self.seq,status=self.status,results=results or []))

    def test_delivery_round_trip_and_duplicates_execute_once(self):
        self.poll();command=uid();self.pc.queue(command,'ACTUATOR_FORWARD')
        reply=self.poll();self.pi.accept(reply['command']);self.pi.tick();self.assertEqual(len(self.wire.writes),1)
        self.pi.accept(reply['command']);self.pi.tick();self.assertEqual(len(self.wire.writes),1)
        self.wire.incoming=b'R 1 DONE TIMER\n';self.pi.tick();results=self.journal.results()
        self.assertEqual(results[0]['state'],'COMPLETED');response=self.poll(results)
        self.journal.acknowledge(response['acknowledged']);self.assertEqual(self.journal.results(),[])
        self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'COMPLETED')
        self.assertEqual(self.pc.queue(command,'ACTUATOR_FORWARD')['state'],'COMPLETED')
        with self.assertRaises(Fault):self.pc.queue(command,'BELT')

    def test_lost_reply_is_never_automatically_resent(self):
        self.poll();self.pc.queue(uid(),'BELT');first=self.poll();self.assertIsNotNone(first['command'])
        self.assertIsNone(self.poll()['command']);self.time+=9
        self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'UNKNOWN')

    def test_one_second_duration_and_compatible_firmware(self):
        self.poll();self.pc.queue(uid(),'ACTUATOR_FORWARD');command=self.poll()['command']
        self.assertEqual(command['duration_ms'],1000)
        self.pi.accept(command);self.pi.tick();self.assertIn(b'EXTEND 1000',self.wire.writes[-1])
        self.time+=2;self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'SENT')
        self.time+=8;self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'UNKNOWN')
        self.status['protocol']='WIRELESS_BENCH';self.poll()
        self.assertEqual(self.pc.queue(uid(),'ACTUATOR_REVERSE')['state'],'PENDING')
        self.pi.line('ALIVE MOBOT_WIRELESS_TEST_10S');self.assertEqual(self.pi.protocol,'WIRELESS_BENCH_10S')

    def test_legacy_one_second_delivery_and_long_command_rejected(self):
        self.status['protocol']='LEGACY_BENCH';self.pi.line('READY UNO_SIM_V1');self.poll()
        self.pc.queue(uid(),'ACTUATOR_FORWARD');command=self.poll()['command']
        self.assertEqual(command['duration_ms'],1000)
        self.pi.accept(command);self.pi.tick();self.assertEqual(self.wire.writes[-1],b'j\n')
        self.pi.line('JOG:DONE_POSITION_UNVERIFIED');self.poll(self.journal.results())
        self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'COMPLETED')
        with self.assertRaises(ValueError):self.pi.accept(dict(command_id=uid(),action='ACTUATOR_FORWARD',duration_ms=10000,valid_for_ms=2000))

    def test_expired_commands_and_reboot_do_not_move(self):
        self.poll();self.pc.queue(uid(),'BELT');self.time+=3
        self.assertIsNone(self.poll()['command']);self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'UNKNOWN')
        self.pc.queue(uid(),'ACTUATOR_FORWARD');command=self.poll()['command'];self.pi.accept(command,elapsed=4);self.pi.tick();self.assertFalse(self.wire.writes)
        self.boot=uid();self.seq=0;self.poll();self.assertEqual(self.pc.snapshot()['commands'][0]['state'],'UNKNOWN')

    def test_stop_preempts_bounded_motion_and_link_loss_requests_stop(self):
        self.poll();self.pc.queue(uid(),'ACTUATOR_FORWARD');self.pi.accept(self.poll()['command']);self.pi.tick()
        self.pc.queue(uid(),'STOP');self.pi.accept(self.poll()['command']);self.pi.tick()
        self.assertTrue(any(b'STOP' in line for line in self.wire.writes));self.assertEqual(self.journal.results()[0]['state'],'STOPPED')
        self.pi.last_contact=time.monotonic()-3;self.pi.tick();self.assertIsNone(self.pi.active)

    def test_unknown_firmware_blocks_motion_and_network_ping_is_explicit(self):
        self.pi.protocol='UNKNOWN';self.pi.serial=None
        command=dict(command_id=uid(),action='PING',duration_ms=0,valid_for_ms=2000);self.pi.accept(command);self.pi.tick()
        self.assertIn('UNO 연결은 별도',self.journal.results()[0]['message'])
        command=dict(command_id=uid(),action='ACTUATOR_FORWARD',duration_ms=1000,valid_for_ms=2000);self.pi.accept(command);self.pi.tick()
        self.assertEqual(self.journal.results()[1]['state'],'FAILED')

    def test_legacy_firmware_allows_jog_but_not_belt_and_ping_completes(self):
        self.pi.line('READY UNO_SIM_V1')
        command=dict(command_id=uid(),action='BELT',duration_ms=500,valid_for_ms=2000);self.pi.accept(command);self.pi.tick();self.assertFalse(self.wire.writes)
        command=dict(command_id=uid(),action='PING',duration_ms=0,valid_for_ms=2000);self.pi.accept(command);self.pi.tick();self.pi.line('TIMING:0:5500:3000:2000')
        self.assertEqual(self.journal.results()[1]['state'],'COMPLETED')
        command=dict(command_id=uid(),action='ACTUATOR_REVERSE',duration_ms=1000,valid_for_ms=2000);self.pi.accept(command);self.pi.tick();self.assertEqual(self.wire.writes[-1],b'k\n');self.pi.line('JOG:DONE_POSITION_UNVERIFIED')
        self.assertEqual(self.journal.results()[2]['state'],'COMPLETED')

    def test_restart_journal_keeps_unknown_and_duplicate_poll_is_stable(self):
        self.poll();self.pc.queue(uid(),'BELT');command=self.poll()['command'];self.pi.accept(command);self.pi.tick()
        recovered=Journal(self.path/'pi.sqlite3');self.assertEqual(recovered.results()[0]['state'],'UNKNOWN');self.assertIsNone(recovered.claim(command))
        packet=dict(boot_id=self.boot,sequence=self.seq+1,status=self.status,results=[])
        self.assertEqual(self.pc.poll(packet),self.pc.poll(packet))
        packet['status']=dict(self.status,message='changed')
        with self.assertRaises(Fault):self.pc.poll(packet)

    def test_duplicate_cached_poll_cannot_revive_expired_delivery(self):
        self.poll();self.pc.queue(uid(),'BELT')
        packet=dict(boot_id=self.boot,sequence=self.seq+1,status=self.status,results=[])
        self.assertIsNotNone(self.pc.poll(packet)['command']);self.time+=3
        self.assertIsNone(self.pc.poll(packet)['command'])

    def test_public_poll_token_isolated_from_internal_admin_controls(self):
        import test_factory_v1 as fixtures
        import os
        from app import create_app
        case=fixtures.FactoryTests('test_stock_shared_and_public_privacy');case.setUp();self.addCleanup(case.doCleanups);case.engine.close()
        token='private-wireless-test';config=case.path.parent/'wireless-test.json';config.write_text(json.dumps({'enabled':True,'token_hash':hashlib.sha256(token.encode()).hexdigest()}))
        with patch.dict(os.environ,{'MOBOT_DB':str(case.path),'MOBOT_V1_MODE':'SIMULATION','MOBOT_V1_BACKGROUND':'0'}):app=create_app()
        self.addCleanup(app.extensions['factory_v1'].close);client=app.test_client()
        packet=dict(boot_id=uid(),sequence=1,status=self.status,results=[])
        self.assertEqual(client.post('/wireless-test/v1/poll',json=packet).status_code,401)
        response=client.post('/wireless-test/v1/poll',json=packet,headers={'Authorization':'Bearer '+token,'X-Forwarded-For':'8.8.8.8'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(client.get('/api/v1/wireless-test/status',headers=case.auth).status_code,200)
        self.assertEqual(client.get('/api/v1/wireless-test/status',headers={**case.auth,'X-Forwarded-For':'8.8.8.8'}).status_code,403)
        from factory_v1.common import transaction
        with app.app_context(),transaction() as db:self.assertFalse(app.extensions['factory_v1'].ready(db))


if __name__=='__main__':unittest.main()
