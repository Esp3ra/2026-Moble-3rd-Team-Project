"""A registered, opt-in manual procedure for idle mixed-trial standby recovery."""
import threading
from .common import *
from .devices import SimDevice

PROCEDURE='AGV_IDLE_STANDBY_RECOVERY_V1'


class TrialRecovery:
    def __init__(self,engine):
        self.engine=engine;self.host=None
        self.thread=None
        with engine.app.app_context(),transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS V1_TRIAL_RECOVERY(
                command_id TEXT PRIMARY KEY,owner TEXT NOT NULL,state TEXT NOT NULL,
                actor TEXT NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL)''')

    def _stale(self,db):
        db.execute("UPDATE V1_TRIAL_RECOVERY SET state='UNKNOWN',updated_at=? WHERE owner!=? AND state IN('ACCEPTED','RUNNING')",(now(),self.engine.instance_id))

    def result(self,command_id):
        identifier(command_id);self.engine.acquire()
        with transaction() as db:
            self._stale(db)
            row=db.execute('SELECT state FROM V1_TRIAL_RECOVERY WHERE command_id=?',(command_id,)).fetchone()
            require(row is not None,'수동 복구 요청이 없습니다.',404,'NOT_FOUND')
            return dict(command_id=command_id,device_id='AGV',state=row['state'])

    def _check(self,db):
        plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
        require(not plant['cycle_id'] and not self.engine.cycle(db) and not plant['auto_enabled'],
                '진행 회차·자동운전은 현장 확인으로 먼저 중단·대조하세요. 대기 상태만 복구할 수 있습니다.',409,'RECONCILE_REQUIRED','OPERATOR')
        require(not db.execute("SELECT 1 FROM V1_COMMANDS WHERE state IN('ACCEPTED','RUNNING') OR (state IN('UNKNOWN','FAILED') AND action NOT IN('PAUSE','RESUME'))").fetchone(),
                '미완료 동작 명령은 현장 확인이 필요합니다.',409,'RECONCILE_REQUIRED','OPERATOR')
        devices={d['device_id']:d for d in self.engine.device_states(db)}
        require(devices.get('LINE',{}).get('online') and devices['LINE']['state']=='IDLE' and not devices['LINE']['active_command_id'],
                'LINE 연결과 실제 정지를 먼저 복구하세요. 정상 정지(PAUSED)는 기존 정지 해제로 처리하세요.',503,'DEVICE_UNAVAILABLE','OPERATOR')
        require(devices.get('ARM',{}).get('active_command_id') is None,'조립 대역이 동작 중입니다.',409,'BAD_TRANSITION')

    def submit(self,actor,data):
        require(data['device_id']=='AGV' and data['procedure_id']==PROCEDURE,'등록된 AGV 대기 복구 절차를 선택하세요.',422,'BAD_TRANSITION')
        require(self.host is not None,'로컬 AGV 복구 호스트가 연결된 혼합시험 실행기가 필요합니다.',503,'DEVICE_UNAVAILABLE')
        scope='trial-recovery:'+actor
        with self.engine.lock:
            self.engine.acquire()
            with transaction() as db:
                self._stale(db)
                prior=cached(db,scope,data['request_id'],data)
                if prior:return prior
                require(not db.execute("SELECT 1 FROM V1_TRIAL_RECOVERY WHERE state IN('ACCEPTED','RUNNING')").fetchone(),
                        '복구 결과를 먼저 조회하세요.',409,'BAD_TRANSITION')
                self._check(db)
                command=uid();result=dict(command_id=command,device_id='AGV',state='ACCEPTED')
                db.execute('INSERT INTO V1_TRIAL_RECOVERY VALUES(?,?,?,?,?,?)',(command,self.engine.instance_id,'ACCEPTED',actor,data['reason'],now()))
                db.execute("UPDATE V1_PLANT SET state='RECOVERY_REQUIRED',auto_enabled=0,updated_at=?",(now(),))
                remember(db,scope,data['request_id'],data,result,202)
            self.thread=threading.Thread(target=self._run,args=(command,),name='trial-standby-recovery',daemon=True)
            self.thread.start()
            return result,202

    def _run(self,command):
        with self.engine.app.app_context(),self.engine.lock:
            try:
                with transaction() as db:
                    self.engine.assert_owner(db);self._check(db)
                    row=db.execute('SELECT * FROM V1_TRIAL_RECOVERY WHERE command_id=?',(command,)).fetchone()
                    db.execute("UPDATE V1_TRIAL_RECOVERY SET state='RUNNING',updated_at=? WHERE command_id=?",(now(),command))
                self.host.recover_standby(row['reason'])
                # Only the explicitly simulated ARM is restarted; no physical ARM commands.
                self.engine.clients['ARM']=SimDevice('ARM')
                self.engine.refresh_devices()
                with transaction() as db:
                    self.engine.assert_owner(db);self._check(db)
                    devices=self.engine.device_states(db)
                    require(all(d['online'] and d['state']=='IDLE' and d['active_command_id'] is None for d in devices),
                            '복구 후 실제 대기 상태가 확인되지 않았습니다.',409,'RECONCILE_REQUIRED')
                    # End uncertain idle control requests as STOPPED, never fabricate COMPLETED events.
                    db.execute("UPDATE V1_COMMANDS SET state='STOPPED',updated_at=? WHERE state IN('UNKNOWN','FAILED') AND action IN('PAUSE','RESUME')",(now(),))
                    db.execute("UPDATE V1_TICKETS SET state='STOPPED' WHERE kind='PLANT' AND state IN('UNKNOWN','ACCEPTED','RUNNING')")
                    state='READY' if self.engine.ready(db) else 'NOT_READY'
                    db.execute("UPDATE V1_PLANT SET state=?,auto_enabled=0,pause_ids='{}',action_request=NULL,updated_at=?",(state,now()))
                    db.execute("UPDATE V1_TRIAL_RECOVERY SET state='COMPLETED',updated_at=? WHERE command_id=?",(now(),command))
                    db.execute("UPDATE V1_TRIAL_RECOVERY SET state='STOPPED',updated_at=? WHERE command_id!=? AND state='UNKNOWN'",(now(),command))
                    db.execute('INSERT INTO V1_AUDIT VALUES(?,?,?,?,?)',(uid(),row['actor'],'MIXED_TRIAL_STANDBY_RECOVERY',dump(dict(command_id=command,procedure_id=PROCEDURE,reason=row['reason'],plant_state=state)),now()))
            except Exception as error:
                with transaction() as db:
                    db.execute("UPDATE V1_TRIAL_RECOVERY SET state='UNKNOWN',updated_at=? WHERE command_id=?",(now(),command))
                    db.execute("UPDATE V1_PLANT SET state='RECOVERY_REQUIRED',auto_enabled=0,updated_at=?",(now(),))
                    db.execute('INSERT INTO V1_AUDIT VALUES(?,?,?,?,?)',(uid(),'SERVER','MIXED_TRIAL_RECOVERY_HELD',dump(dict(command_id=command,error_type=type(error).__name__)),now()))

    def close(self):
        if self.thread:self.thread.join(timeout=20)
