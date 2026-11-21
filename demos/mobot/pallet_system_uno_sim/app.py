from __future__ import annotations

import argparse
import secrets
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, abort, jsonify, render_template, request

if __package__:
    from .controller import ACTIVE, Bench
    from .database import DatabaseWorker
else:
    from controller import ACTIVE, Bench
    from database import DatabaseWorker

ROOT = Path(__file__).resolve().parent


def decode(detector, frame, detected_outlines=None):
    ok, values, points, _ = detector.detectAndDecodeMulti(frame)
    codes = [v for v in values if v] if ok else []
    outlines = []
    if points is not None:
        for index, corners in enumerate(points):
            decoded = bool(ok and index < len(values) and values[index])
            outlines.append((corners, decoded))
    if points is not None:
        # Include unreadable outlines so a partial multi-QR decode cannot match.
        count = len(points)
        codes = [(values[i] if ok and i < len(values) and values[i]
                  else "(QR 감지됨 · 판독 불가)") for i in range(count)]
    elif not codes:
        value, single_points, _ = detector.detectAndDecode(frame)
        codes = [value] if value else []
        if single_points is not None:
            outlines = [(single_points, bool(value))]
            if not value: codes = ["(QR 감지됨 · 판독 불가)"]
    if detected_outlines is not None:
        detected_outlines.extend(outlines)
    # Draw only after all decoding has finished so overlays cannot affect decoding.
    for corners, decoded in outlines:
        polygon = np.asarray(corners, dtype=np.int32).reshape(-1, 2)
        color = (0, 200, 0) if decoded else (0, 200, 255)
        cv2.polylines(frame, [polygon], True, color, 3, cv2.LINE_AA)
    return codes


def camera_worker(bench, camera_index, preview_fps=20, qr_fps=10, reconnect_delay=1.0):
    if __package__:
        from .camera_pipeline import CameraFeed
    else:
        from camera_pipeline import CameraFeed
    while not bench.closed.is_set():
        feed = CameraFeed(bench, camera_index, ROOT, preview_fps)
        capture = threading.Thread(target=feed.capture, daemon=True)
        capture.start()
        try:
            feed.process(decode, qr_fps)
        finally:
            feed.stopped.set()
            with feed.condition:
                feed.condition.notify_all()
            # Never open a second camera handle while the previous reader is alive.
            while capture.is_alive() and not bench.closed.is_set():
                capture.join(timeout=.2)
            if bench.closed.is_set():
                capture.join(timeout=2)
        if bench.demo or bench.closed.wait(reconnect_delay):
            break


def demo_step(bench):
    if not bench.demo: return
    with bench.lock:
        trial = bench.trial
        if trial and bench.state in ACTIVE:
            elapsed = bench.clock() - trial["started"]
            marks = trial.setdefault("demo_marks", set())
            timings = trial['timings']
            first_stop = 1.8 + timings['ir1_delay_ms']/1000
            resumed = first_stop + timings['robot_wait_ms']/1000
            ir2_at = resumed + .5
            final_stop = ir2_at + timings['ir2_delay_ms']/1000
            if trial["qr_sent"] and not trial["qr_ack"]:
                bench.line("QR:RECOGNIZED_CONTINUE_TO_IR")
            schedule = ((.1,"ACTUATOR:PUSH_10000MS"),
                        (1.2,"ACTUATOR:PAUSE_500MS"),
                        (1.3,"RETURN_AND_BELT:STARTED"),
                        (1.8,"IR:DETECTED"),(1.8,"IR_TIMER:STARTED"),
                        (2.5,"RETURN_TIMER_DONE:POSITION_UNVERIFIED"),
                        (first_stop,"SIM:STATION_STOPPED"),
                        (resumed,"SIM:BELT_RESTARTED"),
                        (ir2_at,"IR2:DETECTED"),(ir2_at,"IR2_TIMER:STARTED"),
                        (final_stop,"BELT_STOPPED:IR2_DELAY_DONE"),
                        (max(2.5,final_stop)+.1,"CYCLE_DONE:READY_A"))
            for delay,message in sorted(schedule,key=lambda item:item[0]):
                if elapsed>=delay and message not in marks:
                    marks.add(message);bench.line(message)
        else:
            if not bench.ir_clear:bench.line('IR:CLEAR')
            if not bench.ir2_clear:bench.line('IR2:CLEAR')


def monitor_worker(bench):
    while not bench.closed.wait(.05):
        demo_step(bench)
        bench.tick()


def serial_reconnect_worker(bench, retry_interval=1.):
    while not bench.closed.wait(retry_interval):
        try:bench.serial_reconnect_step(retry_interval)
        except Exception as exc:
            with bench.lock:bench.serial_error='UNO 재탐색 오류: '+str(exc)


def create_app(bench, database=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4096
    app.config["CSRF_TOKEN"] = secrets.token_urlsafe(32)

    @app.before_request
    def protect():
        if request.method == "POST":
            value = request.headers.get("X-Test-Token", "")
            if not secrets.compare_digest(value, app.config["CSRF_TOKEN"]):
                abort(403)
            if not request.is_json: abort(415)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", token=app.config["CSRF_TOKEN"], demo=bench.demo)

    @app.get("/api/state")
    def state():
        result = bench.snapshot()
        result["database"] = database.snapshot() if database else {"status": "disabled"}
        result["database"]["blocks_next_run"] = database.blocks_next_run(result) if database else False
        return jsonify(result)

    @app.get("/api/ports")
    def ports():
        try: return jsonify(ports=bench.available_ports())
        except Exception as exc: return jsonify(error=str(exc)), 503

    @app.get("/api/frame.jpg")
    def frame():
        with bench.preview_condition: image = bench.preview_jpeg
        if image is None:
            with bench.lock: image = bench.jpeg
        if image is None: return Response(status=503)
        return Response(image, mimetype="image/jpeg")

    @app.get("/api/stream.mjpg")
    def stream():
        def frames():
            sequence = -1
            while not bench.closed.is_set():
                with bench.preview_condition:
                    bench.preview_condition.wait_for(
                        lambda: bench.preview_sequence != sequence or bench.closed.is_set(), timeout=1)
                    if bench.closed.is_set(): break
                    image = bench.preview_jpeg
                    current = bench.preview_sequence
                    if image is None or current == sequence: continue
                    sequence = current
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(image)).encode('ascii') + b"\r\n\r\n" + image + b"\r\n")
        response = Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame')
        response.headers['X-Accel-Buffering'] = 'no'
        return response

    def action(callback):
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict): raise ValueError("JSON 객체가 필요합니다")
            result = callback(data)
            return jsonify(ok=True, result=result)
        except (ValueError, OSError) as exc:
            return jsonify(ok=False, error=str(exc)), 409

    @app.post("/api/orders")
    def orders():
        if database is None or bench.demo:
            return jsonify(error="DB 주문 접수는 실제 장비 모드에서만 가능합니다"), 409
        from order_service import receive_order, validate_order, OrderConflict
        import mysql.connector
        connection = None
        try:
            data = validate_order(request.get_json(silent=True))
            connection = database.connect()
            result = receive_order(connection, data)
            return jsonify(result), 201 if result["created"] else 200
        except OrderConflict as exc:
            return jsonify(error=str(exc)), 409
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except mysql.connector.Error:
            return jsonify(error="DB 처리 실패. 같은 job_id로 재시도하세요"), 503
        finally:
            if connection is not None: connection.close()

    @app.post("/api/database/retry")
    def retry_database():
        if database is None: return jsonify(error="DB 비활성"), 409
        return action(lambda d: database.retry())

    @app.post("/api/connect")
    def connect(): return action(lambda d: bench.connect(d.get("port", "")))

    @app.post("/api/disconnect")
    def disconnect(): return action(lambda d: bench.disconnect())

    @app.post("/api/timings")
    def timings():
        return action(lambda data: bench.update_timings(data.get('values')))

    @app.post("/api/start")
    def start():
        def begin(data):
            with bench.lock:
                if database and database.blocks_next_run(bench.snapshot()):
                    raise ValueError("이전 회차의 DB 처리를 완료하거나 실패를 재시도하세요")
                return bench.start(data.get("owner"), data.get("position_ready"))
        return action(begin)

    @app.post("/api/actuator/jog")
    def jog():
        def move(data):
            with bench.lock:
                if database and database.blocks_next_run(bench.snapshot()):
                    raise ValueError("이전 회차의 DB 처리를 먼저 확인하세요")
                return bench.manual_jog(data.get("direction"), data.get("owner"), data.get("area_clear"))
        return action(move)

    @app.post("/api/stop")
    def stop(): return action(lambda d: bench.abort("사용자 전체 정지", manual=True))

    @app.post("/api/heartbeat")
    def heartbeat(): return action(lambda d: bench.heartbeat(d.get("owner")))

    @app.post("/api/confirm-ejection")
    def confirm(): return action(lambda d: bench.confirm_ejection(d.get("owner")))

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Never open real serial/camera devices")
    parser.add_argument("--mapping", choices=("lookup", "auto"), default="lookup", help="lookup: 조회만 / auto: 가장 오래된 대기 주문 배정")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--preview-fps", type=int, default=20, help="화면 전송 목표 FPS (1~30)")
    parser.add_argument("--qr-fps", type=int, default=10, help="QR 판독 목표 FPS (1~30)")
    args = parser.parse_args()
    if not (1 <= args.preview_fps <= 30 and 1 <= args.qr_fps <= 30):
        parser.error("FPS는 1~30 사이여야 합니다")
    bench = Bench(ROOT / "results", demo=args.demo)
    threading.Thread(target=camera_worker, args=(bench, args.camera, args.preview_fps, args.qr_fps), daemon=True).start()
    threading.Thread(target=monitor_worker, args=(bench,), daemon=True).start()
    threading.Thread(target=serial_reconnect_worker, args=(bench,), daemon=True).start()
    database = DatabaseWorker(bench, args.mapping)
    threading.Thread(target=database.run, daemon=True).start()
    app = create_app(bench, database)
    try:
        print("DEMO: no real devices" if args.demo else "LIVE: device connection requires UI button")
        app.run(host=args.host, port=args.port, debug=False, threaded=True, use_reloader=False)
    finally:
        bench.shutdown()


if __name__ == "__main__": main()
