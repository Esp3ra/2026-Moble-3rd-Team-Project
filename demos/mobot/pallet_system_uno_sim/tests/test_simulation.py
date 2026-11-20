import tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from app import ROOT,create_app,camera_worker,demo_step,monitor_worker
from controller import Bench,ACTIVE,MOTION,READY
from database import DatabaseWorker

DEFAULTS={'ir1_delay_ms':5500,'robot_wait_ms':3000,'ir2_delay_ms':2000}
class SimulationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.now=100.
  self.bench=Bench(Path(self.temp.name),demo=True,clock=lambda:self.now)
  self.bench.connect('DEMO');self.image=np.zeros((100,100,3),np.uint8)
  for _ in range(3):self.bench.frame([],self.image,self.now)
  self.db=DatabaseWorker(self.bench,'auto')
 def at(self,elapsed):
  self.now=100+elapsed;self.bench.camera_at=self.now;self.bench.heartbeat('owner')
  demo_step(self.bench);self.bench.tick()
 def start(self,qr=True):
  self.bench.start('owner',True);self.at(.11)
  if qr:
   self.bench.frame(['PALLET-06'],self.image,self.now);self.at(.12)
 def complete(self):
  self.start();self.at(13)
  self.assertEqual(self.bench.state,'AWAIT_CONFIRM')
 def test_actual_firmware_start_ack_is_accepted(self):
  import re
  firmware=(ROOT/'firmware/RobotWorkSimulation/RobotWorkSimulation.ino').read_text(encoding='utf-8')
  response=re.search(r'emit\(F\("(ACTUATOR:PUSH_[0-9]+MS)"\)\)',firmware)
  self.assertIsNotNone(response)
  self.assertEqual(response.group(1),'ACTUATOR:PUSH_10000MS')
  self.bench.start('owner',True)
  self.bench.line(response.group(1))
  self.assertTrue(self.bench.trial['acked'])
  self.assertEqual(self.bench.state,'PUSHING')
 def test_default_three_stage_times(self):
  self.assertEqual(self.bench.timing_values,DEFAULTS)
  self.start();self.at(1.81)
  self.assertTrue(self.bench.trial['ir_triggered'])
  self.at(7.29);self.assertFalse(self.bench.trial['first_belt_stopped'])
  self.at(7.31);self.assertEqual(self.bench.state,'ROBOT_WAIT')
  self.assertFalse(self.bench.trial['belt_stopped'])
  self.at(10.29);self.assertFalse(self.bench.trial['sim_resumed'])
  self.at(10.31);self.assertEqual(self.bench.state,'SECOND_BELT')
  self.at(10.81);self.assertEqual(self.bench.state,'WAIT_IR2')
  self.at(12.79);self.assertFalse(self.bench.trial['belt_stopped'])
  self.at(12.81);self.assertTrue(self.bench.trial['belt_stopped'])
  self.at(12.91);self.assertEqual(self.bench.state,'AWAIT_CONFIRM')
  self.bench.confirm_ejection('owner');self.assertEqual(self.bench.state,'SUCCESS')
 def test_settings_command_waits_for_exact_ack(self):
  self.bench.demo=False;self.bench._write=Mock()
  values={'ir1_delay_ms':6000,'robot_wait_ms':4000,'ir2_delay_ms':2500}
  self.bench.update_timings(values);request=self.bench.timing_pending
  self.assertEqual(self.bench.timing_status,'pending');self.assertEqual(self.bench.timing_values,DEFAULTS)
  self.assertEqual(self.bench._write.call_args.args[0],f"T {request['revision']} 6000 4000 2500")
  self.bench.line(f"TIMING:{request['revision']+1}:6000:4000:2500")
  self.assertEqual(self.bench.timing_status,'pending')
  self.bench.line(f"TIMING:{request['revision']}:6000:4000:2000")
  self.assertEqual(self.bench.timing_status,'pending')
  self.bench.line(f"TIMING:{request['revision']}:6000:4000:2500")
  self.assertEqual(self.bench.timing_status,'confirmed');self.assertEqual(self.bench.timing_values,values)
 def test_demo_applies_changes_and_new_cycle_uses_them(self):
  values={'ir1_delay_ms':4000,'robot_wait_ms':1000,'ir2_delay_ms':500}
  self.bench.update_timings(values);self.bench.tick()
  self.assertEqual(self.bench.timing_values,values)
  self.start();self.assertEqual(self.bench.trial['timings'],values)
  self.at(5.81);self.assertEqual(self.bench.state,'ROBOT_WAIT')
  self.at(6.81);self.assertEqual(self.bench.state,'SECOND_BELT')
  self.at(7.81);self.assertTrue(self.bench.trial['belt_stopped'])
  self.at(7.91);self.assertEqual(self.bench.state,'AWAIT_CONFIRM')
 def test_cycle_timing_snapshot_is_not_mutated(self):
  self.start();self.bench.timing_values['robot_wait_ms']=9000
  self.assertEqual(self.bench.trial['timings']['robot_wait_ms'],3000)
 def test_no_settings_changes_during_motion_or_confirmation(self):
  for state in MOTION|{'AWAIT_CONFIRM'}:
   self.bench.state=state
   with self.assertRaises(ValueError):self.bench.update_timings(DEFAULTS)
 def test_pending_settings_block_start_and_jog(self):
  self.bench.update_timings(DEFAULTS)
  with self.assertRaises(ValueError):self.bench.start('owner',True)
  with self.assertRaises(ValueError):self.bench.manual_jog('reverse','owner',True)
 def test_timeout_and_reapply_do_not_assume_success(self):
  self.bench.demo=False;self.bench._write=Mock()
  self.bench.update_timings(DEFAULTS);old=self.bench.timing_pending['revision']
  self.now+=2.1;self.bench.tick()
  self.assertEqual(self.bench.timing_status,'error')
  with self.assertRaises(ValueError):self.bench.start('owner',True)
  new={**DEFAULTS,'ir1_delay_ms':6500};self.bench.update_timings(new)
  revision=self.bench.timing_pending['revision']
  self.bench.line(f'TIMING:{old}:5500:3000:2000')
  self.assertEqual(self.bench.timing_status,'pending')
  self.bench.line(f'TIMING:{revision}:6500:3000:2000')
  self.assertEqual(self.bench.timing_status,'confirmed')
 def test_invalid_ranges_types_and_units(self):
  for value in (-500,1,250,25500,True,1.0,'500',None):
   with self.subTest(value=value):
    with self.assertRaises(ValueError):self.bench.validate_timings({**DEFAULTS,'ir1_delay_ms':value})
  for values in ({},None,{**DEFAULTS,'unknown':1},{**DEFAULTS,'robot_wait_ms':30500}):
   with self.assertRaises(ValueError):self.bench.validate_timings(values)
  self.assertEqual(self.bench.validate_timings({'ir1_delay_ms':0,'robot_wait_ms':0,'ir2_delay_ms':0}),{'ir1_delay_ms':0,'robot_wait_ms':0,'ir2_delay_ms':0})
 def test_invalid_or_rejected_ack_blocks_start(self):
  self.bench.line('TIMING:0:not-valid:3000:2000')
  self.assertEqual(self.bench.timing_status,'error')
  with self.assertRaises(ValueError):self.bench.start('owner',True)
  self.bench.timing_status='confirmed';self.bench.update_timings(DEFAULTS)
  self.bench.line('ERR:TIMING_RANGE');self.assertEqual(self.bench.timing_status,'error')
 def test_old_firmware_rejected(self):
  self.bench.line('READY V4_QR_IR A=FULL_CYCLE Q=QR_INFO S=STOP_BOTH')
  self.bench.line('STOPPED:BOTH_READY_A')
  with self.assertRaises(ValueError):self.bench.start('owner',True)
 def test_second_sensor_must_be_clear_before_start(self):
  self.bench.line('IR2:DETECTED')
  with self.assertRaises(ValueError):self.bench.start('owner',True)
 def test_out_of_order_restart_and_final_stop_are_rejected(self):
  self.start();self.bench.line('SIM:BELT_RESTARTED')
  self.assertEqual(self.bench.state,'ERROR')
 def test_first_stop_does_not_count_as_final_or_start_db(self):
  self.start();self.at(7.31);self.db.process_once()
  self.assertFalse(self.bench.trial['belt_stopped']);self.assertFalse(self.bench.trial['machine_done'])
  self.assertFalse(DatabaseWorker.eligible(self.bench.snapshot()))
  with self.assertRaises(ValueError):self.bench.confirm_ejection('owner')
 def test_db_only_runs_after_ir2_and_cycle_complete(self):
  self.start();self.at(12.81);self.db.process_once();self.assertNotEqual(self.db.snapshot()['status'],'success')
  self.at(12.91);self.bench.demo=False;self.db.connect=Mock(return_value=Mock())
  with patch('database.assign_next_order') as assign,patch('database.lookup',return_value={'pallet_id':'PALLET-06'}):
   self.db.process_once();self.db.process_once();assign.assert_called_once_with(self.db.connect.return_value,'PALLET-06')
 def test_missing_qr_finishes_motion_but_blocks_success(self):
  self.start(False);self.at(13)
  self.assertEqual(self.bench.state,'QR_UNCONFIRMED');self.assertTrue(self.bench.trial['machine_done'])
  self.db.process_once();self.assertEqual(self.db.snapshot()['status'],'blocked')
 def test_stop_during_wait_prevents_later_restart(self):
  self.start();self.at(7.31);self.bench.abort('operator',manual=True);self.at(20)
  self.assertEqual(self.bench.state,'STOPPED');self.assertFalse(self.bench.trial['sim_resumed'])
 def test_ir2_fault_is_not_a_success(self):
  self.start();self.at(10.31);self.bench.line('ERR:IR2_NOT_CLEAR_AT_RESTART')
  self.assertEqual(self.bench.state,'ERROR');self.assertFalse(DatabaseWorker.eligible(self.bench.snapshot()))
 def test_zero_delays_still_require_sensor_order(self):
  self.bench.update_timings({'ir1_delay_ms':0,'robot_wait_ms':0,'ir2_delay_ms':0});self.bench.tick()
  self.start();self.at(1.81)
  self.assertTrue(self.bench.trial['first_belt_stopped']);self.assertTrue(self.bench.trial['sim_resumed'])
  self.assertFalse(self.bench.trial['ir2_triggered'])
  self.at(2.31);self.assertTrue(self.bench.trial['belt_stopped']);self.assertFalse(self.bench.trial['machine_done'])
  self.at(2.61);self.assertEqual(self.bench.state,'AWAIT_CONFIRM')
 def test_reboot_requires_new_timing_confirmation(self):
  self.bench.line(READY)
  self.assertEqual(self.bench.timing_status,'waiting')
  self.bench.line('IR:CLEAR');self.bench.line('IR2:CLEAR')
  with self.assertRaises(ValueError):self.bench.start('owner',True)
  self.bench.line('TIMING:0:5500:3000:2000')
  self.assertEqual(self.bench.timing_status,'confirmed')
 def test_api_csrf_and_config_payload(self):
  app=create_app(self.bench,self.db);client=app.test_client()
  self.assertEqual(client.post('/api/timings',json={'values':DEFAULTS}).status_code,403)
  headers={'X-Test-Token':app.config['CSRF_TOKEN']}
  self.assertEqual(client.post('/api/timings',headers=headers,json={'values':{**DEFAULTS,'ir1_delay_ms':6000}}).status_code,200)
  self.assertEqual(client.get('/api/state').get_json()['timings']['status'],'pending')
  self.bench.tick();self.assertEqual(client.get('/api/state').get_json()['timings']['values']['ir1_delay_ms'],6000)
 def test_config_update_never_sends_motion_commands(self):
  self.bench.update_timings(DEFAULTS);self.bench.tick()
  commands=[e['command'] for e in self.bench.events if e['event']=='DEMO_COMMAND']
  self.assertEqual(len(commands),1);self.assertTrue(commands[0].startswith('T '))

class IntegrationTests(unittest.TestCase):
 def test_camera_mjpeg_and_short_configured_cycle(self):
  with tempfile.TemporaryDirectory() as temp:
   bench=Bench(Path(temp),demo=True);db=DatabaseWorker(bench)
   threads=[threading.Thread(target=camera_worker,args=(bench,0),daemon=True),threading.Thread(target=monitor_worker,args=(bench,),daemon=True),threading.Thread(target=db.run,daemon=True)]
   def wait(predicate):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
     if predicate():return
     bench.heartbeat('integration');time.sleep(.02)
    self.fail(str(bench.snapshot()))
   try:
    for t in threads:t.start()
    wait(lambda:bench.clear_frames>=3);bench.connect('DEMO')
    bench.update_timings({'ir1_delay_ms':500,'robot_wait_ms':500,'ir2_delay_ms':500});wait(lambda:bench.timing_status=='confirmed')
    bench.start('integration',True);wait(lambda:bench.state in {'AWAIT_CONFIRM','ERROR','QR_UNCONFIRMED'})
    self.assertEqual(bench.state,'AWAIT_CONFIRM');self.assertTrue(bench.trial['ir2_triggered'])
    wait(lambda:db.snapshot()['status']=='success')
    response=create_app(bench,db).test_client().get('/api/stream.mjpg',buffered=False)
    try:
     chunk=next(iter(response.response));jpg=chunk.split(b'\r\n\r\n',1)[1][:-2]
     self.assertEqual(cv2.imdecode(np.frombuffer(jpg,np.uint8),1).shape[:2],(480,640))
    finally:response.close()
    bench.confirm_ejection('integration');self.assertEqual(bench.state,'SUCCESS')
   finally:
    bench.shutdown()
    for t in threads:t.join(timeout=3)
    self.assertFalse(any(t.is_alive() for t in threads))

if __name__=='__main__':unittest.main()
