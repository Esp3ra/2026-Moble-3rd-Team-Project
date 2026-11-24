import collections,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from app import serial_reconnect_worker
from controller import Bench,READY


def port(device='COM3',serial='UNO-A',location='USB-1',vid=0x1A86,pid=0x7523):
 return {'device':device,'description':'USB-SERIAL CH340','serial_number':serial,'location':location,'vid':vid,'pid':pid}

class FakeSerial:
 def __init__(self,factory,name):
  self.factory=factory;self.name=name;self.closed=False;self.buffer=bytearray();self.writes=[]
  self.respond=True
  self.queue((factory.banner+'\n' if factory.banner else '')+'MANUAL_JOG_MS:1000\nTIMING:0:5500:3000:2000\nIR:CLEAR\nIR2:CLEAR\n')
  factory.devices.append(self);factory.handles+=1;factory.max_handles=max(factory.max_handles,factory.handles)
 def queue(self,text):
  with self.factory.lock:self.buffer.extend(text.encode())
 def read(self,count):
  deadline=time.monotonic()+.03
  while time.monotonic()<deadline:
   if self.closed or not any(p['device']==self.name for p in self.factory.ports):raise OSError('USB unplugged')
   with self.factory.lock:
    if self.buffer:
     data=bytes(self.buffer[:count]);del self.buffer[:count];return data
   time.sleep(.002)
  return b''
 def write(self,data):
  if self.closed or not any(p['device']==self.name for p in self.factory.ports):raise OSError('USB disconnected')
  command=data.decode().strip();self.writes.append(command)
  if self.respond:
   if command=='s':self.queue('STOPPED:BOTH_READY_A\n')
   elif command=='?':self.queue('TIMING:0:5500:3000:2000\n')
   elif command=='a':self.queue('ACTUATOR:PUSH_10000MS\n')
  return len(data)
 def close(self):
  with self.factory.lock:
   if not self.closed:self.factory.handles-=1
   self.closed=True

class Factory:
 def __init__(self):
  self.lock=threading.Lock();self.ports=[port()];self.devices=[];self.busy=False
  self.handles=self.max_handles=0;self.attempts=0;self.banner=READY
  self.open_enter=None;self.open_resume=None
 def __call__(self,name,*args,**kwargs):
  self.attempts+=1
  if self.open_enter:self.open_enter.set();self.open_resume.wait(3)
  if self.busy:raise OSError('port occupied')
  if not any(p['device']==name for p in self.ports):raise OSError('port absent')
  return FakeSerial(self,name)

class SerialReconnectTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.bench=Bench(Path(self.temp.name),demo=False)
  self.factory=Factory()
  self.patch=patch('serial.Serial',side_effect=self.factory);self.patch.start();self.addCleanup(self.patch.stop)
  self.ports_patch=patch.object(self.bench,'_port_records',side_effect=lambda:[p.copy() for p in self.factory.ports]);self.ports_patch.start();self.addCleanup(self.ports_patch.stop)
  self.worker=None
  self.addCleanup(self.stop)
 def stop(self):
  self.bench.shutdown()
  if self.worker:self.worker.join(timeout=2);self.assertFalse(self.worker.is_alive())
  if self.bench._serial_reader:self.bench._serial_reader.join(timeout=2);self.assertFalse(self.bench._serial_reader.is_alive())
  self.assertEqual(self.factory.handles,0)
 def wait(self,predicate,timeout=3):
  deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   if predicate():return
   time.sleep(.005)
  self.fail(str(self.bench.snapshot()))
 def start_worker(self):
  self.worker=threading.Thread(target=serial_reconnect_worker,args=(self.bench,.02),daemon=True);self.worker.start()
 def connected(self):return self.bench.connected and self.bench.ready and self.bench.protocol_ok and self.bench.timing_status=='confirmed' and self.bench.ir_clear and self.bench.ir2_clear
 def initial(self):self.bench.connect('COM3');self.wait(self.connected);self.start_worker()
 def test_no_automatic_open_before_user_selects_board(self):
  self.start_worker();time.sleep(.07)
  self.assertEqual(self.factory.attempts,0)
 def test_connect_requires_banner_stop_ack_and_timing(self):
  self.bench.connect('COM3');self.wait(self.connected)
  self.assertEqual(self.bench.serial_port,'COM3')
  self.assertFalse(self.bench.serial_stop_pending)
  self.assertEqual(self.factory.devices[0].writes[:2],['s','s'])
 def test_unplug_then_replug_same_port_without_server_restart(self):
  self.initial();old=self.bench.serial
  self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.assertTrue(self.bench.serial_reconnecting);self.assertFalse(self.bench.protocol_ok)
  self.factory.ports=[port()];self.wait(self.connected)
  self.assertIsNot(self.bench.serial,old);self.assertTrue(old.closed)
  self.assertEqual(self.factory.max_handles,1)
  self.assertFalse(any(c in ('a','q','j','k') for d in self.factory.devices for c in d.writes))
 def test_changed_com_port_follows_same_usb_serial_number(self):
  self.initial();self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.factory.ports=[port('COM7',location='USB-9')];self.wait(lambda:self.connected() and self.bench.serial_port=='COM7')
  self.assertEqual(self.factory.max_handles,1)
 def test_no_serial_number_uses_same_usb_location(self):
  self.factory.ports=[port(serial=None)];self.initial()
  self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.factory.ports=[port('COM8',serial=None)];self.wait(lambda:self.connected() and self.bench.serial_port=='COM8')
 def test_different_board_on_same_com_is_not_opened(self):
  self.initial();self.factory.ports=[port(serial='OTHER')]
  self.wait(lambda:not self.bench.connected);time.sleep(.07)
  self.assertEqual(len(self.factory.devices),1)
  self.assertTrue(self.bench.serial_auto_enabled)
 def test_ambiguous_identical_boards_are_not_guessed(self):
  self.initial();self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.factory.ports=[port('COM7'),port('COM8')];time.sleep(.07)
  self.assertEqual(len(self.factory.devices),1)
  self.assertFalse(self.bench.connected)
 def test_manual_disconnect_cancels_automatic_reconnect(self):
  self.initial();self.bench.disconnect();time.sleep(.08)
  self.assertFalse(self.bench.serial_auto_enabled)
  self.assertEqual(len(self.factory.devices),1)
 def test_disconnect_while_researching_does_not_reopen(self):
  self.initial();self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.bench.disconnect();self.factory.ports=[port()];time.sleep(.08)
  self.assertFalse(self.bench.connected);self.assertEqual(len(self.factory.devices),1)
 def test_initial_port_busy_is_retried_after_it_becomes_available(self):
  self.factory.busy=True
  with self.assertRaises(OSError):self.bench.connect('COM3')
  self.assertTrue(self.bench.serial_auto_enabled)
  self.start_worker();self.factory.busy=False;self.wait(self.connected)
 def test_active_cycle_is_interrupted_and_never_resumed(self):
  self.initial()
  self.wait(lambda:self.bench.snapshot()['jog_available'])
  self.bench.camera_ok=True;self.bench.camera_at=self.bench.clock();self.bench.clear_frames=3
  self.bench.start('owner',True);self.wait(lambda:self.bench.trial['acked'])
  self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.assertEqual(self.bench.state,'ERROR')
  self.factory.ports=[port('COM7')];self.wait(self.connected)
  self.assertEqual(self.bench.state,'ERROR')
  self.assertFalse(self.bench.trial['machine_done'])
  self.assertEqual(sum(c=='a' for d in self.factory.devices for c in d.writes),1)
 def test_reboot_loads_board_timing_instead_of_assuming_old_values(self):
  self.initial();self.bench.timing_values['ir1_delay_ms']=6500
  self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.factory.ports=[port()];self.wait(self.connected)
  self.assertEqual(self.bench.timing_values['ir1_delay_ms'],5500)
 def test_stale_reader_messages_are_ignored(self):
  self.initial();old=self.bench.serial;old_generation=self.bench._serial_generation
  self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.factory.ports=[port('COM7')];self.wait(self.connected)
  self.bench._serial_lost(old,old_generation,'late failure')
  self.assertTrue(self.connected());self.assertEqual(self.bench.serial_port,'COM7')
 def test_missing_ready_causes_close_and_retry(self):
  self.factory.banner='';self.bench.connect('COM3')
  self.wait(lambda:self.bench.serial is not None)
  self.assertFalse(self.bench.ready)
  with self.bench.lock:self.bench.ready_deadline=self.bench.clock()-1
  self.factory.banner=READY;self.start_worker()
  self.wait(lambda:len(self.factory.devices)>=2 and self.connected())
  self.assertTrue(self.factory.devices[0].closed)
 def test_wrong_firmware_stops_automatic_reopen_loop(self):
  self.factory.banner='READY V4_QR_IR A=FULL_CYCLE Q=QR_INFO S=STOP_BOTH'
  self.bench.connect('COM3');self.wait(lambda:not self.bench.serial_auto_enabled)
  self.start_worker();time.sleep(.07)
  self.assertFalse(self.bench.protocol_ok);self.assertEqual(len(self.factory.devices),1)
 def test_silent_device_is_probed_and_reconnected(self):
  self.initial();device=self.bench.serial
  with self.factory.lock:device.respond=False;device.buffer.clear()
  with self.bench.lock:self.bench._serial_last_seen=self.bench.clock()-7
  self.wait(lambda:len(self.factory.devices)>=2 and self.connected())
  self.assertTrue(device.closed)
 def test_read_only_probe_does_not_start_motors(self):
  self.initial()
  with self.bench.lock:self.bench._serial_last_probe=self.bench.clock()-3
  self.wait(lambda:'?' in self.factory.devices[0].writes)
  self.assertFalse(any(c in ('a','q','j','k') for d in self.factory.devices for c in d.writes))
 def test_manual_cancel_during_slow_open_closes_late_handle(self):
  self.factory.open_enter=threading.Event();self.factory.open_resume=threading.Event()
  task=threading.Thread(target=self.bench.connect,args=('COM3',));task.start()
  self.assertTrue(self.factory.open_enter.wait(1))
  self.bench.disconnect();self.factory.open_resume.set();task.join(timeout=2)
  self.assertFalse(task.is_alive());self.assertFalse(self.bench.connected)
  self.assertEqual(self.factory.handles,0)
 def test_shutdown_prevents_future_retry(self):
  self.initial();self.factory.ports=[];self.wait(lambda:not self.bench.connected)
  self.bench.shutdown();attempts=self.factory.attempts
  self.factory.ports=[port()];time.sleep(.06)
  self.assertEqual(self.factory.attempts,attempts)

if __name__=='__main__':unittest.main()
