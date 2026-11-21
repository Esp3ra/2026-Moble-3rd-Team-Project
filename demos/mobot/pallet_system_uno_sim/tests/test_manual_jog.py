import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app import create_app
from controller import Bench, READY
from database import DatabaseWorker

class Clock:
    def __init__(self):self.at=100.
    def __call__(self):return self.at

class JogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.clock=Clock();self.bench=Bench(Path(self.temp.name),demo=True,clock=self.clock)
        self.bench.connect('DEMO')
        self.bench.jog_ms=250  # Also support previously uploaded firmware.

    def test_demo_jog_has_bounded_duration(self):
        self.bench.manual_jog('reverse','owner',True)
        self.assertEqual(self.bench.state,'JOGGING')
        self.bench.tick();self.assertTrue(self.bench.jog['acked'])
        self.clock.at+=.24;self.bench.tick();self.assertEqual(self.bench.state,'JOGGING')
        self.clock.at+=.02;self.bench.tick()
        self.assertEqual(self.bench.state,'STOPPED');self.assertTrue(self.bench.jog['done'])
        self.assertFalse(DatabaseWorker.eligible(self.bench.snapshot()))

    def test_direction_commands_and_ack_matching(self):
        for direction,command in [('forward','j'),('reverse','k')]:
            with self.subTest(direction=direction):
                self.clock.at+=1
                self.bench.manual_jog(direction,'owner',True)
                self.assertEqual(self.bench.events[-1]['command'],command)
                wrong='REVERSE' if direction=='forward' else 'FORWARD'
                self.bench.line('JOG:'+wrong+'_250MS')
                self.assertFalse(self.bench.jog['acked'])
                self.bench.line('JOG:'+direction.upper()+'_250MS')
                self.bench.line('JOG:DONE_POSITION_UNVERIFIED')
                self.assertTrue(self.bench.jog['done'])

    def test_rejects_movement_during_automatic_and_result_confirmation(self):
        for state in ('STARTING','PUSHING','RETURNING','WAIT_IR','AWAIT_CONFIRM','JOGGING'):
            self.bench.state=state
            with self.assertRaises(ValueError):self.bench.manual_jog('forward','owner',True)

    def test_rejects_unconfirmed_area_bad_input_and_old_firmware(self):
        with self.assertRaises(ValueError):self.bench.manual_jog('forward','owner',False)
        for direction in ('wrong',[],None):
            with self.assertRaises(ValueError):self.bench.manual_jog(direction,'owner',True)
        self.bench.jog_ms=None
        with self.assertRaises(ValueError):self.bench.manual_jog('forward','owner',True)

    def test_direction_reversal_has_cooldown(self):
        self.bench.manual_jog('forward','owner',True)
        self.clock.at+=.3;self.bench.tick()
        with self.assertRaises(ValueError):self.bench.manual_jog('reverse','owner',True)
        self.clock.at+=.61;self.bench.manual_jog('reverse','owner',True)
        self.assertEqual(self.bench.state,'JOGGING')

    def test_manual_jog_does_not_require_camera_ir_clear_or_database(self):
        self.bench.ir_clear=False;self.bench.camera_ok=False
        self.bench.manual_jog('reverse','owner',True)
        self.assertEqual(self.bench.state,'JOGGING')

    def test_stop_prevents_late_jog_done_from_changing_state(self):
        self.bench.manual_jog('reverse','owner',True)
        self.bench.abort('operator',manual=True)
        self.bench.line('JOG:DONE_POSITION_UNVERIFIED')
        self.assertEqual(self.bench.state,'STOPPED');self.assertFalse(self.bench.jog['done'])

    def test_jog_blocks_automatic_start_and_disconnect(self):
        self.bench.manual_jog('reverse','owner',True)
        with self.assertRaises(ValueError):self.bench.start('owner',True)
        with self.assertRaises(ValueError):self.bench.disconnect()

    def test_ack_and_completion_timeouts(self):
        for ack in (False,True):
            with self.subTest(ack=ack):
                self.bench.state='STOPPED';self.bench.ready=True;self.clock.at+=1
                self.bench.manual_jog('reverse','owner',True)
                self.bench.demo=False;self.bench._write=Mock()
                if ack:self.bench.line('JOG:REVERSE_250MS')
                self.clock.at+=2.6;self.bench.tick()
                self.assertEqual(self.bench.state,'ERROR');self.bench._write.assert_called_with('s')
                self.bench.demo=True

    def test_reboot_during_jog_stops_without_trial(self):
        self.bench.manual_jog('reverse','owner',True)
        self.bench.line(READY)
        self.assertEqual(self.bench.state,'ERROR')

    def test_completion_without_ack_is_error(self):
        self.bench.manual_jog('reverse','owner',True)
        self.bench.line('JOG:DONE_POSITION_UNVERIFIED')
        self.assertEqual(self.bench.state,'ERROR')

    def test_user_one_second_jog_is_supported(self):
        self.bench.jog_ms=1000
        self.bench.manual_jog('reverse','owner',True)
        self.assertEqual(self.bench.jog['duration_ms'],1000)
        self.bench.line('JOG:REVERSE_1000MS')
        self.assertTrue(self.bench.jog['acked'])
        self.clock.at+=.99;self.bench.tick()
        self.assertEqual(self.bench.state,'JOGGING')
        self.clock.at+=.02;self.bench.tick()
        self.assertEqual(self.bench.state,'STOPPED')

    def test_longer_jog_uses_duration_based_completion_timeout(self):
        self.bench.jog_ms=2000
        self.bench.manual_jog('forward','owner',True)
        self.bench.demo=False;self.bench._write=Mock()
        self.bench.line('JOG:FORWARD_2000MS')
        self.clock.at+=2.5;self.bench.tick()
        self.assertEqual(self.bench.state,'JOGGING')
        self.clock.at+=1.6;self.bench.tick()
        self.assertEqual(self.bench.state,'ERROR')
        self.bench._write.assert_called_with('s')

    def test_unsupported_duration_rejected(self):
        for duration in (-100,0,99,5001,None):
            self.bench.jog_ms=duration
            with self.assertRaises(ValueError):self.bench.manual_jog('reverse','owner',True)

    def test_api_and_csrf(self):
        app=create_app(self.bench,DatabaseWorker(self.bench));client=app.test_client()
        data={'direction':'reverse','owner':'owner','area_clear':True}
        self.assertEqual(client.post('/api/actuator/jog',json=data).status_code,403)
        headers={'X-Test-Token':app.config['CSRF_TOKEN']}
        self.assertEqual(client.post('/api/actuator/jog',headers=headers,json=data).status_code,200)
        self.assertEqual(client.post('/api/actuator/jog',headers=headers,json=data).status_code,409)
        self.assertEqual(client.post('/api/stop',headers=headers,json={}).status_code,200)

    def test_api_blocks_jog_during_pending_db_work(self):
        db=DatabaseWorker(self.bench);db.data={'status':'processing'}
        app=create_app(self.bench,db)
        result=app.test_client().post('/api/actuator/jog',headers={'X-Test-Token':app.config['CSRF_TOKEN']},json={'direction':'forward','owner':'owner','area_clear':True})
        self.assertEqual(result.status_code,409)

    def test_firmware_rejection_cancels_jog(self):
        self.bench.manual_jog('reverse','owner',True)
        self.bench.line('ERR:ACTUATOR_COOLDOWN')
        self.assertEqual(self.bench.state,'ERROR')

if __name__=='__main__':unittest.main()
