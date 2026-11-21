"""Keep one latest frame: capture/preview never wait for QR decoding."""
import os
import threading
import time

import cv2
import numpy as np


class CameraFeed:
    def __init__(self, bench, index, root, preview_fps=20):
        self.bench, self.index, self.root = bench, index, root
        self.interval = 1 / preview_fps
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.latest = None
        self.sequence = 0
        self.outlines = []
        self.outline_at = -1000

    def fail(self, exc):
        with self.bench.lock:
            self.stopped.set()
            message = str(exc)
            # Log a loss once, rather than every failed reopen attempt.
            first_failure = not self.bench.camera_reconnecting
            self.bench.camera_ok = False
            self.bench.camera_reconnecting = not self.bench.demo
            self.bench.camera_error = (message + ' · 자동 재연결 중'
                                       if not self.bench.demo else message)
            self.bench.camera_at = -1000.0
            self.bench.clear_frames = 0
            self.bench.latest_codes = []
            self.bench.jpeg = None
            if first_failure:
                self.bench.event('CAMERA_ERROR', message=message)
            if __package__:
                from .controller import ACTIVE
            else:
                from controller import ACTIVE
            if self.bench.state in ACTIVE:
                self.bench.abort('카메라 오류: ' + message)
        with self.condition:
            self.latest = None
            self.outlines = []
            self.condition.notify_all()
        # Replace frozen video with a disconnect notice on the existing MJPEG stream.
        frame = np.full((480, 640, 3), (36, 28, 20), np.uint8)
        cv2.putText(frame, 'CAMERA DISCONNECTED', (65, 225),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (220, 220, 235), 2)
        cv2.putText(frame, 'Reconnecting automatically...', (65, 270),
                    cv2.FONT_HERSHEY_SIMPLEX, .65, (160, 185, 200), 1)
        ok, jpeg = cv2.imencode('.jpg', frame)
        with self.bench.preview_condition:
            self.bench.preview_jpeg = jpeg.tobytes() if ok else None
            self.bench.preview_sequence += 1
            self.bench.preview_condition.notify_all()

    def capture(self):
        cap = None
        try:
            if self.bench.demo:
                qr = cv2.imdecode(np.fromfile(str(self.root / 'assets/demo_qr.png'), np.uint8), 1)
                if qr is None: raise OSError('데모 QR 이미지가 없습니다')
                qr = cv2.resize(qr, (232, 232), interpolation=cv2.INTER_NEAREST)
            else:
                if os.name == 'nt':
                    cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
                    if not cap.isOpened():
                        cap.release()
                        cap = cv2.VideoCapture(self.index)
                else:
                    cap = cv2.VideoCapture(self.index)
                if not cap.isOpened(): raise OSError(f'USB 카메라 {self.index} 연결 실패')
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_FPS, 30)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            next_preview = 0
            while not self.bench.closed.is_set() and not self.stopped.is_set():
                at = self.bench.clock()
                if self.bench.demo:
                    if __package__:
                        from .controller import ACTIVE
                    else:
                        from controller import ACTIVE
                    frame = np.full((480, 640, 3), 238, np.uint8)
                    with self.bench.lock:
                        trial = self.bench.trial
                        show = trial and self.bench.state in ACTIVE and self.bench.clock() - trial['started'] > 1.4
                    if show: frame[130:362, 205:437] = qr
                    cv2.putText(frame, 'DEMO - NO REAL HARDWARE', (15, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 200), 2)
                else:
                    ok, frame = cap.read()
                    if not ok or frame is None: raise OSError('USB 카메라 영상 수신 실패')
                if frame.shape[1] > 800:
                    frame = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
                with self.condition:
                    if self.stopped.is_set() or self.bench.closed.is_set(): break
                    self.latest = (frame, at)
                    self.sequence += 1
                    outlines = self.outlines if at - self.outline_at < .35 else []
                    self.condition.notify_all()
                now = time.monotonic()
                if now >= next_preview:
                    next_preview = max(next_preview + self.interval, now)
                    preview = frame.copy()
                    for corners, decoded in outlines:
                        polygon = np.asarray(corners, dtype=np.int32).reshape(-1, 2)
                        cv2.polylines(preview, [polygon], True,
                                      (0, 200, 0) if decoded else (0, 200, 255), 3, cv2.LINE_AA)
                    ok, jpeg = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if not ok: raise OSError('카메라 이미지 인코딩 실패')
                    with self.bench.preview_condition:
                        if self.stopped.is_set() or self.bench.closed.is_set(): break
                        self.bench.preview_jpeg = jpeg.tobytes()
                        self.bench.preview_sequence += 1
                        self.bench.preview_condition.notify_all()
                if self.bench.demo: self.bench.closed.wait(1 / 30)
        except Exception as exc:
            self.fail(exc)
        finally:
            self.stopped.set()
            with self.condition: self.condition.notify_all()
            if cap is not None: cap.release()

    def process(self, decode, qr_fps=10):
        detector = cv2.QRCodeDetector()
        sequence = 0
        try:
            while not self.bench.closed.is_set() and not self.stopped.is_set():
                started = time.monotonic()
                with self.condition:
                    changed = self.condition.wait_for(
                        lambda: self.sequence != sequence or self.stopped.is_set()
                        or self.bench.closed.is_set(), timeout=.5)
                    if self.stopped.is_set() or self.bench.closed.is_set(): break
                    if not changed or self.latest is None: continue
                    sequence = self.sequence
                    frame, at = self.latest
                    frame = frame.copy()
                outlines = []
                codes = decode(detector, frame, outlines)
                # This remains the only path updating QR freshness and issuing QR stops.
                with self.bench.lock:
                    if self.stopped.is_set() or self.bench.closed.is_set(): break
                    self.bench.frame(codes, frame, at)
                with self.condition:
                    self.outlines, self.outline_at = outlines, at
                self.bench.closed.wait(max(0, 1 / qr_fps - (time.monotonic() - started)))
        except Exception as exc:
            self.fail(exc)
        finally:
            self.stopped.set()
