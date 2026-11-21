"""Outbound Pi polling bridge. Motion commands are delivered at most once."""
import json
import atexit
import sqlite3
import threading
import time
from contextlib import closing
from types import SimpleNamespace
from uuid import UUID, uuid4

import cv2
import numpy as np
from flask import Blueprint, Response, current_app, request

from auth import bearer_token, token_hash
from equipment import EquipmentDemo
from orders import connect
from pallet_system_uno_sim.controller import Bench
from pallet_system_uno_sim.database import DatabaseWorker
from pallet_system_uno_sim.pallet_repository import validate_pallet_id


class RemoteEquipment(EquipmentDemo):
    def __init__(self, database, device_id, clock=time.time):
        self.database, self.device_id, self.clock = database, device_id, clock
        self.lock = threading.RLock()
        self.run_id = None
        self.closed = False
        self.instance_id=str(uuid4())
        with closing(connect(database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            key='REMOTE:'+device_id
            lease=db.execute('SELECT * FROM EQUIPMENT_DEVICE_LEASES WHERE device_id=?',(key,)).fetchone()
            if lease and lease['heartbeat_at']>clock()-5:raise ValueError('다른 서버가 원격 UNO를 사용 중입니다.')
            db.execute('INSERT INTO EQUIPMENT_DEVICE_LEASES VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET instance_id=excluded.instance_id,heartbeat_at=excluded.heartbeat_at',(key,self.instance_id,clock()))
            # Reconcile once per server instance, including when the Pi polls first.
            db.execute("UPDATE EQUIPMENT_RUNS SET state='INTERRUPTED',database_error='중앙 서버 재시작 · 자동 재운전 없음',finished_at=CURRENT_TIMESTAMP WHERE device_id=? AND finished_at IS NULL",(device_id,))
            db.execute("UPDATE REMOTE_COMMANDS SET state='CANCELLED' WHERE device_id=? AND state IN ('PENDING','SENT')",(device_id,))

    def start_worker(self):
        def lease_loop():
            while not self.closed:
                try:
                    with closing(connect(self.database)) as db, db:
                        if not db.execute('UPDATE EQUIPMENT_DEVICE_LEASES SET heartbeat_at=? WHERE device_id=? AND instance_id=?',(self.clock(),'REMOTE:'+self.device_id,self.instance_id)).rowcount:
                            self.closed=True
                except sqlite3.Error:pass
                time.sleep(.5)
        threading.Thread(target=lease_loop,name='remote-uno-lease',daemon=True).start()

    def close(self):
        self.closed = True
        with closing(connect(self.database)) as db, db:
            db.execute('DELETE FROM EQUIPMENT_DEVICE_LEASES WHERE device_id=? AND instance_id=?',('REMOTE:'+self.device_id,self.instance_id))

    def _device(self, db):
        if self.closed or not db.execute('SELECT 1 FROM EQUIPMENT_DEVICE_LEASES WHERE device_id=? AND instance_id=?',('REMOTE:'+self.device_id,self.instance_id)).fetchone():
            raise ValueError('원격 장비 서버 소유권을 잃었습니다. 서버를 재시작하세요.')
        row = db.execute('SELECT * FROM REMOTE_DEVICES WHERE device_id=? AND enabled=1',(self.device_id,)).fetchone()
        if not row:
            raise ValueError('라즈베리파이 장비 등록이 필요합니다.')
        return row

    def _current(self, db):
        return db.execute('SELECT * FROM EQUIPMENT_RUNS WHERE device_id=? ORDER BY rowid DESC LIMIT 1',(self.device_id,)).fetchone()

    def _queue(self, db, action, payload, run_id=None):
        command = str(uuid4())
        db.execute('INSERT INTO REMOTE_COMMANDS(command_id,device_id,run_id,action,payload_json,expires_at) VALUES(?,?,?,?,?,?)',
                   (command,self.device_id,run_id,action,json.dumps(payload),self.clock()+3))
        return command

    def _fail(self, db, run, message, state='ERROR'):
        if not run or run['finished_at']:
            return
        db.execute('UPDATE EQUIPMENT_RUNS SET state=?,database_error=?,finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND finished_at IS NULL',(state,message,run['run_id']))
        db.execute("UPDATE REMOTE_COMMANDS SET state='CANCELLED' WHERE run_id=? AND state='PENDING'",(run['run_id'],))
        self._queue(db,'stop',{},run['run_id'])

    def _sweep(self, db):
        run = self._current(db)
        if run and not run['finished_at']:
            heartbeat = db.execute('SELECT owner_heartbeat FROM REMOTE_RUN_CONTROL WHERE run_id=?',(run['run_id'],)).fetchone()
            device = self._device(db)
            if not heartbeat or self.clock()-heartbeat[0]>4:
                self._fail(db,run,'WPF heartbeat 단절 · 원격 운전 중단')
            elif self.clock()-device['last_seen']>4:
                self._fail(db,run,'라즈베리파이 통신 단절 · 자동 재운전 없음')
        expired = db.execute("SELECT * FROM REMOTE_COMMANDS WHERE device_id=? AND state IN ('PENDING','SENT') AND expires_at<?",(self.device_id,self.clock())).fetchall()
        for command in expired:
            db.execute("UPDATE REMOTE_COMMANDS SET state='EXPIRED' WHERE command_id=?",(command['command_id'],))
            if command['action'] in {'start','confirm'} and run and command['run_id']==run['run_id']:
                self._fail(db,run,'명령 응답 확인 실패 · 중복 전송하지 않음')

    def snapshot(self, owner, previous=None):
        with self.lock, closing(connect(self.database)) as db, db:
            self._sweep(db)
            device = self._device(db)
            run = previous if previous is not None else self._current(db)
            status = json.loads(device['status_json'])
            online = self.clock()-device['last_seen']<3
            active = bool(run and not run['finished_at'])
            defaults = dict(state='OFFLINE',trial={},events=[],timings={'values':{},'status':'waiting'},
                            ready=False,clear_view=False,camera_ok=False,ir_clear=False,ir2_clear=False)
            defaults.update(status)
            defaults.update(mode='REMOTE', hardware_mode=device['hardware_mode'], online=online,
                            error=device['error'] or status.get('error','') if online else '라즈베리파이 연결 대기')
            data = None
            if run:
                data = dict(run)
                data.pop('owner_hash')
                data['trial'] = json.loads(data.pop('trial_json'))
                for name in ('created_at','updated_at','operator_confirmed_at','finished_at'):
                    if data[name]:data[name]=data[name].replace(' ','T')+'Z'
                if active or run['state'] in {'ERROR','STOPPED','INTERRUPTED'}:
                    defaults['state'] = run['state']
            is_owner = bool(run and run['owner_hash']==owner)
            defaults.update(run=data,is_owner=is_owner,
                can_start=online and not active and defaults['ready'] and defaults['clear_view'] and defaults['camera_ok'] and defaults['timings']['status']=='confirmed',
                can_confirm=online and active and is_owner and run['database_status']=='mapped' and DatabaseWorker.eligible(defaults),
                can_retry=online and active and is_owner and run['database_status']=='error',
                database={'status':run['database_status'] if run else 'waiting','message':run['database_error'] if run else ''})
            return defaults

    def get_frame(self):
        with closing(connect(self.database)) as db:
            d = self._device(db)
            return bytes(d['frame_jpeg']) if d['frame_jpeg'] and self.clock()-d['frame_at']<3 else None

    def start(self, owner, account_id, data):
        try:request_id=str(UUID(data.get('request_id','')))
        except (ValueError,TypeError,AttributeError) as error:raise ValueError('운전 요청 UUID가 필요합니다.') from error
        pallet = validate_pallet_id(data.get('pallet_id',''))
        job = data.get('job_id')
        if not isinstance(job,str) or data.get('position_ready') is not True:
            raise ValueError('주문 선택과 실제 시작 위치 확인이 필요합니다.')
        with self.lock:
            with closing(connect(self.database)) as db:
                old=db.execute('SELECT * FROM EQUIPMENT_RUNS WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if (old['owner_hash'],old['job_id'],old['expected_pallet_id'],old['device_id']) != (owner,job,pallet,self.device_id):
                        raise ValueError('운전 요청 UUID 내용이 다릅니다.')
                    return self.snapshot(owner,old)
            if not self.snapshot(owner)['can_start']:
                raise ValueError('라즈베리파이·UNO·카메라 준비와 이전 회차 종료를 확인하세요.')
            with closing(connect(self.database)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM EQUIPMENT_RUNS WHERE finished_at IS NULL').fetchone():
                    raise ValueError('다른 UNO 회차가 진행 중입니다.')
                order=db.execute('SELECT status FROM ORDERS WHERE job_id=?',(job,)).fetchone()
                p=db.execute('SELECT current_status FROM PALLETS WHERE pallet_id=?',(pallet,)).fetchone()
                if not order or order[0]!='PENDING' or not p or p[0]!='READY' or db.execute('SELECT 1 FROM JOB_PALLET_MAPPINGS WHERE (job_id=? OR pallet_id=?) AND (is_active=1 OR unmapped_at IS NULL)',(job,pallet)).fetchone():
                    raise ValueError('주문·팔레트가 배정 가능한 상태가 아닙니다.')
                run_id=str(uuid4())
                db.execute("INSERT INTO EQUIPMENT_RUNS(run_id,request_id,device_id,mode,job_id,expected_pallet_id,account_id,owner_hash,state) VALUES(?,?,?,'REMOTE',?,?,?,?,'STARTING')",(run_id,request_id,self.device_id,job,pallet,account_id,owner))
                db.execute('INSERT INTO REMOTE_RUN_CONTROL VALUES(?,?)',(run_id,self.clock()))
                self._queue(db,'start',{'position_ready':True},run_id)
            return self.snapshot(owner)

    def _owned(self, db, owner):
        run=self._current(db)
        if not run or run['finished_at'] or run['owner_hash']!=owner:
            raise ValueError('진행 중인 회차를 시작한 로그인 세션이 필요합니다.')
        return run

    def _mapping_valid(self, db, run):
        return bool(db.execute("SELECT 1 FROM JOB_PALLET_MAPPINGS m JOIN ORDERS o ON o.job_id=m.job_id JOIN PALLETS p ON p.pallet_id=m.pallet_id WHERE m.mapping_id=? AND m.job_id=? AND m.pallet_id=? AND m.is_active=1 AND m.unmapped_at IS NULL AND o.status='IN_PROGRESS' AND p.current_status='MAPPED'",(run['mapping_id'],run['job_id'],run['pallet_id'])).fetchone())

    def heartbeat(self, owner):
        with self.lock, closing(connect(self.database)) as db, db:
            run=self._current(db)
            if not run or run['owner_hash']!=owner:raise ValueError('운전 세션 소유자가 다릅니다.')
            if not run['finished_at']:
                db.execute('UPDATE REMOTE_RUN_CONTROL SET owner_heartbeat=? WHERE run_id=?',(self.clock(),run['run_id']))

    def stop(self, owner):
        with self.lock, closing(connect(self.database)) as db, db:
            run=self._current(db)
            self._fail(db,run,'관리자 원격 정지','STOPPED')
            if not run or run['finished_at']:self._queue(db,'stop',{})
        return self.snapshot(owner)

    def timings(self, owner, values):
        values=Bench.validate_timings(values)
        with self.lock, closing(connect(self.database)) as db, db:
            if db.execute('SELECT 1 FROM EQUIPMENT_RUNS WHERE device_id=? AND finished_at IS NULL',(self.device_id,)).fetchone():
                raise ValueError('운전 중에는 시간을 바꿀 수 없습니다.')
            if self.clock()-self._device(db)['last_seen']>3:raise ValueError('라즈베리파이 연결 대기')
            self._queue(db,'timings',{'values':values})
        return self.snapshot(owner)

    def retry(self, owner):
        with self.lock, closing(connect(self.database)) as db:
            run=self._owned(db,owner)
            status=json.loads(self._device(db)['status_json'])
            if run['database_status']!='error' or not status.get('trial',{}).get('qr_ack'):
                raise ValueError('QR 확인 후 실패한 DB 배정만 재시도할 수 있습니다.')
        with self.lock:self._bind_report(run,status)
        return self.snapshot(owner)

    def confirm(self, owner):
        with self.lock:
            if not self.snapshot(owner)['can_confirm']:raise ValueError('QR·DB·장비 완료와 회차 소유자를 확인하세요.')
            with closing(connect(self.database)) as db, db:
                run=self._owned(db,owner)
                if not self._mapping_valid(db,run):
                    raise ValueError('활성 주문·팔레트 배정이 일치하지 않습니다.')
                if not db.execute("SELECT 1 FROM REMOTE_COMMANDS WHERE run_id=? AND action='confirm' AND state IN ('PENDING','SENT','ACKED')",(run['run_id'],)).fetchone():
                    db.execute('UPDATE EQUIPMENT_RUNS SET operator_confirmed_at=CURRENT_TIMESTAMP WHERE run_id=?',(run['run_id'],))
                    self._queue(db,'confirm',{},run['run_id'])
            return self.snapshot(owner)

    def _bind_report(self, run, status):
        self.run_id=run['run_id']
        self.expected_pallet=run['expected_pallet_id']
        self.bench=SimpleNamespace(trial=status['trial'])
        try:
            self._bind_order()
        except (ValueError,sqlite3.Error) as error:
            with closing(connect(self.database)) as db, db:
                db.execute("UPDATE EQUIPMENT_RUNS SET database_status='error',database_error=? WHERE run_id=?",(str(error),run['run_id']))

    def poll(self, data):
        boot=data.get('boot_id')
        seq=data.get('sequence')
        status=data.get('status')
        if not isinstance(boot,str) or len(boot)>80 or not boot or type(seq) is not int or seq<0 or not isinstance(status,dict):
            raise ValueError('장비 보고 형식 오류')
        if status.get('mode') not in {'LIVE','DEMO'} or not isinstance(status.get('trial'),dict) or not isinstance(status.get('timings'),dict) or not isinstance(status.get('events'),list):
            raise ValueError('장비 상태 형식 오류')
        if not isinstance(data.get('results',[]),list):raise ValueError('장비 결과 목록 형식 오류')
        if status.get('state') not in {'IDLE','STARTING','PUSHING','RETURNING','WAIT_IR','ROBOT_WAIT','SECOND_BELT','WAIT_IR2','AWAIT_CONFIRM','SUCCESS','STOPPED','ERROR','QR_UNCONFIRMED'}:
            raise ValueError('장비 상태 이름 오류')
        Bench.validate_timings(status['timings'].get('values'))
        for field in ('connected','ready','clear_view','camera_ok','ir_clear','ir2_clear'):
            if type(status.get(field)) is not bool:raise ValueError('장비 상태 플래그 오류')
        for value in status['trial'].values():
            if isinstance(value,str) and len(value)>200:raise ValueError('장비 보고 문자열이 너무 깁니다.')
        if len(json.dumps(data))>60000:raise ValueError('장비 상태 보고가 너무 큽니다.')
        run_for_binding=None
        with self.lock:
            with closing(connect(self.database)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                d=self._device(db)
                if status['mode']!=d['hardware_mode']:raise ValueError('등록된 LIVE/DEMO 모드와 다릅니다.')
                if d['boot_id'] and d['boot_id']!=boot and self.clock()-d['last_seen']<6:
                    raise ValueError('동일 장비의 다른 에이전트가 접속 중입니다. 기존 프로세스를 종료하고 6초 후 재접속하세요.')
                fresh=d['boot_id']!=boot or seq>d['sequence']
                if d['boot_id']!=boot:
                    self._fail(db,self._current(db),'라즈베리파이 재시작 · 자동 재운전 없음','INTERRUPTED')
                    db.execute("UPDATE REMOTE_COMMANDS SET state='CANCELLED' WHERE device_id=? AND action!='stop' AND state IN ('PENDING','SENT')",(self.device_id,))
                if fresh:
                    db.execute('UPDATE REMOTE_DEVICES SET boot_id=?,sequence=?,last_seen=?,status_json=? WHERE device_id=?',(boot,seq,self.clock(),json.dumps(status),self.device_id))
                    for result in data.get('results',[])[:10]:
                        if not isinstance(result,dict):continue
                        db.execute("UPDATE REMOTE_COMMANDS SET state=?,result=? WHERE device_id=? AND command_id=? AND state='SENT'",('ACKED' if result.get('ok') is True else 'FAILED',str(result.get('error',''))[:500],self.device_id,result.get('command_id')))
                    run=self._current(db)
                    if run and not run['finished_at'] and data.get('run_id')==run['run_id']:
                        trial=status['trial']
                        state=status.get('state','ERROR')
                        if state=='SUCCESS':
                            eligible=DatabaseWorker.eligible(status)
                            if run['database_status']=='mapped' and run['operator_confirmed_at'] and eligible and trial.get('ejection_confirmed') and self._mapping_valid(db,run):
                                db.execute("UPDATE EQUIPMENT_RUNS SET state='SUCCESS',database_status='success',trial_json=?,finished_at=CURRENT_TIMESTAMP WHERE run_id=?",(json.dumps(trial),run['run_id']))
                            else:self._fail(db,run,'원격 성공 조건·DB·배출 확인 불일치')
                        elif state in {'ERROR','STOPPED','QR_UNCONFIRMED'}:
                            self._fail(db,run,status.get('error') or state,state)
                        else:
                            db.execute('UPDATE EQUIPMENT_RUNS SET state=?,trial_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=?',(state,json.dumps(trial),run['run_id']))
                            if trial.get('qr_ok') and trial.get('qr_ack') and run['database_status']=='waiting':run_for_binding=run
                    # A rejected start must not leave a phantom active run.
                    failed=db.execute("SELECT result,run_id FROM REMOTE_COMMANDS WHERE device_id=? AND action='start' AND state='FAILED' ORDER BY rowid DESC LIMIT 1",(self.device_id,)).fetchone()
                    if failed and run and failed['run_id']==run['run_id']:self._fail(db,run,failed['result'] or '라즈베리파이가 시작 명령을 거절했습니다.')
                self._sweep(db)
            if run_for_binding:self._bind_report(run_for_binding,status)
            with closing(connect(self.database)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                run=self._current(db)
                permit=bool(run and not run['finished_at'])
                command=None
                if fresh:
                    row=db.execute("SELECT * FROM REMOTE_COMMANDS WHERE device_id=? AND state='PENDING' AND expires_at>=? ORDER BY (action='stop') DESC,rowid LIMIT 1",(self.device_id,self.clock())).fetchone()
                    if row:
                        db.execute("UPDATE REMOTE_COMMANDS SET state='SENT' WHERE command_id=?",(row['command_id'],))
                        command={k:row[k] for k in ('command_id','run_id','action','expires_at')}
                        command['payload']=json.loads(row['payload_json'])
                return dict(server_time=self.clock(),command=command,allow_run=permit,run_id=run['run_id'] if permit else None)


def get_remote(app):
    lock=app.extensions.setdefault('remote_equipment_lock',threading.RLock())
    with lock:
        if 'remote_equipment' not in app.extensions:
            app.extensions['remote_equipment']=RemoteEquipment(app.config['ORDER_DB'],app.config['REMOTE_DEVICE_ID'])
            app.extensions['remote_equipment'].start_worker()
            atexit.register(app.extensions['remote_equipment'].close)
        return app.extensions['remote_equipment']


def create_device_api():
    api=Blueprint('devices',__name__,url_prefix='/api/devices')

    @api.before_request
    def authenticate():
        device=request.view_args.get('device_id') if request.view_args else None
        if device!=current_app.config['REMOTE_DEVICE_ID']:return {'error':'장비 인증 실패'},401
        with closing(connect(current_app.config['ORDER_DB'])) as db:
            row=db.execute('SELECT token_hash FROM REMOTE_DEVICES WHERE device_id=? AND enabled=1',(device,)).fetchone()
        import hmac
        if not row or not hmac.compare_digest(row[0],token_hash(bearer_token())):return {'error':'장비 인증 실패'},401

    @api.errorhandler(ValueError)
    def invalid(error):return {'error':str(error)},409

    @api.errorhandler(sqlite3.Error)
    def db_error(error):return {'error':'장비 DB 처리 실패'},503

    @api.after_request
    def no_cache(response):
        response.headers['Cache-Control']='no-store'
        return response

    @api.post('/<device_id>/poll')
    def poll(device_id):
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return {'error':'JSON 객체 필요'},400
        return get_remote(current_app).poll(data)

    @api.post('/<device_id>/frame')
    def frame(device_id):
        raw=request.get_data()
        if request.mimetype!='image/jpeg' or not raw or len(raw)>512000:return {'error':'JPEG 형식·크기 오류'},400
        decoded=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if decoded is None or decoded.shape[0]>1080 or decoded.shape[1]>1920:return {'error':'JPEG 해상도 오류'},400
        with closing(connect(current_app.config['ORDER_DB'])) as db, db:
            d=db.execute('SELECT boot_id,last_seen FROM REMOTE_DEVICES WHERE device_id=?',(device_id,)).fetchone()
            if not d or request.headers.get('X-Device-Boot')!=d['boot_id'] or time.time()-d['last_seen']>4:return {'error':'활성 장비 세션 필요'},409
            db.execute('UPDATE REMOTE_DEVICES SET frame_jpeg=?,frame_at=? WHERE device_id=?',(raw,time.time(),device_id))
        return Response(status=204)
    return api
