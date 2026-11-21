"""Hardware-free orchestration of the seven AgvRouteTest delivery steps."""
import atexit
import json
import sqlite3
import threading
import time
from contextlib import closing
from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, jsonify, request

from auth import bearer_token, create_admin_required, token_hash
from orders import connect

DESTINATIONS = {3: '서울 HUB', 4: '평택 HUB', 5: '수원 HUB', 6: '오류1', 7: '오류2'}


def delivery_steps(destination):
    return [('go', 1), ('servo', 110), ('go', 2), ('go', destination),
            ('servo', 60), ('go', 2), ('servo', 85)]


class DemoVehicle:
    """Emulates /status, /go and /servo; never opens a network connection."""
    def __init__(self, clock):
        self.clock = clock
        self.status = dict(state='ready', position=2, target=0, servo_angle=85,
                           fork_moving=False, mode='auto')
        self.pending = None
        self.commands = []

    def command(self, action, value):
        if self.pending:
            raise ValueError('AGV가 이전 동작을 처리 중입니다.')
        self.commands.append((action, value))
        self.pending = (action, value, self.clock() + (.8 if action == 'go' else .5))
        if action == 'go':
            self.status.update(state='moving', target=value)
        else:
            self.status['fork_moving'] = True

    def poll(self):
        if self.pending and self.clock() >= self.pending[2]:
            action, value, _ = self.pending
            if action == 'go':
                self.status.update(state='arrived', position=value)
            else:
                self.status.update(servo_angle=value, fork_moving=False)
            self.pending = None
        return self.status.copy()

    def stop(self):
        self.pending = None
        self.status.update(state='stopped', fork_moving=False)


class AgvDemo:
    def __init__(self, database, clock=time.monotonic):
        self.database, self.clock = database, clock
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.instance = str(uuid4())
        self.vehicle = DemoVehicle(clock)
        self.task_id = self.owner = None
        self.last_heartbeat = clock()
        self.step_started = None
        self.worker = None
        with closing(connect(database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            lease = db.execute("SELECT * FROM EQUIPMENT_DEVICE_LEASES WHERE device_id='AGV-DEMO'").fetchone()
            if lease and lease['heartbeat_at'] > int(time.time()) - 5:
                raise ValueError('다른 중앙 서버가 AGV 데모를 사용 중입니다.')
            db.execute("INSERT INTO EQUIPMENT_DEVICE_LEASES VALUES('AGV-DEMO',?,?) ON CONFLICT(device_id) DO UPDATE SET instance_id=excluded.instance_id,heartbeat_at=excluded.heartbeat_at", (self.instance, int(time.time())))
            db.execute("UPDATE AGV_TASKS SET state='INTERRUPTED',error='서버 재시작 · 자동 재출발 없음',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE finished_at IS NULL")

    def start_worker(self):
        def loop():
            while not self.closed.wait(.15):
                try:
                    self.step()
                except Exception as error:
                    self.vehicle.stop()
                    try:
                        self.stop('ERROR', 'AGV 데모 처리 실패: ' + str(error))
                    except sqlite3.Error:
                        pass
        self.worker = threading.Thread(target=loop, name='agv-demo', daemon=True)
        self.worker.start()

    def _task(self, db):
        if self.task_id:
            return db.execute('SELECT * FROM AGV_TASKS WHERE task_id=?', (self.task_id,)).fetchone()
        return db.execute('SELECT * FROM AGV_TASKS ORDER BY rowid DESC LIMIT 1').fetchone()

    def snapshot(self, owner):
        with self.lock, closing(connect(self.database)) as db:
            row = self._task(db)
            task = dict(row) if row else None
            if task:
                task.pop('owner_hash')
                task['status'] = json.loads(task.pop('status_json'))
            events = [dict(e) for e in db.execute('SELECT step_index,event,created_at FROM AGV_TASK_EVENTS WHERE task_id=? ORDER BY event_id DESC LIMIT 3', (row['task_id'] if row else '',))][::-1]
            eligible = [dict(e) for e in db.execute("""SELECT r.run_id,r.job_id,r.pallet_id,o.target_station
                FROM EQUIPMENT_RUNS r JOIN ORDERS o ON o.job_id=r.job_id
                JOIN JOB_PALLET_MAPPINGS m ON m.mapping_id=r.mapping_id
                WHERE r.state='SUCCESS' AND r.database_status='success' AND r.operator_confirmed_at IS NOT NULL
                  AND o.status='IN_PROGRESS' AND m.is_active=1 AND m.unmapped_at IS NULL
                  AND EXISTS(SELECT 1 FROM PALLETS p WHERE p.pallet_id=r.pallet_id AND p.current_status='MAPPED')
                  AND m.job_id=r.job_id AND m.pallet_id=r.pallet_id
                  AND NOT EXISTS(SELECT 1 FROM AGV_TASKS t WHERE t.equipment_run_id=r.run_id)""")]
            active = db.execute('SELECT 1 FROM AGV_TASKS WHERE finished_at IS NULL').fetchone()
            return dict(mode='DEMO', device=self.vehicle.status.copy(), task=task, events=events,
                        eligible=eligible, is_owner=bool(row and row['owner_hash'] == owner),
                        can_start=not active and not self.closed.is_set(), can_stop=bool(active),
                        destinations=[dict(point=p, name=n) for p, n in DESTINATIONS.items()])

    def start(self, owner, account_id, data):
        try:
            request_id = str(UUID(data.get('request_id', '')))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError('올바른 배송 요청 UUID가 필요합니다.') from error
        job = data.get('job_id')
        destination = data.get('destination')
        if not isinstance(job, str) or type(destination) is not int or destination not in DESTINATIONS:
            raise ValueError('주문과 목적지 3~7을 선택해 주세요.')
        with self.lock, closing(connect(self.database)) as db, db:
            if self.closed.is_set():
                raise ValueError('AGV 데모 서버가 종료되었습니다.')
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM AGV_TASKS WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous['owner_hash'] != owner or previous['job_id'] != job or previous['destination'] != destination:
                    raise ValueError('같은 요청 UUID에 다른 배송 내용이 있습니다.')
                # A retry reports its own persisted task, never starts another trip.
                result = dict(previous)
                result.pop('owner_hash')
                result['status'] = json.loads(result.pop('status_json'))
                state = self.snapshot(owner)
                state['task'] = result
                return state
            if db.execute('SELECT 1 FROM AGV_TASKS WHERE finished_at IS NULL').fetchone():
                raise ValueError('AGV가 다른 배송을 진행 중입니다.')
            run = db.execute("""SELECT r.* FROM EQUIPMENT_RUNS r
                JOIN ORDERS o ON o.job_id=r.job_id JOIN PALLETS p ON p.pallet_id=r.pallet_id
                JOIN JOB_PALLET_MAPPINGS m ON m.mapping_id=r.mapping_id
                WHERE r.job_id=? AND r.state='SUCCESS' AND r.database_status='success'
                AND r.operator_confirmed_at IS NOT NULL AND o.status='IN_PROGRESS'
                AND p.current_status='MAPPED' AND m.is_active=1 AND m.unmapped_at IS NULL
                AND m.job_id=r.job_id AND m.pallet_id=r.pallet_id ORDER BY r.rowid DESC LIMIT 1""", (job,)).fetchone()
            if not run:
                raise ValueError('선택 주문의 UNO 배출 확인·활성 QR 배정이 완료되어야 합니다.')
            if db.execute('SELECT 1 FROM AGV_TASKS WHERE equipment_run_id=?', (run['run_id'],)).fetchone():
                raise ValueError('이미 배송 기록이 있는 UNO 회차입니다. 자동 재배차하지 않습니다.')
            # Virtual reset is permitted only in DEMO. No physical position is asserted.
            self.vehicle = DemoVehicle(self.clock)
            task_id = str(uuid4())
            db.execute("""INSERT INTO AGV_TASKS(task_id,request_id,equipment_run_id,job_id,pallet_id,mapping_id,
                destination,account_id,owner_hash,state,status_json) VALUES(?,?,?,?,?,?,?,?,?,'RUNNING',?)""",
                (task_id,request_id,run['run_id'],job,run['pallet_id'],run['mapping_id'],destination,account_id,owner,json.dumps(self.vehicle.status)))
            db.execute('UPDATE ORDERS SET target_station=? WHERE job_id=?', (destination,job))
        self.task_id, self.owner = task_id, owner
        self.last_heartbeat = self.clock()
        self.step_started = None
        return self.snapshot(owner)

    def heartbeat(self, owner):
        with self.lock:
            if self.owner != owner or self.task_id is None:
                raise ValueError('배송을 시작한 로그인 세션에서 요청하세요.')
            self.last_heartbeat = self.clock()

    def stop(self, state='STOPPED', error='관리자가 AGV 데모를 정지했습니다.'):
        with self.lock, closing(connect(self.database)) as db, db:
            self.vehicle.stop()
            if self.task_id:
                db.execute("UPDATE AGV_TASKS SET state=?,error=?,status_json=?,finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND finished_at IS NULL", (state,error,json.dumps(self.vehicle.status),self.task_id))

    def step(self):
        with self.lock, closing(connect(self.database)) as db, db:
            if self.closed.is_set():
                return
            if not db.execute("UPDATE EQUIPMENT_DEVICE_LEASES SET heartbeat_at=? WHERE device_id='AGV-DEMO' AND instance_id=?", (int(time.time()),self.instance)).rowcount:
                self.vehicle.stop()
                if self.task_id:
                    db.execute("UPDATE AGV_TASKS SET state='INTERRUPTED',error='AGV 프로세스 소유권 상실',finished_at=CURRENT_TIMESTAMP WHERE task_id=? AND finished_at IS NULL",(self.task_id,))
                self.closed.set()
                return
            task = self._task(db)
            if not task or task['finished_at'] is not None or task['task_id'] != self.task_id:
                return
            failure = None
            status = self.vehicle.poll()
            if self.clock() - self.last_heartbeat > 4:
                failure = 'WPF heartbeat가 4초 이상 끊겨 배송을 중단했습니다.'
            elif status['state'] in {'stopped','wifi_lost','line_lost','timeout','unknown','reverse_testing'} or status['servo_angle'] < 0 or status['mode'] != 'auto':
                failure = 'AGV 상태 오류: ' + status['state']
            elif self.step_started is not None and self.clock() - self.step_started > 30:
                failure = 'AGV 단계 완료 응답 시간 초과'
            if failure:
                self.vehicle.stop()
                db.execute("UPDATE AGV_TASKS SET state='ERROR',error=?,status_json=?,finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (failure,json.dumps(self.vehicle.status),self.task_id))
                return
            index = task['step_index']
            steps = delivery_steps(task['destination'])
            action, value = steps[index]
            if self.step_started is None:
                self.vehicle.command(action, value)
                self.step_started = self.clock()
                db.execute('INSERT INTO AGV_TASK_EVENTS(task_id,step_index,event) VALUES(?,?,?)', (self.task_id,index,f'{action} {value} 요청'))
            else:
                complete = (status['state'] == 'arrived' and status['position'] == value and not status['fork_moving']) if action == 'go' else (status['servo_angle'] == value and not status['fork_moving'] and status['state'] in {'ready','arrived'})
                if complete:
                    db.execute('INSERT INTO AGV_TASK_EVENTS(task_id,step_index,event) VALUES(?,?,?)', (self.task_id,index,f'{action} {value} 완료'))
                    index += 1
                    self.step_started = None
                    if index == len(steps):
                        if status['position'] != 2 or status['servo_angle'] != 85 or status['fork_moving']:
                            raise ValueError('최종 2번·85도 대기 조건이 일치하지 않습니다.')
                        db.execute("UPDATE AGV_TASKS SET state='SUCCESS',finished_at=CURRENT_TIMESTAMP WHERE task_id=?", (self.task_id,))
                    db.execute('UPDATE AGV_TASKS SET step_index=? WHERE task_id=?', (index,self.task_id))
            db.execute('UPDATE AGV_TASKS SET status_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=?', (json.dumps(self.vehicle.status),self.task_id))

    def close(self):
        with self.lock:
            if self.closed.is_set():
                return
            self.stop('INTERRUPTED', '서버 종료 · 자동 재출발 없음')
            self.closed.set()
            with closing(connect(self.database)) as db, db:
                db.execute("DELETE FROM EQUIPMENT_DEVICE_LEASES WHERE device_id='AGV-DEMO' AND instance_id=?", (self.instance,))
        if self.worker:
            self.worker.join(timeout=2)


def create_agv_api():
    api = Blueprint('agv', __name__, url_prefix='/api/agv')
    required = create_admin_required(connect)
    creation_lock = threading.Lock()

    def service():
        with creation_lock:
            if 'agv_demo' not in current_app.extensions:
                demo = AgvDemo(current_app.config['ORDER_DB'])
                current_app.extensions['agv_demo'] = demo
                demo.start_worker()
                atexit.register(demo.close)
            return current_app.extensions['agv_demo']

    @api.errorhandler(ValueError)
    def conflict(error):
        return {'error':str(error)}, 409

    @api.errorhandler(sqlite3.Error)
    def database_error(error):
        return {'error':'AGV DB 처리 실패: '+str(error)}, 503

    @api.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @api.get('/state')
    @required
    def state():
        return jsonify(service().snapshot(token_hash(bearer_token())))

    @api.post('/<action>')
    @required
    def command(action):
        if g.admin_account is None:
            return {'error':'AGV 제어는 계정 로그인이 필요합니다.'},403
        data = request.get_json(silent=True)
        if not isinstance(data,dict):
            return {'error':'JSON 객체가 필요합니다.'},400
        demo, owner = service(), token_hash(bearer_token())
        if action == 'start':
            return jsonify(demo.start(owner,g.admin_account['account_id'],data))
        if action == 'heartbeat':
            demo.heartbeat(owner)
            return {'ok':True}
        if action == 'stop':
            demo.stop()
            return jsonify(demo.snapshot(owner))
        return {'error':'지원하지 않는 AGV 동작입니다.'},404
    return api
