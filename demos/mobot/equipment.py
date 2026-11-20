"""Authenticated, single-device UNO demo using the supplied controller and SQLite."""
import atexit
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import cv2
import numpy as np
from flask import Blueprint, Response, current_app, g, jsonify, request

from auth import bearer_token, create_admin_required, token_hash
from orders import connect
from pallet_system_uno_sim.app import decode, demo_step
from pallet_system_uno_sim.controller import ACTIVE, MOTION, Bench
from pallet_system_uno_sim.database import DatabaseWorker
from pallet_system_uno_sim.pallet_repository import validate_pallet_id


class EquipmentDemo:
    def __init__(self, database, root, qr_root, clock=time.monotonic):
        self.database = Path(database)
        self.qr_root = Path(qr_root)
        self.lock = threading.RLock()
        self.bench = Bench(Path(root), demo=True, clock=clock)
        self.run_id = None
        self.owner = None
        self.expected_pallet = None
        self.qr_image = None
        self.decoded_codes = []
        self.database_status = 'waiting'
        self.database_error = ''
        self.saved_signature = None
        self.worker = None
        self.instance_id = str(uuid4())
        with closing(connect(self.database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            lease = db.execute("SELECT * FROM EQUIPMENT_DEVICE_LEASES WHERE device_id = 'UNO-DEMO'").fetchone()
            if lease and lease['heartbeat_at'] > int(time.time()) - 5:
                raise ValueError('다른 중앙 서버 프로세스가 UNO 데모를 사용 중입니다.')
            db.execute("INSERT INTO EQUIPMENT_DEVICE_LEASES VALUES ('UNO-DEMO', ?, ?) ON CONFLICT(device_id) DO UPDATE SET instance_id = excluded.instance_id, heartbeat_at = excluded.heartbeat_at", (self.instance_id, int(time.time())))
            interrupted = db.execute(
                """UPDATE EQUIPMENT_RUNS SET state = 'INTERRUPTED', finished_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP, database_error = '서버 재시작으로 회차 중단 · 자동 재시작 없음'
                   WHERE device_id = 'UNO-DEMO' AND finished_at IS NULL"""
            ).rowcount
        self.bench.connect('DEMO')
        if interrupted:
            self.bench.error = '이전 데모 회차가 서버 재시작으로 중단되었습니다.'
        blank = np.full((480, 640, 3), 238, np.uint8)
        for _ in range(3):
            self.bench.frame([], blank, self.bench.clock())

    def start_worker(self):
        self.worker = threading.Thread(target=self._loop, name='uno-demo', daemon=True)
        self.worker.start()

    def close(self):
        with self.lock:
            if self.bench.closed.is_set():
                return
            if self.run_id and self.bench.state in MOTION | {'AWAIT_CONFIRM'}:
                self.bench.abort('서버 종료로 데모 회차 중단')
                self._save()
            self.bench.shutdown()
            with closing(connect(self.database)) as db, db:
                db.execute("DELETE FROM EQUIPMENT_DEVICE_LEASES WHERE device_id = 'UNO-DEMO' AND instance_id = ?", (self.instance_id,))
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)

    def _loop(self):
        while not self.bench.closed.wait(.15):
            try:
                self.step()
            except Exception as error:
                with self.lock:
                    self.database_status = 'error'
                    self.database_error = str(error)

    def step(self):
        with self.lock:
            with closing(connect(self.database)) as db, db:
                owned = db.execute("UPDATE EQUIPMENT_DEVICE_LEASES SET heartbeat_at = ? WHERE device_id = 'UNO-DEMO' AND instance_id = ?", (int(time.time()), self.instance_id)).rowcount
            if not owned:
                self.bench.abort('데모 프로세스 소유권을 잃어 중단했습니다.')
                self.bench.closed.set()
                return
            demo_step(self.bench)
            frame = np.full((480, 640, 3), 238, np.uint8)
            cv2.putText(frame, 'UNO DEMO - NO HARDWARE', (16, 32), cv2.FONT_HERSHEY_SIMPLEX, .7, (65, 75, 95), 2)
            cv2.putText(frame, self.bench.state, (16, 66), cv2.FONT_HERSHEY_SIMPLEX, .65, (65, 75, 95), 2)
            show_qr = (self.bench.trial and self.bench.state in ACTIVE | {'AWAIT_CONFIRM'}
                       and self.bench.clock() - self.bench.trial['started'] >= 1.5)
            codes = []
            if show_qr and self.qr_image is not None:
                frame[130:362, 204:436] = self.qr_image
                codes = self.decoded_codes or decode(cv2.QRCodeDetector(), frame)
                if codes:
                    self.decoded_codes = codes.copy()
            self.bench.frame(codes, frame, self.bench.clock())
            self.bench.tick()
            if (self.run_id and self.bench.trial['qr_ok'] and self.bench.trial['qr_ack']
                    and self.database_status == 'waiting'):
                try:
                    self._bind_order()
                except (ValueError, sqlite3.Error) as error:
                    self.database_status = 'error'
                    self.database_error = str(error)
            self._save()

    def _run(self, db):
        if self.run_id:
            return db.execute('SELECT * FROM EQUIPMENT_RUNS WHERE run_id = ?', (self.run_id,)).fetchone()
        return db.execute("SELECT * FROM EQUIPMENT_RUNS WHERE device_id = 'UNO-DEMO' ORDER BY rowid DESC LIMIT 1").fetchone()

    def _save(self):
        if not self.run_id:
            return
        state = self.bench.snapshot()
        trial = json.dumps(state['trial'], ensure_ascii=False, sort_keys=True)
        signature = (state['state'], trial, self.database_status, self.database_error)
        if signature == self.saved_signature:
            return
        terminal = state['state'] in {'STOPPED', 'ERROR', 'QR_UNCONFIRMED'}
        with closing(connect(self.database)) as db, db:
            db.execute(
                """UPDATE EQUIPMENT_RUNS SET state = ?, trial_json = ?, database_status = ?, database_error = ?,
                       updated_at = CURRENT_TIMESTAMP,
                       finished_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE finished_at END
                   WHERE run_id = ? AND finished_at IS NULL""",
                (*signature, terminal, self.run_id),
            )
        self.saved_signature = signature

    def start(self, owner, account_id, data):
        job_id = data.get('job_id')
        if not isinstance(data.get('pallet_id'), str):
            raise ValueError('데모 팔레트를 선택해 주세요.')
        pallet_id = validate_pallet_id(data.get('pallet_id'))
        try:
            request_id = str(UUID(data.get('request_id', '')))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError('올바른 운전 요청 번호가 필요합니다.') from error
        if not isinstance(job_id, str) or not job_id or data.get('position_ready') is not True:
            raise ValueError('주문 선택과 시작 위치 확인이 필요합니다.')
        with self.lock, closing(connect(self.database)) as db, db:
            if self.bench.closed.is_set():
                raise ValueError('데모 프로세스가 종료되었습니다. 중앙 서버를 재시작해 주세요.')
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM EQUIPMENT_RUNS WHERE request_id = ?', (request_id,)).fetchone()
            if previous:
                if previous['owner_hash'] != owner or previous['job_id'] != job_id or previous['expected_pallet_id'] != pallet_id:
                    raise ValueError('같은 요청 번호에 다른 운전 정보가 있습니다.')
                return self.snapshot(owner, previous)
            if db.execute("SELECT 1 FROM EQUIPMENT_RUNS WHERE device_id = 'UNO-DEMO' AND finished_at IS NULL").fetchone():
                raise ValueError('이전 회차의 DB 처리와 배출 확인을 먼저 완료해 주세요.')
            order = db.execute('SELECT status FROM ORDERS WHERE job_id = ?', (job_id,)).fetchone()
            pallet = db.execute('SELECT current_status FROM PALLETS WHERE pallet_id = ?', (pallet_id,)).fetchone()
            if order is None or order['status'] != 'PENDING':
                raise ValueError('선택한 주문은 생산 대기 상태여야 합니다.')
            if pallet is None or pallet['current_status'] != 'READY':
                raise ValueError('등록된 READY 팔레트를 선택해 주세요.')
            if db.execute('SELECT 1 FROM JOB_PALLET_MAPPINGS WHERE (job_id = ? OR pallet_id = ?) AND (is_active = 1 OR unmapped_at IS NULL)', (job_id, pallet_id)).fetchone():
                raise ValueError('주문 또는 팔레트에 활성 배정이 있습니다.')
            image = cv2.imdecode(np.fromfile(str(self.qr_root / f'{pallet_id}.png'), np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError('해당 팔레트의 데모 QR 이미지가 없습니다.')
            self.bench.start(owner, True)
            self.run_id = self.bench.trial['id']
            self.owner = owner
            self.expected_pallet = pallet_id
            self.qr_image = cv2.resize(image, (232, 232), interpolation=cv2.INTER_NEAREST)
            self.decoded_codes = []
            self.database_status = 'waiting'
            self.database_error = ''
            self.saved_signature = None
            try:
                db.execute(
                    """INSERT INTO EQUIPMENT_RUNS(run_id, request_id, job_id, expected_pallet_id, account_id, owner_hash, state)
                       VALUES (?, ?, ?, ?, ?, ?, 'STARTING')""",
                    (self.run_id, request_id, job_id, pallet_id, account_id, owner),
                )
            except sqlite3.Error:
                self.bench.abort('회차 기록 실패로 데모 시작을 중단했습니다.')
                self.run_id = None
                raise
        return self.snapshot(owner)

    def _bind_order(self):
        pallet_id = self.bench.trial['pallet_id']
        if pallet_id != self.expected_pallet:
            raise ValueError('인식된 QR이 선택한 데모 팔레트와 다릅니다.')
        with closing(connect(self.database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db)
            if run is None or run['finished_at'] is not None:
                raise ValueError('이미 종료된 회차에는 주문을 배정할 수 없습니다.')
            active = db.execute(
                'SELECT * FROM JOB_PALLET_MAPPINGS WHERE (job_id = ? OR pallet_id = ?) AND (is_active = 1 OR unmapped_at IS NULL)',
                (run['job_id'], pallet_id),
            ).fetchall()
            if active:
                if len(active) != 1 or active[0]['job_id'] != run['job_id'] or active[0]['pallet_id'] != pallet_id or active[0]['unmapped_at'] is not None:
                    raise ValueError('다른 주문 또는 팔레트가 이미 배정되었습니다.')
                mapping_id = active[0]['mapping_id']
            else:
                order = db.execute('SELECT status FROM ORDERS WHERE job_id = ?', (run['job_id'],)).fetchone()
                pallet = db.execute('SELECT current_status FROM PALLETS WHERE pallet_id = ?', (pallet_id,)).fetchone()
                if not order or order['status'] != 'PENDING' or not pallet or pallet['current_status'] != 'READY':
                    raise ValueError('주문 또는 팔레트 상태가 배정 가능한 상태가 아닙니다.')
                mapping_id = db.execute('INSERT INTO JOB_PALLET_MAPPINGS(job_id,pallet_id,qr_read_success,is_active) VALUES(?,?,1,1)', (run['job_id'], pallet_id)).lastrowid
                db.execute("UPDATE ORDERS SET status = 'IN_PROGRESS' WHERE job_id = ?", (run['job_id'],))
                db.execute("UPDATE PALLETS SET current_status = 'MAPPED', updated_at = CURRENT_TIMESTAMP WHERE pallet_id = ?", (pallet_id,))
            db.execute("UPDATE EQUIPMENT_RUNS SET pallet_id = ?, mapping_id = ?, database_status = 'mapped', database_error = '', updated_at = CURRENT_TIMESTAMP WHERE run_id = ?", (pallet_id, mapping_id, self.run_id))
        self.database_status = 'mapped'
        self.database_error = ''

    def _check_owner(self, owner):
        if not self.run_id or self.owner != owner:
            raise ValueError('이 회차를 시작한 로그인 세션에서 요청해 주세요.')

    def heartbeat(self, owner):
        with self.lock:
            self._check_owner(owner)
            self.bench.heartbeat(owner)

    def stop(self, owner):
        with self.lock:
            self.bench.abort('관리자 데모 정지', manual=True)
            self._save()
            return self.snapshot(owner)

    def retry(self, owner):
        with self.lock:
            self._check_owner(owner)
            if self.database_status != 'error' or not self.bench.trial['qr_ack']:
                raise ValueError('QR 확인 후 실패한 DB 작업만 재시도할 수 있습니다.')
            with closing(connect(self.database)) as db:
                if self._run(db)['finished_at'] is not None:
                    raise ValueError('종료된 회차는 자동 재시작하거나 재배정하지 않습니다.')
            self.database_status = 'waiting'
            self.database_error = ''
            self.saved_signature = None
            self.step()
            return self.snapshot(owner)

    def confirm(self, owner):
        with self.lock:
            self._check_owner(owner)
            if self.bench.state == 'SUCCESS':
                return self.snapshot(owner)
            if self.database_status != 'mapped' or not DatabaseWorker.eligible(self.bench.snapshot()):
                raise ValueError('QR·장비 종료·DB 배정을 모두 확인한 뒤 배출 확인을 진행해 주세요.')
            try:
                with closing(connect(self.database)) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    run = self._run(db)
                    if run['finished_at'] is not None or run['mapping_id'] is None:
                        raise ValueError('현재 회차의 DB 배정을 확인할 수 없습니다.')
                    mapping = db.execute('SELECT * FROM JOB_PALLET_MAPPINGS WHERE mapping_id = ?', (run['mapping_id'],)).fetchone()
                    order = db.execute('SELECT status FROM ORDERS WHERE job_id = ?', (run['job_id'],)).fetchone()
                    if (not mapping or not mapping['is_active'] or mapping['unmapped_at'] is not None
                            or mapping['job_id'] != run['job_id'] or mapping['pallet_id'] != run['pallet_id']
                            or not order or order['status'] != 'IN_PROGRESS'):
                        raise ValueError('현재 주문의 활성 팔레트 배정이 일치하지 않습니다.')
                    self.bench.confirm_ejection(owner)
                    if self.bench.state != 'SUCCESS':
                        raise OSError(self.bench.error)
                    db.execute("UPDATE EQUIPMENT_RUNS SET state = 'SUCCESS', database_status = 'success', operator_confirmed_at = CURRENT_TIMESTAMP, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, trial_json = ? WHERE run_id = ?", (json.dumps(self.bench.snapshot()['trial'], ensure_ascii=False), self.run_id))
            except (sqlite3.Error, OSError):
                self.bench.state = 'AWAIT_CONFIRM'
                self.bench.trial['ejection_confirmed'] = False
                raise
            self.database_status = 'success'
            return self.snapshot(owner)

    def timings(self, owner, values):
        with self.lock:
            self.bench.update_timings(values)
            self.bench.tick()
            return self.snapshot(owner)

    def snapshot(self, owner, previous=None):
        with self.lock, closing(connect(self.database)) as db:
            state = self.bench.snapshot()
            run = previous if previous is not None else self._run(db)
            run_data = None
            is_owner = bool(run and run['owner_hash'] == owner)
            if run:
                run_data = dict(run)
                run_data.pop('owner_hash')
                run_data['trial'] = json.loads(run_data.pop('trial_json'))
                if run['run_id'] == self.run_id and run['finished_at'] is None:
                    run_data['database_status'] = self.database_status
                    run_data['database_error'] = self.database_error
                for field in ('created_at', 'updated_at', 'operator_confirmed_at', 'finished_at'):
                    if run_data[field]:
                        run_data[field] = run_data[field].replace(' ', 'T') + 'Z'
            active = db.execute("SELECT 1 FROM EQUIPMENT_RUNS WHERE device_id = 'UNO-DEMO' AND finished_at IS NULL").fetchone()
            state.update(run=run_data, is_owner=is_owner,
                         can_start=not self.bench.closed.is_set() and not active and state['ready'] and state['clear_view'] and state['camera_ok']
                         and state['timings']['status'] == 'confirmed' and state['state'] not in MOTION | {'AWAIT_CONFIRM'},
                         can_confirm=is_owner and self.database_status == 'mapped' and DatabaseWorker.eligible(state),
                         can_retry=is_owner and bool(run and not run['finished_at']) and self.database_status == 'error',
                         database={'status': run_data['database_status'] if run_data else 'waiting',
                                   'message': run_data['database_error'] if run_data else '', 'run_id': run['run_id'] if run else None})
            return state


def create_equipment_api():
    api = Blueprint('equipment', __name__, url_prefix='/api/equipment')
    admin_required = create_admin_required(connect)
    creation_lock = threading.Lock()

    @api.errorhandler(ValueError)
    def unavailable(error):
        return {'error': str(error)}, 409

    @api.errorhandler(sqlite3.Error)
    def database_unavailable(error):
        return {'error': '장비 DB 조회 실패: ' + str(error)}, 503

    def service():
        with creation_lock:
            existing = current_app.extensions.get('uno_demo')
            if existing is None:
                existing = EquipmentDemo(current_app.config['ORDER_DB'], current_app.config['UNO_DEMO_ROOT'], current_app.config['UNO_DEMO_QRS'])
                current_app.extensions['uno_demo'] = existing
                existing.start_worker()
                atexit.register(existing.close)
            return existing

    def owner():
        return token_hash(bearer_token())

    @api.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @api.get('/state')
    @admin_required
    def state():
        return jsonify(service().snapshot(owner()))

    @api.get('/frame.jpg')
    @admin_required
    def frame():
        bench = service().bench
        with bench.lock:
            image = bench.jpeg
        return Response(image, mimetype='image/jpeg') if image else Response(status=503)

    @api.post('/<action>')
    @admin_required
    def command(action):
        if g.admin_account is None:
            return {'error': '장비 제어는 관리자 계정으로 로그인해야 합니다.'}, 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {'error': 'JSON 객체가 필요합니다.'}, 400
        equipment = service()
        try:
            if action == 'start':
                result = equipment.start(owner(), g.admin_account['account_id'], data)
            elif action == 'heartbeat':
                equipment.heartbeat(owner())
                return {'ok': True}
            elif action == 'stop':
                result = equipment.stop(owner())
            elif action == 'confirm-ejection':
                result = equipment.confirm(owner())
            elif action == 'database-retry':
                result = equipment.retry(owner())
            elif action == 'timings':
                result = equipment.timings(owner(), data.get('values'))
            else:
                return {'error': '지원하지 않는 장비 동작입니다.'}, 404
            return jsonify(result)
        except ValueError as error:
            return {'error': str(error)}, 409
        except (sqlite3.Error, OSError) as error:
            return {'error': '데모 기록·DB 처리 실패: ' + str(error)}, 503

    return api
