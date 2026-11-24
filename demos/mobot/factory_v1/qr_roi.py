"""CAM1 ROI + original-first CLAHE QR reader. Camera ownership stays in PcVision."""
import json
import math
import re
import threading
import time

import cv2
from .common import require


def validate_roi(value):
    require(isinstance(value,list) and len(value)==4 and all(type(n) in (int,float) and math.isfinite(n) for n in value),'ROI 형식 오류')
    x,y,w,h=value
    require(min(x,y)>=0 and min(w,h)>=.01 and x+w<=1.000001 and y+h<=1.000001,'영상 안의 ROI를 지정하세요.')
    return list(value)


def crop_roi(frame,roi):
    h,w=frame.shape[:2];x,y,rw,rh=roi
    x1,y1=min(w-1,int(x*w)),min(h-1,int(y*h))
    x2,y2=max(x1+1,min(w,round((x+rw)*w))),max(y1+1,min(h,round((y+rh)*h)))
    return frame[y1:y2,x1:x2].copy()


def decode_candidate(detector,image):
    try:
        ok,values,points,_=detector.detectAndDecodeMulti(image)
        texts=[v for v in values if v] if ok else []
        if points is None:
            value,_,_=detector.detectAndDecode(image);texts=[value] if value else []
        count=len(points) if points is not None else len(texts)
        return ('MULTIPLE_QR' if count>1 else 'READ' if len(texts)==1 else 'UNREADABLE'),texts
    except cv2.error:return 'UNREADABLE',[]


def decode_roi(detector,crop,clahe):
    state,texts=decode_candidate(detector,crop)
    if state!='UNREADABLE':return state,texts,'ORIGINAL'
    corrected=clahe.apply(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY))
    state,texts=decode_candidate(detector,corrected)
    return state,texts,'CLAHE'


class QrRoi:
    def __init__(self,owner,path):
        self.owner,self.path=owner,path
        self.lock=threading.RLock();self.decode_lock=threading.Lock();self.jpeg_lock=threading.Lock()
        self.roi=[0.,0.,1.,1.];self.revision=0
        if path.exists():
            data=json.loads(path.read_text(encoding='utf-8-sig'));self.roi=validate_roi(data['roi']);self.revision=data.get('revision',0)
        self.detector=cv2.QRCodeDetector();self.clahe=cv2.createCLAHE(clipLimit=2.,tileGridSize=(8,8))
        self.result=None;self.result_at=0.;self.key=None;self.jpeg_cache=None
        self.capture_count=0;self.capture_at=time.monotonic();self.capture_fps=0.
        self.decode_count=0;self.decode_at=time.monotonic();self.decode_fps=0.
        self.thread=None;self.closed=threading.Event()

    def start(self):
        if self.thread or self.owner.simulation:return
        self.thread=threading.Thread(target=self.run,daemon=True,name='cam1-roi-qr');self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread and threading.current_thread()!=self.thread:self.thread.join(timeout=2)

    def capture_tick(self):
        with self.lock:
            self.capture_count+=1;at=time.monotonic()
            if at-self.capture_at>=1:self.capture_fps=self.capture_count/(at-self.capture_at);self.capture_count=0;self.capture_at=at

    def read_now(self):
        frame,frame_id,captured=self.owner.frame_data('cam1')
        with self.lock:roi=self.roi.copy();revision=self.revision
        empty=dict(state='NO_FRAME',pallet_id=None,text='',decoded_values=[],method=None,frame_id=frame_id,captured_at=captured,revision=revision)
        if frame is None:return empty
        with self.decode_lock:
            state,texts,method=decode_roi(self.detector,crop_roi(frame,roi),self.clahe)
        value=texts[0] if state=='READ' else ''
        if state=='READ' and not re.fullmatch(r'PALLET-0[1-8]',value):state='INVALID_QR'
        result=dict(state=state,pallet_id=value if state=='READ' else None,text=value[:500],decoded_values=[v[:500] for v in texts[:8]],method=method,frame_id=frame_id,captured_at=captured,revision=revision)
        with self.lock:
            if revision!=self.revision:return {**empty,'state':'WAITING','frame_id':None,'captured_at':None,'revision':self.revision}
            self.result=result;self.result_at=time.monotonic();self.key=(frame_id,revision)
            self.decode_count+=1;at=time.monotonic()
            if at-self.decode_at>=1:self.decode_fps=self.decode_count/(at-self.decode_at);self.decode_count=0;self.decode_at=at
        return result

    def run(self):
        previous=None
        while not self.closed.is_set() and not self.owner.closed.is_set():
            started=time.monotonic()
            _,frame_id,_=self.owner.frame_data('cam1')
            with self.lock:key=(frame_id,self.revision)
            if frame_id and key!=previous:
                try:self.read_now();previous=key
                except cv2.error:pass
            self.closed.wait(max(.01,.05-(time.monotonic()-started)))

    def status(self):
        frame,frame_id,captured=self.owner.frame_data('cam1')
        with self.lock:
            result=dict(self.result) if self.result else {}
            fresh=frame is not None
            if not fresh or time.monotonic()-self.result_at>1 or result.get('revision')!=self.revision:
                result=dict(state='WAITING' if fresh else 'NO_FRAME',pallet_id=None,text='',decoded_values=[],method=None,frame_id=frame_id,captured_at=captured,revision=self.revision)
            profile={'width':1920,'height':1080,'fps':30,**self.owner.config.get('camera_profiles',{}).get('cam1',{})}
            return dict(**result,roi=self.roi.copy(),resolution=[frame.shape[1],frame.shape[0]] if fresh else [0,0],requested_resolution=[profile['width'],profile['height']],requested_fps=profile['fps'],capture_fps=round(self.capture_fps,1) if fresh else 0.,decode_fps=round(self.decode_fps,1) if fresh else 0.)

    def set_roi(self,value,expected_revision):
        value=validate_roi(value)
        with self.lock:
            require(type(expected_revision) is int and expected_revision==self.revision,'ROI가 변경되었습니다. 새 상태를 확인하세요.',409,'CONFLICT')
            revision=self.revision+1
            temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(dict(roi=value,revision=revision),indent=2),encoding='utf-8');temporary.replace(self.path)
            self.roi=value;self.revision=revision;self.result=None;self.result_at=0.;self.jpeg_cache=None
        return self.status()

    def jpeg(self):
        with self.jpeg_lock:
            frame,frame_id,captured=self.owner.frame_data('cam1')
            if frame is None:return None,None,None
            with self.lock:roi=self.roi.copy();revision=self.revision;cache=self.jpeg_cache
            key=(frame_id,revision)
            if cache and cache[0]==key:return cache[1],captured,str(frame_id)+':'+str(revision)
            ok,data=cv2.imencode('.jpg',crop_roi(frame,roi),[cv2.IMWRITE_JPEG_QUALITY,90])
            if not ok:return None,None,None
            encoded=data.tobytes()
            with self.lock:
                if revision!=self.revision:return None,None,None
                self.jpeg_cache=(key,encoded)
            return encoded,captured,str(frame_id)+':'+str(revision)
