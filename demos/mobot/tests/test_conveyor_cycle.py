from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import test_wireless_control as fixtures
from factory_v1.common import uid,Fault


class ConveyorTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.WirelessTests('test_delivery_round_trip_and_duplicates_execute_once');self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.pc,self.pi,self.wire=self.case.pc,self.case.pi,self.case.wire
        self.pi.line('READY UNO_SIM_QR_V2');self.report()

    def report(self,results=None):
        self.case.status=self.pi.status()
        return self.case.poll(results)

    def start(self):
        self.id=uid();self.pc.queue(self.id,'RUN_CYCLE');command=self.report()['command']
        self.pi.accept(command);self.pi.tick();self.serial=self.pi.active[1]
        self.assertEqual(self.wire.writes[-1],f'a {self.serial}\n'.encode())

    def event(self,text):self.pi.line(f'EV {self.serial} '+text)

    def test_full_serial_progress_qr_gate_and_completion(self):
        self.start()
        for text,stage in [('ACTUATOR:PUSH_10000MS','DISPENSING'),('ACTUATOR:PAUSE_500MS','REVERSAL'),('RETURN_AND_BELT:STARTED','TO_IR1'),('IR_TIMER:STARTED','IR1_DELAY'),('SIM:STATION_STOPPED','WAIT_QR')]:
            self.event(text);self.report();self.assertEqual(self.pc.snapshot()['cycle']['stage'],stage)
        vision=SimpleNamespace(simulation=False,qr=SimpleNamespace(status=Mock(return_value=dict(state='UNREADABLE'))))
        self.pc.confirm_from_vision(vision)
        # A failed, stale or pre-stop camera result cannot release the belt.
        for state in ('UNREADABLE','MULTIPLE_QR','INVALID_QR'):
            vision.qr.status.return_value=dict(state=state)
            self.pc.confirm_from_vision(vision);self.assertIsNone(self.report()['command'])
        vision.qr.status.return_value=dict(state='READ',pallet_id='PALLET-01',frame_id=uid(),captured_at=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat())
        self.pc.confirm_from_vision(vision);self.assertIsNone(self.report()['command'])
        vision.qr.status.return_value.update(frame_id=uid(),captured_at=datetime.now(timezone.utc).isoformat())
        self.pc.confirm_from_vision(vision);self.pc.confirm_from_vision(vision)
        command=self.report()['command'];self.assertEqual(command['action'],'QR_CONFIRM');self.assertEqual(command['parent_command_id'],self.id)
        self.pi.accept(command);self.pi.tick();self.assertEqual(self.wire.writes[-1],f'q {self.serial}\n'.encode())
        self.pi.accept(command);self.pi.tick();self.assertEqual(self.wire.writes.count(f'q {self.serial}\n'.encode()),1)
        self.event('QR:RECOGNIZED_ROBOT_WAIT');self.report(self.pi.journal.results())
        self.assertEqual(self.pi.active[0]['command_id'],self.id);self.assertTrue(self.pc.snapshot()['cycle']['qr_confirmed'])
        for text,stage in [('SIM:BELT_RESTARTED','TO_IR2'),('IR2_TIMER:STARTED','IR2_DELAY'),('BELT_STOPPED:IR2_DELAY_DONE','WAIT_RETRACT'),('CYCLE_DONE:READY_A','DONE')]:
            self.event(text);self.report(self.pi.journal.results());self.assertEqual(self.pc.snapshot()['cycle']['stage'],stage)
        commands=self.pc.snapshot()['commands'];self.assertEqual(next(x['state'] for x in commands if x['command_id']==self.id),'COMPLETED')

    def test_old_cycle_and_untagged_lines_cannot_advance_current_cycle(self):
        self.start();self.event('ACTUATOR:PUSH_10000MS')
        self.pi.line('EV 9999 CYCLE_DONE:READY_A');self.pi.line('CYCLE_DONE:READY_A')
        self.assertIsNotNone(self.pi.active);self.assertEqual(self.pi.cycle['stage'],'DISPENSING')
        self.pi.line('TIMING:0:5350:3000:1750');self.assertEqual(self.pi.protocol,'UNO_SIM_QR_V2')

    def test_stop_mid_cycle_and_connection_loss_never_restart(self):
        self.start();self.event('QR:WAITING');self.report()
        self.pc.queue(uid(),'STOP');self.pi.accept(self.report()['command']);self.pi.tick()
        self.assertEqual(self.wire.writes[-1],b's\n')
        self.event('STOPPED:BOTH_READY_A');self.report(self.pi.journal.results())
        self.assertEqual(self.pc.snapshot()['cycle']['stage'],'STOPPED');self.assertIsNone(self.pi.active)
        self.start();self.pi.last_contact=0;self.pi.tick()
        self.assertEqual(self.pi.cycle['stage'],'UNKNOWN');self.assertIsNone(self.pi.active)
        self.report(self.pi.journal.results())
        with self.assertRaises(Fault):self.pc.queue(uid(),'RUN_CYCLE')

    def test_wrong_parent_or_phase_cannot_confirm_qr_and_legacy_cannot_start(self):
        self.start()
        command=dict(command_id=uid(),action='QR_CONFIRM',duration_ms=0,valid_for_ms=2000,parent_command_id=uid())
        self.pi.accept(command);self.pi.tick();self.assertEqual(len(self.wire.writes),1)
        self.assertEqual(self.pi.journal.results()[0]['state'],'FAILED')
        self.pi.line('READY UNO_SIM_V1');self.assertEqual(self.pi.cycle['stage'],'UNKNOWN')
        self.report(self.pi.journal.results())
        with self.assertRaises(Fault):self.pc.queue(uid(),'RUN_CYCLE')

    def test_cycle_result_window_does_not_expire_at_jog_timeout(self):
        self.start();self.case.time+=100
        self.assertEqual(next(x for x in self.pc.snapshot()['commands'] if x['command_id']==self.id)['state'],'SENT')
