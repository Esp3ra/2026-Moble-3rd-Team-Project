"""User-authorized ngrok bench test. Separate from production device APIs and orders."""
from pathlib import Path
from contextlib import closing,contextmanager
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime,timezone

from flask import Blueprint,current_app,request
from factory_v1.common import Fault,admin,identifier,uid,now,require,dump,digest
from factory_v1.api import internal_client
from wireless_pi.cycle_progress import STAGES

ACTIONS={'PING':0,'STOP':0,'BELT':500,'ACTUATOR_FORWARD':1000,'ACTUATOR_REVERSE':1000,'RUN_CYCLE':120000,'QR_CONFIRM':0}
TERMINAL={'COMPLETED','FAILED','STOPPED','UNKNOWN'}


class WirelessBench:
    def __init__(self,path,settings,clock=time.time):
        self.path,self.settings,self.clock=Path(path),settings,clock;self.lock=threading.RLock()
        with self.tx() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS device(id INTEGER PRIMARY KEY,boot TEXT,seq INTEGER NOT NULL DEFAULT 0,fingerprint TEXT,reply TEXT,status TEXT NOT NULL DEFAULT '{}',seen REAL NOT NULL DEFAULT 0);
          INSERT OR IGNORE INTO device(id) VALUES(1);
          CREATE TABLE IF NOT EXISTS commands(command_id TEXT PRIMARY KEY,action TEXT NOT NULL,boot TEXT,state TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,updated_at TEXT NOT NULL,message TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS cycle_qr(command_id TEXT PRIMARY KEY,waiting_since TEXT NOT NULL,pallet_id TEXT,frame_id TEXT,captured_at TEXT,confirm_id TEXT UNIQUE);
        ''')
        with self.tx() as db:
            if 'parent_id' not in {r[1] for r in db.execute('PRAGMA table_info(commands)')}:db.execute('ALTER TABLE commands ADD COLUMN parent_id TEXT')

    @contextmanager
    def tx(self):
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');yield db

    def expire(self,db):
        db.execute("UPDATE commands SET state='UNKNOWN',message='전달 유효시간 초과 · 자동 재전송 없음',updated_at=? WHERE state='PENDING' AND expires<?",(now(),self.clock()))
        db.execute("UPDATE commands SET state='UNKNOWN',message='결과 회신 미확인 · 자동 재전송 없음',updated_at=? WHERE state='SENT' AND created < ? - CASE WHEN action='RUN_CYCLE' THEN 150 ELSE 8 END",(now(),self.clock()))

    def snapshot(self):
        with self.tx() as db:
            self.expire(db);device=db.execute('SELECT * FROM device').fetchone();status=json.loads(device['status'])
            commands=[dict(r) for r in db.execute('SELECT command_id,action,state,updated_at,message FROM commands ORDER BY created DESC,rowid DESC LIMIT 20')]
            cycle=status.get('cycle')
            if cycle:
                cycle=dict(cycle);record=db.execute('SELECT * FROM cycle_qr WHERE command_id=?',(cycle['command_id'],)).fetchone()
                cycle.update(pallet_id=record['pallet_id'] if record else None,qr_confirmed=bool(record and record['confirm_id'] and db.execute("SELECT 1 FROM commands WHERE command_id=? AND state='COMPLETED'",(record['confirm_id'],)).fetchone()))
            return dict(enabled=True,cycle=cycle,transport=self.settings.get('transport','NGROK_HTTPS_TEST'),online=self.clock()-device['seen']<3 and bool(device['boot']),serial_connected=status.get('serial_connected',False),serial_ready=status.get('serial_ready',False),protocol=status.get('protocol','UNKNOWN'),message=status.get('message','Pi 접속 대기'),last_seen=datetime.fromtimestamp(device['seen'],timezone.utc).isoformat().replace('+00:00','Z') if device['seen'] else None,lines=status.get('lines',[]),busy=any(r['state'] in ('PENDING','SENT') for r in commands),commands=commands)

    def queue(self,command_id,action):
        identifier(command_id);require(isinstance(action,str) and action in ACTIONS,'지원하지 않는 시험 동작입니다.')
        with self.tx() as db:
            self.expire(db);old=db.execute('SELECT * FROM commands WHERE command_id=?',(command_id,)).fetchone()
            if old:
                require(old['action']==action,'같은 요청 번호의 동작이 다릅니다.',409,'CONFLICT');return dict(old)
            device=db.execute('SELECT * FROM device').fetchone();status=json.loads(device['status'])
            if action!='STOP':
                require(device['boot'] and self.clock()-device['seen']<3,'Pi가 연결되지 않았습니다.',503,'DEVICE_UNAVAILABLE')
                require(not db.execute("SELECT 1 FROM commands WHERE state IN('PENDING','SENT')").fetchone(),'이전 시험 결과를 기다리세요.',409,'CONFLICT')
            if action not in ('PING','STOP'):
                require(status.get('serial_ready') is True,'UNO 연결·펌웨어를 확인하세요.',503,'DEVICE_UNAVAILABLE')
                require(action!='BELT' or status.get('protocol') in ('WIRELESS_BENCH','WIRELESS_BENCH_10S'),'컨베이어 단독 시험 펌웨어를 업로드하세요.',409,'BAD_TRANSITION')
                require(not action.startswith('ACTUATOR_') or status.get('protocol') in ('LEGACY_BENCH','WIRELESS_BENCH','WIRELESS_BENCH_10S','UNO_SIM_QR_V2'),'지원되는 UNO 시험 펌웨어를 연결하세요.',409,'BAD_TRANSITION')
                require(action!='RUN_CYCLE' or status.get('protocol')=='UNO_SIM_QR_V2','QR 대기형 RobotWorkSimulation 펌웨어를 업로드하세요.',409,'BAD_TRANSITION')
                require(action!='QR_CONFIRM','QR 성공 확인은 서버가 현재 회차에만 발행합니다.',403,'FORBIDDEN')
                if action=='RUN_CYCLE':
                    unknown=db.execute("SELECT rowid FROM commands WHERE action='RUN_CYCLE' AND state='UNKNOWN' ORDER BY rowid DESC LIMIT 1").fetchone()
                    require(not unknown or db.execute("SELECT 1 FROM commands WHERE action='STOP' AND state='COMPLETED' AND rowid>?",(unknown[0],)).fetchone(),'이전 회차 결과가 미확인입니다. 전체 정지 회신과 현장을 확인하세요.',409,'RECONCILE_REQUIRED')
            if action=='STOP':db.execute("UPDATE commands SET state='STOPPED',message='정지 요청으로 취소',updated_at=? WHERE state='PENDING'",(now(),))
            db.execute("INSERT INTO commands(command_id,action,boot,state,created,expires,updated_at,message) VALUES(?,?,?,'PENDING',?,?,?,'')",(command_id,action,device['boot'],self.clock(),self.clock()+(30 if action=='STOP' else 2),now()))
            return dict(db.execute('SELECT * FROM commands WHERE command_id=?',(command_id,)).fetchone())

    def poll(self,data):
        require(isinstance(data,dict) and set(data)=={'boot_id','sequence','status','results'},'시험 보고 형식 오류')
        boot=identifier(data['boot_id']);seq=data['sequence'];status=data['status'];results=data['results']
        require(type(seq) is int and 1<=seq<2**32,'보고 순번 오류')
        fields={'serial_connected','serial_ready','protocol','message','lines'}
        require(isinstance(status,dict) and set(status) in (fields,fields|{'cycle'}),'장비 상태 형식 오류')
        require(type(status['serial_connected']) is bool and type(status['serial_ready']) is bool and status['protocol'] in ('UNKNOWN','NETWORK_ONLY','LEGACY_BENCH','WIRELESS_BENCH','WIRELESS_BENCH_10S','UNO_SIM_QR_V2'),'장비 상태 오류')
        if status.get('cycle') is not None:
            cycle=status['cycle'];require(isinstance(cycle,dict) and set(cycle)=={'command_id','stage','line','changed_at'},'회차 상태 형식 오류')
            identifier(cycle['command_id']);require(cycle['stage'] in STAGES and isinstance(cycle['line'],str) and len(cycle['line'])<=120 and isinstance(cycle['changed_at'],str) and len(cycle['changed_at'])<=40,'회차 진행 상태 오류')
        require(isinstance(status['message'],str) and len(status['message'])<=240 and isinstance(status['lines'],list) and len(status['lines'])<=10 and all(isinstance(line,str) and len(line)<=120 for line in status['lines']),'시리얼 기록 길이 오류')
        require(isinstance(results,list) and len(results)<=20,'결과 목록 오류')
        with self.tx() as db:
            device=db.execute('SELECT * FROM device').fetchone()
            if device['boot']==boot:
                if seq==device['seq']:
                    require(device['fingerprint']==digest(data),'같은 순번의 내용이 다릅니다.',409,'CONFLICT')
                    reply=json.loads(device['reply'])
                    if reply['command']:
                        old=db.execute('SELECT state,expires FROM commands WHERE command_id=?',(reply['command']['command_id'],)).fetchone()
                        if not old or old['state']!='SENT' or old['expires']<=self.clock():reply['command']=None
                        else:reply['command']['valid_for_ms']=max(1,int((old['expires']-self.clock())*1000))
                    return reply
                require(seq>device['seq'],'이전 보고 순번입니다.',409,'CONFLICT')
            else:db.execute("UPDATE commands SET state='UNKNOWN',message='Pi 재시작 · 자동 재실행 없음',updated_at=? WHERE state IN('PENDING','SENT') AND action!='STOP'",(now(),))
            acknowledged=[]
            for result in results:
                require(isinstance(result,dict) and set(result)=={'command_id','state','message'},'명령 결과 형식 오류')
                command_id=identifier(result['command_id']);require(result['state'] in TERMINAL and isinstance(result['message'],str) and len(result['message'])<=240,'명령 결과 오류')
                row=db.execute('SELECT * FROM commands WHERE command_id=?',(command_id,)).fetchone()
                if row and row['boot']==boot and row['state'] in ('SENT','UNKNOWN'):
                    db.execute('UPDATE commands SET state=?,message=?,updated_at=? WHERE command_id=?',(result['state'],result['message'],now(),command_id))
                acknowledged.append(command_id)
            self.expire(db)
            pending=db.execute("SELECT * FROM commands WHERE state='PENDING' ORDER BY CASE WHEN action='STOP' THEN 0 ELSE 1 END,created LIMIT 1").fetchone()
            command=None
            if pending:
                command=dict(command_id=pending['command_id'],action=pending['action'],duration_ms=ACTIONS[pending['action']],valid_for_ms=max(1,min(30000,int((pending['expires']-self.clock())*1000))))
                if pending['parent_id']:command['parent_command_id']=pending['parent_id']
                db.execute("UPDATE commands SET state='SENT',boot=?,updated_at=? WHERE command_id=?",(boot,now(),pending['command_id']))
            reply=dict(acknowledged=acknowledged,command=command)
            db.execute('UPDATE device SET boot=?,seq=?,fingerprint=?,reply=?,status=?,seen=? WHERE id=1',(boot,seq,digest(data),dump(reply),dump(status),self.clock()))
            return reply

    def confirm_from_vision(self,vision):
        if vision.simulation:return
        with self.tx() as db:
            device=db.execute('SELECT * FROM device').fetchone();cycle=json.loads(device['status']).get('cycle')
            if not cycle or cycle['stage']!='WAIT_QR':return
            parent=db.execute("SELECT * FROM commands WHERE command_id=? AND action='RUN_CYCLE' AND state='SENT' AND boot=?",(cycle['command_id'],device['boot'])).fetchone()
            if not parent:return
            db.execute('INSERT OR IGNORE INTO cycle_qr(command_id,waiting_since) VALUES(?,?)',(cycle['command_id'],now()))
            wait=db.execute('SELECT * FROM cycle_qr WHERE command_id=?',(cycle['command_id'],)).fetchone()
            if wait['confirm_id']:return
            boundary=wait['waiting_since'];parent_id=cycle['command_id'];boot=device['boot']
        result=vision.qr.status()
        if result.get('state')!='READ' or result.get('pallet_id') not in {f'PALLET-{i:02}' for i in range(1,9)} or not result.get('frame_id'):return
        try:
            captured=datetime.fromisoformat(result['captured_at'].replace('Z','+00:00'))
            if captured<datetime.fromisoformat(boundary.replace('Z','+00:00')) or not 0<=(datetime.now(timezone.utc)-captured).total_seconds()<2:return
        except (ValueError,TypeError,KeyError):return
        with self.tx() as db:
            device=db.execute('SELECT * FROM device').fetchone();current=json.loads(device['status']).get('cycle')
            parent=db.execute("SELECT 1 FROM commands WHERE command_id=? AND state='SENT'",(parent_id,)).fetchone()
            record=db.execute('SELECT * FROM cycle_qr WHERE command_id=?',(parent_id,)).fetchone()
            stop=db.execute("SELECT 1 FROM commands WHERE action='STOP' AND state IN('PENDING','SENT')").fetchone()
            if stop or not parent or device['boot']!=boot or not current or current['command_id']!=parent_id or current['stage']!='WAIT_QR' or record['confirm_id']:return
            command_id=uid()
            db.execute("INSERT INTO commands(command_id,action,boot,state,created,expires,updated_at,message,parent_id) VALUES(?,'QR_CONFIRM',?,'PENDING',?,?,?,'',?)",(command_id,boot,self.clock(),self.clock()+2,now(),parent_id))
            db.execute('UPDATE cycle_qr SET pallet_id=?,frame_id=?,captured_at=?,confirm_id=? WHERE command_id=?',(result['pallet_id'],result['frame_id'],result['captured_at'],command_id,parent_id))


def install(app):
    folder=Path(app.config['ORDER_DB']).parent;path=folder/'wireless-test.json'
    settings=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    enabled=settings.get('enabled') is True and isinstance(settings.get('token_hash'),str) and len(settings['token_hash'])==64
    app.config['WIRELESS_TEST_ENABLED']=enabled
    bridge=WirelessBench(folder/'wireless-test.sqlite3',settings) if enabled else None
    app.extensions['wireless_bench']=bridge
    control=Blueprint('wireless_bench_admin',__name__,url_prefix='/api/v1/wireless-test')

    @control.before_request
    def internal():require(internal_client(),'시험 조작은 내부 관리자 전용입니다.',403,'FORBIDDEN')

    @control.after_request
    def no_cache(response):response.headers['Cache-Control']='no-store';return response

    @control.get('/status')
    @admin()
    def status():
        if bridge:return bridge.snapshot()
        return dict(enabled=False,online=False,serial_connected=False,serial_ready=False,protocol='UNKNOWN',message='ngrok 단독 시험 미활성',last_seen=None,lines=[],busy=False,commands=[])

    @control.post('/commands')
    @admin()
    def command():
        require(bridge is not None,'무선 시험을 먼저 활성화하세요.',503,'DEVICE_UNAVAILABLE')
        data=request.get_json(silent=True);require(isinstance(data,dict) and set(data)=={'request_id','action'},'시험 요청 형식 오류')
        from factory_v1.common import transaction
        with transaction() as db:require(not app.extensions['factory_v1'].cycle(db),'자동 공정 실행 중에는 단독 시험을 할 수 없습니다.',409,'BAD_TRANSITION')
        return bridge.queue(identifier(data['request_id']),data['action']),202

    @control.post('/qr-read')
    @admin()
    def qr_read():
        require(bridge is not None,'무선 시험을 먼저 활성화하세요.',503,'DEVICE_UNAVAILABLE')
        # Pure camera read: no order, binding, cycle, event or motor command writes.
        return app.extensions['factory_v1'].vision.scan_test_qr()

    device=Blueprint('wireless_bench_device',__name__)

    def authenticate():
        require(bridge is not None,'무선 시험 미활성',503,'DEVICE_UNAVAILABLE')
        if settings.get('transport')=='LAN_HTTP_TEST':require(internal_client(),'내부망 시험은 사설망에서만 접속할 수 있습니다.',403,'FORBIDDEN')
        supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
        require(secrets.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(),settings['token_hash']),'시험 장비 인증 실패',401,'UNAUTHORIZED')

    @device.get('/ping')
    def ping():
        require(settings.get('transport')=='LAN_HTTP_TEST' and bridge is not None,'내부망 시험 서버가 아닙니다.',404,'NOT_FOUND')
        require(internal_client(),'내부망 시험 전용입니다.',403,'FORBIDDEN')
        return dict(status='ok',server='mobot-main',transport='LAN_HTTP_TEST',port=request.host.split(':')[-1])

    @device.post('/wireless-test/v1/probe')
    def probe():
        require(settings.get('transport')=='LAN_HTTP_TEST','내부망 시험 전용입니다.',404,'NOT_FOUND')
        authenticate()
        data=request.get_json(silent=True)
        require(isinstance(data,dict) and set(data)=={'device_id','message'} and data['device_id']=='LINE' and isinstance(data['message'],str) and len(data['message'])<=120,'진단 메시지 형식 오류')
        return dict(status='ok',device_id='LINE',transport='LAN_HTTP_TEST',message='인증된 진단 수신 · 공정 이벤트/명령 생성 없음')

    @device.post('/wireless-test/v1/poll')
    def poll():
        authenticate()
        reply=bridge.poll(request.get_json(silent=True))
        bridge.confirm_from_vision(app.extensions['factory_v1'].vision)
        return reply

    app.register_blueprint(control);app.register_blueprint(device)
