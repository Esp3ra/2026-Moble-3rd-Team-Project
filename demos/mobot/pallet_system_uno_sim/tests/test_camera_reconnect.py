import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app import ROOT, camera_worker, create_app
from camera_pipeline import CameraFeed
from controller import Bench


class FakeCamera:
    def __init__(self, factory):
        self.factory=factory
        self.opened=factory.online.is_set()
        self.released=False
        factory.cameras.append(self)
        if self.opened:
            factory.open_handles+=1
            factory.max_handles=max(factory.max_handles,factory.open_handles)
    def isOpened(self): return self.opened and not self.released
    def set(self,*args):return True
    def read(self):
        time.sleep(.01)
        if not self.isOpened() or not self.factory.online.is_set():return False,None
        return True,np.full((480,640,3),180,np.uint8)
    def release(self):
        if self.opened and not self.released:self.factory.open_handles-=1
        self.released=True


class FakeCameraFactory:
    def __init__(self):
        self.online=threading.Event()
        self.cameras=[]
        self.open_handles=self.max_handles=0
    def __call__(self,*args):return FakeCamera(self)


class CameraReconnectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.bench=Bench(Path(self.temp.name),demo=True)
        self.bench.connect('DEMO')
        self.bench.demo=False
        self.bench._write=Mock()
        self.factory=FakeCameraFactory()
        self.worker=None

    def wait_until(self,predicate,timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.01)
        self.fail('Timed out waiting for camera state: '+str(self.bench.snapshot()))

    def start_worker(self):
        self.capture_patch=patch('camera_pipeline.cv2.VideoCapture',side_effect=self.factory)
        self.decode_patch=patch('app.decode',return_value=[])
        self.capture_patch.start();self.decode_patch.start()
        self.addCleanup(self.capture_patch.stop);self.addCleanup(self.decode_patch.stop)
        self.worker=threading.Thread(target=camera_worker,args=(self.bench,0,30,30,.03),daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        self.bench.shutdown()
        if self.worker:
            self.worker.join(timeout=3)
            self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.factory.open_handles,0)

    def test_absent_at_start_then_plugged_in_without_server_restart(self):
        self.start_worker()
        self.wait_until(lambda:self.bench.camera_reconnecting and len(self.factory.cameras)>=2)
        self.assertFalse(self.bench.snapshot()['camera_ok'])
        self.assertEqual(sum(e['event']=='CAMERA_ERROR' for e in self.bench.events),1)
        self.factory.online.set()
        self.wait_until(lambda:self.bench.snapshot()['camera_ok'] and self.bench.clear_frames>=3)
        self.assertFalse(self.bench.camera_reconnecting)
        self.assertEqual(self.bench.camera_error,'')
        self.assertTrue(any(e['event']=='CAMERA_RECONNECTED' for e in self.bench.events))
        self.bench._write.assert_not_called()

    def test_repeated_disconnect_reconnect_releases_previous_handle(self):
        self.factory.online.set();self.start_worker()
        self.wait_until(lambda:self.bench.camera_ok and self.bench.clear_frames>=3)
        for _ in range(2):
            self.factory.online.clear()
            self.wait_until(lambda:self.bench.camera_reconnecting)
            self.assertEqual(self.bench.clear_frames,0)
            self.assertEqual(self.bench.latest_codes,[])
            self.factory.online.set()
            self.wait_until(lambda:self.bench.camera_ok and self.bench.clear_frames>=3)
        self.assertEqual(self.factory.max_handles,1)
        self.assertEqual(sum(e['event']=='CAMERA_RECONNECTED' for e in self.bench.events),2)
        self.assertTrue(all(c.released for c in self.factory.cameras[:-1]))

    def test_disconnect_aborts_motion_and_reconnect_does_not_resume(self):
        self.factory.online.set();self.start_worker()
        self.wait_until(lambda:self.bench.camera_ok and self.bench.clear_frames>=3)
        self.bench.start('operator',True)
        self.bench.line('ACTUATOR:PUSH_10000MS')
        self.factory.online.clear()
        self.wait_until(lambda:self.bench.state=='ERROR')
        self.bench.line('STOPPED:BOTH_READY_A')
        self.factory.online.set()
        self.wait_until(lambda:self.bench.camera_ok and self.bench.clear_frames>=3)
        self.assertEqual(self.bench.state,'ERROR')
        self.assertFalse(self.bench.trial['qr_ok'])
        self.assertEqual([call.args[0] for call in self.bench._write.call_args_list],['a','s'])

    def test_existing_mjpeg_stream_receives_disconnect_notice_and_recovered_frame(self):
        self.factory.online.set();self.start_worker()
        self.wait_until(lambda:self.bench.camera_ok and self.bench.preview_jpeg is not None)
        response=create_app(self.bench).test_client().get('/api/stream.mjpg',buffered=False)
        self.addCleanup(response.close)
        iterator=iter(response.response)
        initial=next(iterator)
        original_jpeg=self.bench.preview_jpeg
        self.factory.online.clear()
        self.wait_until(lambda:self.bench.camera_reconnecting and self.bench.preview_jpeg!=original_jpeg)
        lost=next(iterator)
        self.assertNotEqual(initial,lost)
        self.factory.online.set()
        self.wait_until(lambda:self.bench.camera_ok)
        recovered=next(iterator)
        for chunk in (lost,recovered):
            jpg=chunk.split(b'\r\n\r\n',1)[1][:-2]
            self.assertEqual(cv2.imdecode(np.frombuffer(jpg,np.uint8),1).shape[:2],(480,640))

    def test_shutdown_interrupts_reconnect_wait(self):
        self.start_worker()
        self.wait_until(lambda:self.bench.camera_reconnecting)
        before=len(self.factory.cameras)
        self.stop_worker()
        time.sleep(.06)
        self.assertEqual(len(self.factory.cameras),before)

    def test_qr_decoded_during_failure_is_discarded(self):
        feed=CameraFeed(self.bench,0,ROOT)
        feed.latest=(np.zeros((100,100,3),np.uint8),self.bench.clock())
        feed.sequence=1
        entered=threading.Event();resume=threading.Event()
        def slow_decode(*args):
            entered.set();resume.wait(2)
            return ['PALLET-06']
        self.bench.frame=Mock()
        processor=threading.Thread(target=feed.process,args=(slow_decode,),daemon=True)
        processor.start()
        try:
            self.assertTrue(entered.wait(2))
            feed.fail(OSError('unplugged'))
        finally:
            resume.set();processor.join(timeout=3)
        self.assertFalse(processor.is_alive())
        self.bench.frame.assert_not_called()
        self.assertFalse(self.bench.camera_ok)
        self.assertEqual(self.bench.latest_codes,[])


if __name__=='__main__':unittest.main()
