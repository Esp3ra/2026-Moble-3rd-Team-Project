import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from contextlib import closing

import cv2
import numpy as np
import test_factory_v1 as fixtures
from factory_v1.common import now,uid
from factory_v1.qr_roi import QrRoi,decode_roi
from orders import connect


class QrRoiTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.FactoryTests('test_stock_shared_and_public_privacy');self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.vision=self.case.engine.vision;self.vision.simulation=False
        self.frame=None;self.frame_id=uid()
        self.vision.frame_data=lambda cam:(self.frame,self.frame_id,now()) if self.frame is not None else (None,None,None)
        self.client=self.case.client;self.auth=self.case.auth

    @staticmethod
    def qr(value):
        gray=cv2.QRCodeEncoder_create().encode(value)
        gray=cv2.copyMakeBorder(gray,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
        return cv2.cvtColor(cv2.resize(gray,(300,300),interpolation=cv2.INTER_NEAREST),cv2.COLOR_GRAY2BGR)

    def test_roi_decoder_used_by_live_and_process_without_preview_binding(self):
        self.frame=np.concatenate([self.qr('PALLET-01'),self.qr('PALLET-02')],axis=1)
        self.assertEqual(self.vision.qr.read_now()['state'],'MULTIPLE_QR')
        response=self.client.put('/api/v1/qr-roi/roi',json=dict(roi=[0,0,.5,1],expected_revision=0),headers=self.auth)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.vision.qr.read_now()['pallet_id'],'PALLET-01')
        live=self.client.get('/api/v1/qr-roi/status',headers=self.auth)
        self.assertEqual(live.json['pallet_id'],'PALLET-01')
        process=self.vision.read_feed_qr(uid(),uid(),1)
        self.assertEqual((process['pallet_id'],process['frame_id']),('PALLET-01',self.frame_id))
        with closing(connect(self.case.path)) as db:
            for table in ('V1_CYCLES','V1_COMMANDS','V1_ORDERS'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        image=self.client.get('/api/v1/qr-roi/frame',headers=self.auth)
        self.assertEqual(image.status_code,200);self.assertEqual(image.headers['Cache-Control'],'no-store')
        self.assertEqual(cv2.imdecode(np.frombuffer(image.data,np.uint8),1).shape[:2],(300,300))
        self.assertTrue(image.headers['X-Frame-Id'].endswith(':1'))
        restored=QrRoi(self.vision,self.vision.qr.path);self.assertEqual(restored.roi,[0,0,.5,1])

    def test_auth_revision_validation_and_disconnect_clear(self):
        self.assertEqual(self.client.get('/api/v1/qr-roi/status').status_code,401)
        self.assertEqual(self.client.get('/api/v1/qr-roi/status',headers={**self.auth,'X-Forwarded-For':'8.8.8.8'}).status_code,403)
        for value in ([0,0,0,1],[0,0,2,1],[True,0,1,1]):
            self.assertEqual(self.client.put('/api/v1/qr-roi/roi',json=dict(roi=value,expected_revision=0),headers=self.auth).status_code,400)
        self.assertEqual(self.client.put('/api/v1/qr-roi/roi',json=dict(roi=[0,0,1,1],expected_revision=99),headers=self.auth).status_code,409)
        self.frame=self.qr('PALLET-01');self.vision.qr.read_now();self.frame=None
        self.assertIsNone(self.vision.qr.status()['pallet_id'])
        self.assertEqual(self.client.get('/api/v1/qr-roi/frame',headers=self.auth).status_code,503)

    def test_roi_change_during_decode_discards_old_result(self):
        self.frame=self.qr('PALLET-01')
        def changed(*args):
            self.vision.qr.set_roi([.1,.1,.8,.8],0)
            return 'READ',['PALLET-01'],'ORIGINAL'
        with patch('factory_v1.qr_roi.decode_roi',side_effect=changed):result=self.vision.qr.read_now()
        self.assertIsNone(result['pallet_id']);self.assertEqual(result['state'],'WAITING')

    def test_clahe_runs_only_after_original_failure(self):
        detector=Mock();clahe=Mock();clahe.apply.return_value=np.zeros((50,50),np.uint8)
        crop=np.zeros((50,50,3),np.uint8)
        detector.detectAndDecodeMulti.return_value=(True,['PALLET-01'],np.zeros((1,4,2)),None)
        self.assertEqual(decode_roi(detector,crop,clahe)[2],'ORIGINAL');clahe.apply.assert_not_called()
        detector.detectAndDecodeMulti.side_effect=[(False,[],None,None),(True,['PALLET-02'],np.zeros((1,4,2)),None)]
        detector.detectAndDecode.return_value=('',None,None)
        self.assertEqual(decode_roi(detector,crop,clahe),('READ',['PALLET-02'],'CLAHE'));clahe.apply.assert_called_once()

    def test_active_cycle_blocks_roi_changes(self):
        # Prepare a real simulated active cycle first; no physical adapter used.
        self.vision.simulation=True
        order,_=self.case.order();self.case.accept(order);self.case.start()
        result=self.client.put('/api/v1/qr-roi/roi',json=dict(roi=[0,0,.5,.5],expected_revision=0),headers=self.auth)
        self.assertEqual(result.status_code,409);self.assertEqual(self.vision.qr.revision,0)
