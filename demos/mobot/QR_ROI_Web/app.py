"""전체 영상 / ROI 영상 / QR 정보만 표시하는 독립 실시간 도구."""
import argparse
import atexit
import json
import math
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent
DEFAULT_ROI = [1500 / 1920, 600 / 1080, 290 / 1920, 300 / 1080]


def validate_roi(value):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError('ROI 형식을 확인하세요.')
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in value):
        raise ValueError('ROI 값은 유한한 숫자여야 합니다.')
    x, y, w, h = value
    if min(x, y) < 0 or min(w, h) < .01 or x+w > 1.000001 or y+h > 1.000001:
        raise ValueError('영상 안에서 가로·세로 1% 이상의 영역을 선택하세요.')
    return value


def crop_roi(frame, roi):
    h, w = frame.shape[:2]
    x, y, rw, rh = roi
    x1, y1 = min(w-1, int(x*w)), min(h-1, int(y*h))
    x2, y2 = max(x1+1, min(w, round((x+rw)*w))), max(y1+1, min(h, round((y+rh)*h)))
    return frame[y1:y2, x1:x2].copy()


def decode_candidate(detector, image):
    try:
        ok, values, points, _ = detector.detectAndDecodeMulti(image)
        texts = [v for v in values if v] if ok else []
        if points is None:
            value, _, _ = detector.detectAndDecode(image)
            texts = [value] if value else []
        detected = len(points) if points is not None else len(texts)
        return ('MULTIPLE' if detected > 1 else 'READ' if len(texts) == 1 else 'UNREADABLE'), texts
    except cv2.error:return 'UNREADABLE', []


def decode_roi(detector, crop, clahe):
    state, texts = decode_candidate(detector, crop)
    # Do not hide multiple detected QRs by salvaging just one after processing.
    if state != 'UNREADABLE':return state, texts, 'ORIGINAL'
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    corrected = clahe.apply(gray)
    state, texts = decode_candidate(detector, corrected)
    return state, texts, 'CLAHE'


class LiveQR:
    def __init__(self, index=0, width=1920, height=1080, fps=30, image=None, config=None):
        self.index, self.width, self.height, self.fps = index, width, height, fps
        self.image, self.config = image, config or ROOT/'roi_config.json'
        self.lock = threading.Condition(threading.RLock())
        self.closed = threading.Event()
        self.frame = None
        self.seq = self.generation = 0
        self.at = 0.
        self.roi = DEFAULT_ROI.copy()
        try:
            if self.config.exists():self.roi = validate_roi(json.loads(self.config.read_text(encoding='utf-8-sig'))['roi'])
        except (ValueError, KeyError, OSError):pass
        self.jpegs = {'full': None, 'roi': None}
        self.preview_seq = 0
        self.resolution = [0, 0]
        self.capture_fps = self.preview_fps = self.decode_fps = 0.
        self.driver_fps = 0.
        self.error = '카메라 연결 중'
        self.result = dict(state='WAITING', text='', texts=[], read_at=None)
        self.result_at = 0.
        self.threads = []

    def start(self):
        for worker in (self.capture, self.preview, self.scan):
            thread = threading.Thread(target=worker, daemon=True)
            self.threads.append(thread);thread.start()

    def close(self):
        self.closed.set()
        with self.lock:self.lock.notify_all()
        for thread in self.threads:thread.join(timeout=2)

    def set_roi(self, value):
        value = validate_roi(value)
        with self.lock:
            draft = self.config.with_suffix('.tmp')
            draft.write_text(json.dumps({'roi': value}, indent=2), encoding='utf-8')
            draft.replace(self.config)
            self.roi = value.copy();self.generation += 1
            self.result = dict(state='WAITING', text='', texts=[], read_at=None)
            self.result_at = 0.;self.jpegs['roi'] = None
            self.lock.notify_all()

    def lose_camera(self, error):
        with self.lock:
            self.error = str(error);self.frame = None;self.jpegs = {'full': None, 'roi': None}
            self.capture_fps = self.preview_fps = self.decode_fps = 0.
            self.result = dict(state='NO_FRAME', text='', texts=[], read_at=None)
            self.result_at = 0.;self.lock.notify_all()

    def capture(self):
        while not self.closed.is_set():
            cap = None
            try:
                if self.image:
                    picture = cv2.imdecode(np.fromfile(str(self.image), np.uint8), cv2.IMREAD_COLOR)
                    if picture is None:raise OSError('테스트 이미지를 읽을 수 없습니다.')
                else:
                    cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW) if os.name == 'nt' else cv2.VideoCapture(self.index)
                    if not cap.isOpened():
                        cap.release();cap = cv2.VideoCapture(self.index)
                    if not cap.isOpened():raise OSError(f'카메라 {self.index} 연결 실패 · USB·권한·다른 앱 점유를 확인하세요.')
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                    # DirectShow can reset the codec when size/FPS changes: set MJPG last.
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    with self.lock:self.driver_fps = cap.get(cv2.CAP_PROP_FPS)
                count, started = 0, time.monotonic()
                while not self.closed.is_set():
                    if self.image:ok, frame = True, picture
                    else:ok, frame = cap.read()
                    if not ok or frame is None:raise OSError('카메라 영상 수신 끊김 · 재연결 중')
                    at = time.monotonic();count += 1
                    with self.lock:
                        self.frame, self.at = frame, at
                        self.seq += 1;self.resolution = [frame.shape[1], frame.shape[0]];self.error = ''
                        if at-started >= 1:
                            self.capture_fps = count/(at-started);count, started = 0, at
                        self.lock.notify_all()
                    if self.image:self.closed.wait(1/self.fps)
            except Exception as error:self.lose_camera(error)
            finally:
                if cap is not None:cap.release()
            self.closed.wait(1)

    def next_frame(self, previous):
        with self.lock:
            self.lock.wait_for(lambda: self.closed.is_set() or (self.frame is not None and (self.seq, self.generation) != previous), timeout=.5)
            if self.closed.is_set() or self.frame is None or (self.seq, self.generation) == previous:return None
            return self.frame, (self.seq, self.generation), self.roi.copy(), self.at

    def preview(self):
        previous = (-1, -1);count, started = 0, time.monotonic()
        while not self.closed.is_set():
            item = self.next_frame(previous)
            if item is None:continue
            frame, previous, roi, at = item
            crop = crop_roi(frame, roi)
            # Encode each captured frame once, shared by all browser clients.
            ok1, full = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            ok2, small = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            count += 1;now = time.monotonic()
            with self.lock:
                if previous[1] != self.generation or self.frame is None or now-at > 1:continue
                if ok1 and ok2:
                    self.jpegs = {'full': full.tobytes(), 'roi': small.tobytes()};self.preview_seq += 1
                if now-started >= 1:self.preview_fps = count/(now-started);count, started = 0, now
                self.lock.notify_all()

    def scan(self):
        detector = cv2.QRCodeDetector()
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        previous = (-1, -1);count, started = 0, time.monotonic()
        while not self.closed.is_set():
            item = self.next_frame(previous)
            if item is None:continue
            frame, previous, roi, at = item
            crop = crop_roi(frame, roi)
            try:
                state, texts, method = decode_roi(detector, crop, clahe)
            except cv2.error:texts, state, method = [], 'UNREADABLE', 'CLAHE'
            now = time.monotonic();count += 1
            with self.lock:
                if previous[1] != self.generation or self.frame is None or now-at > 1:continue
                self.result = dict(state=state, text=texts[0] if state == 'READ' else '', texts=texts[:8], method=method, read_at=time.strftime('%H:%M:%S') if texts else None)
                self.result_at = now
                if now-started >= 1:self.decode_fps = count/(now-started);count, started = 0, now
            # QR decode is independent; this cap does not limit video capture/streaming.
            self.closed.wait(max(0, .05-(time.monotonic()-now)))

    def status(self):
        with self.lock:
            fresh = self.frame is not None and time.monotonic()-self.at < 1
            result = self.result.copy()
            if not fresh:result = dict(state='NO_FRAME', text='', texts=[], read_at=None)
            elif not self.result_at or time.monotonic()-self.result_at > 1:result = dict(state='WAITING', text='', texts=[], read_at=None)
            return dict(roi=self.roi.copy(), resolution=self.resolution, requested_resolution=[self.width, self.height], requested_fps=self.fps, capture_fps=round(self.capture_fps, 1), preview_fps=round(self.preview_fps, 1), decode_fps=round(self.decode_fps, 1), driver_fps=round(self.driver_fps, 1), frame_available=fresh, camera_error=self.error, result=result)

    def stream(self, kind):
        previous = (-1, -1)
        while not self.closed.is_set():
            with self.lock:
                self.lock.wait_for(lambda: self.closed.is_set() or (self.preview_seq, self.generation) != previous, timeout=.5)
                if self.closed.is_set():break
                key = (self.preview_seq, self.generation)
                if previous == key:continue
                previous = key
                image = self.jpegs[kind] if self.frame is not None and time.monotonic()-self.at < 1 else None
            if image:
                yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(image)).encode()+b'\r\n\r\n'+image+b'\r\n'


def create_app(camera):
    app = Flask(__name__)
    app.extensions['camera'] = camera

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/')
    def index():return render_template('index.html')

    @app.get('/api/status')
    def status():return jsonify(camera.status())

    @app.post('/api/roi')
    def roi():
        try:camera.set_roi((request.get_json(silent=True) or {})['roi'])
        except (ValueError, KeyError, TypeError) as error:return jsonify(error=str(error)), 400
        except OSError:return jsonify(error='ROI 설정을 저장하지 못했습니다.'), 500
        return jsonify(ok=True)

    @app.get('/video/<kind>')
    def video(kind):
        if kind not in ('full', 'roi'):return '', 404
        return Response(camera.stream(kind), mimetype='multipart/x-mixed-replace; boundary=frame', headers={'X-Accel-Buffering': 'no'})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--port', type=int, default=5001)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--image', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not (1 <= args.fps <= 120 and 64 <= args.width <= 4096 and 64 <= args.height <= 2160):parser.error('해상도 또는 FPS 범위를 확인하세요.')
    camera = LiveQR(args.camera, args.width, args.height, args.fps, args.image)
    camera.start();atexit.register(camera.close)
    print(f'http://127.0.0.1:{args.port} · FHD 기본 / ROI 실시간 QR 판독 · 종료 Ctrl+C', flush=True)
    try:create_app(camera).run(host='127.0.0.1', port=args.port, threaded=True, debug=False, use_reloader=False)
    finally:camera.close()


if __name__ == '__main__':main()
