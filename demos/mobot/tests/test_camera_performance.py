import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from factory_v1.vision import PcVision


class CameraPerformanceTests(unittest.TestCase):
    def vision(self):
        return PcVision(SimpleNamespace(config={'V1_SETTINGS':{'camera_profiles':{'cam1':{'width':640,'height':480,'fps':90,'fourcc':'MJPG'}}},'V1_MODE':'HARDWARE'}))

    def test_driver_format_is_applied_after_rate_and_size(self):
        vision=self.vision();calls=[];reads=[]
        class Capture:
            def set(self,key,value):calls.append((key,value));return True
            def isOpened(self):return True
            def read(self):
                reads.append(True)
                if len(reads)==3:vision.closed.set()
                return True,np.zeros((12,16,3),np.uint8)
            def release(self):pass
        # Real DirectShow resets MJPEG to YUY2 if FPS is set after the codec.
        with patch('cv2.VideoCapture',return_value=Capture()):vision.capture('cam1',0)
        self.assertEqual(calls[-2:],[(cv2.CAP_PROP_FPS,90),(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))])
        self.assertEqual(len(reads),3)

    def test_jpeg_is_encoded_once_per_unique_frame_and_never_served_stale(self):
        vision=self.vision();frame=np.zeros((12,16,3),np.uint8)
        source=[(frame,'frame-one','2026-11-21T00:00:00.001Z')]
        vision.frame_data=lambda camera:source[0]
        with patch('cv2.imencode',wraps=cv2.imencode) as encode:
            first=vision.jpeg('cam1');self.assertEqual(vision.jpeg('cam1'),first);self.assertEqual(encode.call_count,1)
            source[0]=(frame,'frame-two','2026-11-21T00:00:00.012Z');vision.jpeg('cam1');self.assertEqual(encode.call_count,2)
            source[0]=(None,None,None);self.assertEqual(vision.jpeg('cam1'),(None,None));self.assertEqual(encode.call_count,2)

    def test_station_arrival_excludes_even_a_recent_prearrival_frame(self):
        vision=self.vision()
        image=np.zeros((12,16,3),np.uint8)
        for cam in ('cam1','cam2','cam3'):
            with patch('factory_v1.vision.time.monotonic',return_value=20.5):
                vision.frames[cam]=(19.98,image,'before','2026-11-24T02:00:00Z')
                self.assertIsNone(vision.process_frame(cam,20.0))
                vision.frames[cam]=(20.01,image,'after','2026-11-24T02:00:01Z')
                self.assertEqual(vision.process_frame(cam,20.0)[1],'after')


if __name__=='__main__':unittest.main()
