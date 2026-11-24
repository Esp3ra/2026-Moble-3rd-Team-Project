"""Independent real QR decoding; no orders, binding or motor command writes."""
import hashlib
import json
import os
import unittest
from contextlib import closing
from unittest.mock import patch

import cv2
import numpy as np
import test_factory_v1 as fixtures
from app import create_app
from factory_v1.common import now
from orders import connect


class WirelessQrTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.FactoryTests('test_stock_shared_and_public_privacy');self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.case.engine.close()
        config=self.case.path.parent/'wireless-test.json'
        config.write_text(json.dumps(dict(enabled=True,token_hash=hashlib.sha256(b'qr-test').hexdigest())))
        with patch.dict(os.environ,{'MOBOT_DB':str(self.case.path),'MOBOT_V1_MODE':'SIMULATION','MOBOT_V1_BACKGROUND':'0'}):self.app=create_app()
        self.engine=self.app.extensions['factory_v1'];self.addCleanup(self.engine.close)
        self.engine.vision.simulation=False
        self.client=self.app.test_client()

    @staticmethod
    def image(value):
        qr=cv2.QRCodeEncoder_create().encode(value)
        qr=cv2.copyMakeBorder(qr,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
        return cv2.cvtColor(cv2.resize(qr,(330,330),interpolation=cv2.INTER_NEAREST),cv2.COLOR_GRAY2BGR)

    def read(self,image):
        self.engine.vision.frame_data=lambda camera:(image,'test-frame' if image is not None else None,now() if image is not None else None)
        return self.client.post('/api/v1/wireless-test/qr-read',json={},headers=self.case.auth)

    def test_valid_invalid_missing_and_multiple_do_not_write_process_data(self):
        response=self.read(self.image('PALLET-01'));self.assertEqual(response.status_code,200,response.json)
        self.assertEqual((response.json['state'],response.json['pallet_id']),('READ','PALLET-01'))
        self.assertEqual(self.read(self.image('NOT-A-PALLET')).json['state'],'INVALID_QR')
        self.assertEqual(self.read(None).json['state'],'NO_FRAME')
        self.assertEqual(self.read(np.full((330,330,3),255,np.uint8)).json['state'],'UNREADABLE')
        pair=np.concatenate([self.image('PALLET-01'),self.image('PALLET-02')],axis=1)
        self.assertEqual(self.read(pair).json['state'],'MULTIPLE_QR')
        with closing(connect(self.case.path)) as db:
            for table in ('V1_ORDERS','V1_CYCLES','V1_COMMANDS','V1_HISTORY'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM V1_PALLETS WHERE stage='READY' AND cycle_id IS NULL").fetchone()[0],8)
        self.assertEqual(self.app.extensions['wireless_bench'].snapshot()['commands'],[])

    def test_auth_and_external_access_are_restricted(self):
        path='/api/v1/wireless-test/qr-read'
        self.assertEqual(self.client.post(path,json={}).status_code,401)
        self.assertEqual(self.client.post(path,json={},headers={**self.case.auth,'X-Forwarded-For':'8.8.8.8'}).status_code,403)
