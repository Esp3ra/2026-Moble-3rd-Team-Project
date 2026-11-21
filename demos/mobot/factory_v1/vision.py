"""Four PC-owned cameras; hardware inference/calibration requires approved plugins."""
import importlib
import threading
import cv2
import numpy as np
from .common import *


class PcVision:
    def __init__(self,app,simulation=False):
        self.app,self.simulation=app,simulation
        self.config=app.config['V1_SETTINGS'];self.closed=threading.Event();self.lock=threading.RLock()
        self.frames={};self.captures={};self.images={};self.qr_inputs=[];self.inspection_override=None
        try:self.plugin=importlib.import_module(self.config['vision_module']) if self.config.get('vision_module') else None
        except ImportError:self.plugin=None
        self.profile=self.config.get('calibration_profile',{})

    def start(self):
        if self.simulation:return
        for camera,index in self.config.get('camera_indices',{}).items():
            if camera in ('cam1','cam2','cam3','cam4') and type(index) is int:
                threading.Thread(target=self.capture,args=(camera,index),daemon=True).start()

    def capture(self,camera,index):
        while not self.closed.is_set():
            cap=cv2.VideoCapture(index,cv2.CAP_DSHOW) if __import__('os').name=='nt' else cv2.VideoCapture(index)
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,640);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
                while not self.closed.wait(.1) and cap.isOpened():
                    ok,frame=cap.read()
                    if not ok:break
                    with self.lock:self.frames[camera]=(time.monotonic(),frame.copy(),uid(),now())
            finally:cap.release()
            self.closed.wait(1)

    def frame_data(self,camera):
        if self.simulation:
            frame=np.full((480,640,3),235,np.uint8)
            cv2.putText(frame,'SIMULATED '+camera.upper(),(35,225),cv2.FONT_HERSHEY_SIMPLEX,.9,(55,65,90),2)
            return frame,uid(),now()
        with self.lock:
            value=self.frames.get(camera)
            if value and time.monotonic()-value[0]<2:return value[1].copy(),value[2],value[3]
        return None,None,None

    def frame(self,camera):
        return self.jpeg(camera)[0]

    def jpeg(self,camera):
        frame,_,captured=self.frame_data(camera)
        if frame is None:return None,None
        return cv2.imencode('.jpg',frame)[1].tobytes(),captured

    def ready(self):
        if self.simulation:return dict.fromkeys(('cam1','cam2','cam3','cam4'),True)
        result={camera:self.frame_data(camera)[0] is not None for camera in ('cam1','cam2','cam3','cam4')}
        profile=self.profile
        calibrated=profile.get('approved') is True and isinstance(profile.get('calibration_id'),str) and bool(profile.get('pose_max_age_ms')) and set(profile.get('bounds',{}))=={'x_mm','y_mm','z_mm','rx_deg','ry_deg','rz_deg'}
        result['cam2']=result['cam2'] and bool(self.plugin and calibrated and hasattr(self.plugin,'locate_assembly'))
        result['cam3']=result['cam3'] and bool(self.plugin and self.config.get('vision_profile_approved') is True and hasattr(self.plugin,'inspect_product'))
        return result

    def read_feed_qr(self,cycle,read_id,attempt):
        if self.simulation:
            if self.qr_inputs:pallet=self.qr_inputs.pop(0)
            else:
                with self.app.app_context(),transaction() as db:
                    row=db.execute("SELECT pallet_id FROM V1_PALLETS WHERE stage='READY' AND cycle_id IS NULL ORDER BY pallet_id LIMIT 1").fetchone()
                    pallet=row[0] if row else None
            return dict(frame_id=uid(),pallet_id=pallet,attempt=attempt,read_id=read_id)
        frame,frame_id,_=self.frame_data('cam1');pallet=None
        if frame is not None:
            from pallet_system_uno_sim.app import decode
            codes=decode(cv2.QRCodeDetector(),frame)
            if len(codes)==1 and isinstance(codes[0],str) and re.fullmatch(r'PALLET-0[1-8]',codes[0]):pallet=codes[0]
        return dict(frame_id=frame_id,pallet_id=pallet,attempt=attempt,read_id=read_id)

    def locate_assembly(self,cycle,specification):
        if self.simulation:
            pose=dict(frame='ROBOT_BASE',tool_frame='GRIP_CENTER',calibration_id='SIMULATION_ONLY',
                      x_mm=0,y_mm=0,z_mm=0,rx_deg=0,ry_deg=0,rz_deg=0,captured_at=now())
            return dict(spec=specification,points={k:pose.copy() for k in ('head_pick','head_place','left_pick','left_place','right_pick','right_place')})
        require(self.ready()['cam2'],'승인된 좌표 보정과 카메라 2 준비가 필요합니다.',503,'DEVICE_UNAVAILABLE')
        frame,frame_id,captured=self.frame_data('cam2')
        return self.plugin.locate_assembly(cycle_id=cycle,spec=specification,calibration_id=self.profile['calibration_id'],camera_id='cam2',frame=frame,frame_id=frame_id,captured_at=captured)

    def validate_poses(self,poses):
        if self.simulation:return
        profile=self.profile
        require(profile.get('approved') is True and profile.get('pose_max_age_ms') and profile.get('bounds'),'승인된 보정·작업 공간 프로필이 필요합니다.',422,'STALE_CALIBRATION')
        for pose in poses['points'].values():
            require(pose['calibration_id']==profile['calibration_id'],'보정 ID가 다릅니다.',422,'STALE_CALIBRATION')
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(pose['captured_at'].replace('Z','+00:00'))).total_seconds()*1000
            require(0<=age<=profile['pose_max_age_ms'],'좌표 유효 시간이 지났습니다.',422,'STALE_CALIBRATION')
            for key,limits in profile['bounds'].items():require(limits[0]<=pose[key]<=limits[1],'승인 작업 공간 밖의 좌표입니다.',422,'STALE_CALIBRATION')

    def inspect_product(self,cycle,inspection,specification,pallet):
        frame,frame_id,captured=self.frame_data('cam3')
        if frame is None:return None
        if self.simulation:
            data=dict(inspection_id=inspection,cycle_id=cycle,frame_id=frame_id,captured_at=captured,image_ref=uid(),model_version='SIMULATION_ONLY',calibration_id='SIMULATION_ONLY',qr_read=pallet,
                head=dict(observation='SEEN',observed_id=specification['target_head_id'],confidence=1),
                left=dict(observation='SEEN',observed_id=specification['target_arm_l_id'],confidence=1),
                right=dict(observation='SEEN',observed_id=specification['target_arm_r_id'],confidence=1))
            if self.inspection_override:self.inspection_override(data)
        else:
            require(self.ready()['cam3'],'승인된 비전 프로필이 필요합니다.',503,'DEVICE_UNAVAILABLE')
            data=self.plugin.inspect_product(cycle_id=cycle,inspection_id=inspection,camera_id='cam3',frame=frame,frame_id=frame_id,captured_at=captured)
        require(data['frame_id']==frame_id and data['captured_at']==captured,'동일 프레임 검사 결과가 아닙니다.',409,'CONFLICT')
        with self.lock:self.images[data['image_ref']]=cv2.imencode('.jpg',frame)[1].tobytes()
        return data

    def inspection_image(self,image_ref):
        with self.lock:return self.images.get(image_ref)

    def release_image(self,image_ref):
        with self.lock:self.images.pop(image_ref,None)

    def close(self):self.closed.set()
