"""Four PC-owned cameras; hardware inference/calibration requires approved plugins."""
import importlib
import threading
import cv2
import numpy as np
from .common import *
from .qr_roi import QrRoi


class PcVision:
    def __init__(self,app,simulation=False):
        self.app,self.simulation=app,simulation
        self.config=app.config['V1_SETTINGS'];self.closed=threading.Event();self.lock=threading.RLock()
        self.frames={};self.captures={};self.images={};self.qr_inputs=[];self.inspection_override=None
        self.jpeg_cache={};self.jpeg_locks={camera:threading.Lock() for camera in ('cam1','cam2','cam3','cam4')}
        try:self.plugin=importlib.import_module(self.config['vision_module']) if self.config.get('vision_module') else None
        except ImportError:self.plugin=None
        self.profile=self.config.get('calibration_profile',{})
        self.qr=QrRoi(self,Path(app.config.get('ORDER_DB','unused.sqlite3')).parent/'qr-roi.json')

    def start(self):
        if self.simulation:return
        self.qr.start()
        grouped={}
        for camera,index in self.config.get('camera_indices',{}).items():
            if camera in ('cam1','cam2','cam3','cam4') and type(index) is int:
                grouped.setdefault(index,[]).append(camera)
        for index,cameras in grouped.items():
            cameras.sort()
            threading.Thread(target=self.capture,args=(cameras[0],index,tuple(cameras[1:])),daemon=True).start()

    def capture(self,camera,index,aliases=()):
        while not self.closed.is_set():
            cap=cv2.VideoCapture(index,cv2.CAP_DSHOW) if __import__('os').name=='nt' else cv2.VideoCapture(index)
            try:
                defaults=dict(width=1920,height=1080,fps=30,fourcc='MJPG') if camera=='cam1' else {}
                profile={**defaults,**self.config.get('camera_profiles',{}).get(camera,{})}
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,profile.get('width',640));cap.set(cv2.CAP_PROP_FRAME_HEIGHT,profile.get('height',480))
                if profile.get('fps'):cap.set(cv2.CAP_PROP_FPS,profile['fps'])
                # DirectShow renegotiates the format when FPS/size change. Apply codec last.
                if profile.get('fourcc'):cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*profile['fourcc']))
                while not self.closed.is_set() and cap.isOpened():
                    acquisition_started=time.monotonic()
                    ok,frame=cap.read()
                    if not ok:break
                    sample=(acquisition_started,frame.copy(),uid(),now())
                    with self.lock:
                        for name in (camera,*aliases):self.frames[name]=sample
                    if camera=='cam1':self.qr.capture_tick()
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
        # One JPEG encoding per captured frame, including concurrent WPF viewers.
        with self.jpeg_locks[camera]:
            frame,frame_id,captured=self.frame_data(camera)
            if frame is None:return None,None
            cached=self.jpeg_cache.get(camera)
            if cached and cached[0]==frame_id:return cached[1],cached[2]
            ok,encoded=cv2.imencode('.jpg',frame)
            if not ok:return None,None
            data=encoded.tobytes();self.jpeg_cache[camera]=(frame_id,data,captured)
            return data,captured

    def ready(self):
        if self.simulation:return dict.fromkeys(('cam1','cam2','cam3','cam4'),True)
        with self.lock:
            now_monotonic=time.monotonic()
            return {camera:camera in self.frames and now_monotonic-self.frames[camera][0]<2
                    for camera in ('cam1','cam2','cam3','cam4')}

    def assembly_ready(self):
        if self.simulation:return True
        profile=self.profile
        calibrated=profile.get('approved') is True and isinstance(profile.get('calibration_id'),str) and bool(profile.get('pose_max_age_ms')) and set(profile.get('bounds',{}))=={'x_mm','y_mm','z_mm','rx_deg','ry_deg','rz_deg'}
        return self.ready()['cam2'] and bool(self.plugin and calibrated and hasattr(self.plugin,'locate_assembly'))

    @property
    def inspection_camera(self):
        camera=self.config.get('inspection_camera')
        return 'cam3' if camera=='cam3' else None

    def inspection_ready(self):
        if self.simulation:return True
        return bool(self.inspection_camera and self.plugin and self.config.get('vision_profile_approved') is True
                    and hasattr(self.plugin,'inspect_product') and self.ready()[self.inspection_camera])

    def read_feed_qr(self,cycle,read_id,attempt,sample=None):
        if self.simulation:
            if self.qr_inputs:pallet=self.qr_inputs.pop(0)
            else:
                with self.app.app_context(),transaction() as db:
                    row=db.execute("SELECT pallet_id FROM V1_PALLETS WHERE stage='READY' AND cycle_id IS NULL ORDER BY pallet_id LIMIT 1").fetchone()
                    pallet=row[0] if row else None
            return dict(frame_id=uid(),pallet_id=pallet,attempt=attempt,read_id=read_id)
        # Process reads the current frame through the same ROI/CLAHE decoder.
        # Live preview results never trigger binding or motor commands.
        result=self.qr.read_now(sample) if sample is not None else self.qr.read_now()
        return dict(frame_id=result['frame_id'],pallet_id=result['pallet_id'],attempt=attempt,read_id=read_id)

    @property
    def part_order(self):
        if self.simulation:return ['HEAD','LEFT','RIGHT']
        return self.profile.get('part_order') if self.profile.get('approved') is True else None

    def process_frame(self,camera,after):
        if self.simulation:return self.frame_data(camera)
        with self.lock:
            frame=self.frames.get(camera)
            if frame and frame[0]>after and time.monotonic()-frame[0]<2:
                return frame[1].copy(),frame[2],frame[3]
        return None

    def locate_assembly(self,cycle,specification,sample=None):
        frame,frame_id,captured=sample if sample is not None else self.frame_data('cam2')
        require(frame is not None,'CAM2 촬영 대기',503,'DEVICE_UNAVAILABLE')
        if self.simulation:
            pose=dict(frame='ROBOT_BASE',tool_frame='GRIP_CENTER',calibration_id='SIMULATION_ONLY',
                      x_mm=0,y_mm=0,z_mm=0,rx_deg=0,ry_deg=0,rz_deg=0,captured_at=captured)
            return dict(spec=specification,points={k:pose.copy() for k in ('head_pick','head_place','left_pick','left_place','right_pick','right_place')})
        require(self.assembly_ready(),'승인된 좌표 보정과 카메라 2 준비가 필요합니다.',503,'DEVICE_UNAVAILABLE')
        poses=self.plugin.locate_assembly(cycle_id=cycle,spec=specification,calibration_id=self.profile['calibration_id'],camera_id='cam2',frame=frame,frame_id=frame_id,captured_at=captured)
        from .contracts import pose_set
        pose_set(poses)
        require(poses['spec']==specification and all(p['captured_at']==captured for p in poses['points'].values()),'CAM2 주문 사양·프레임 시각 불일치',422,'STALE_CALIBRATION')
        return poses

    def validate_poses(self,poses):
        if self.simulation:return
        profile=self.profile
        require(profile.get('approved') is True and profile.get('pose_max_age_ms') and profile.get('bounds'),'승인된 보정·작업 공간 프로필이 필요합니다.',422,'STALE_CALIBRATION')
        for pose in poses['points'].values():
            require(pose['calibration_id']==profile['calibration_id'],'보정 ID가 다릅니다.',422,'STALE_CALIBRATION')
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(pose['captured_at'].replace('Z','+00:00'))).total_seconds()*1000
            require(0<=age<=profile['pose_max_age_ms'],'좌표 유효 시간이 지났습니다.',422,'STALE_CALIBRATION')
            for key,limits in profile['bounds'].items():require(limits[0]<=pose[key]<=limits[1],'승인 작업 공간 밖의 좌표입니다.',422,'STALE_CALIBRATION')

    def inspect_product(self,cycle,inspection,specification,pallet,sample=None):
        camera=self.inspection_camera or ('cam3' if self.simulation else None)
        if camera is None:return None
        frame,frame_id,captured=sample if sample is not None else self.frame_data(camera)
        if frame is None:return None
        if self.simulation:
            data=dict(inspection_id=inspection,cycle_id=cycle,frame_id=frame_id,captured_at=captured,image_ref=uid(),model_version='SIMULATION_ONLY',calibration_id='SIMULATION_ONLY',qr_read=pallet,
                head=dict(observation='SEEN',observed_id=specification['target_head_id'],confidence=1),
                left=dict(observation='SEEN',observed_id=specification['target_arm_l_id'],confidence=1),
                right=dict(observation='SEEN',observed_id=specification['target_arm_r_id'],confidence=1))
            if self.inspection_override:self.inspection_override(data)
        else:
            require(self.inspection_ready(),'승인된 검사 카메라와 비전 프로필이 필요합니다.',503,'DEVICE_UNAVAILABLE')
            data=self.plugin.inspect_product(cycle_id=cycle,inspection_id=inspection,camera_id=camera,frame=frame,frame_id=frame_id,captured_at=captured)
        require(data['frame_id']==frame_id and data['captured_at']==captured,'동일 프레임 검사 결과가 아닙니다.',409,'CONFLICT')
        with self.lock:self.images[data['image_ref']]=cv2.imencode('.jpg',frame)[1].tobytes()
        return data

    def inspection_image(self,image_ref):
        with self.lock:return self.images.get(image_ref)

    def release_image(self,image_ref):
        with self.lock:self.images.pop(image_ref,None)

    def close(self):self.closed.set();self.qr.close()
