"""Transactional command outbox and evidence-driven 12-stage orchestration."""
import threading
from .common import *
from . import contracts, placements

TRANSITIONS={
 'DISPENSE':('DISPENSING','TO_ASSEMBLY','LINE','MOVE_TO_ASSEMBLY'),
 'MOVE_TO_ASSEMBLY':('TO_ASSEMBLY','WAIT_FEED_QR',None,None),
 'ASSEMBLE':('ASSEMBLING','INSPECTING',None,None),
 'MOVE_TO_PICKUP':('TO_PICKUP','PICKING_UP','AGV','PICK_UP'),
 'PICK_UP':('PICKING_UP','DELIVERING','AGV','DELIVER'),
 'DELIVER':('DELIVERING','UNLOADING','AGV','UNLOAD'),
 'UNLOAD':('UNLOADING','RETURNING','AGV','RETURN_STANDBY'),
 'RETURN_STANDBY':('RETURNING','CLOSED',None,None)}


class Engine:
    required_devices = DEVICES

    def device_states(self,db):
        return [json.loads(row['status']) for row in db.execute('SELECT device_id,status FROM V1_DEVICES') if row['device_id'] in self.required_devices]

    def __init__(self,app,clients,vision,clock=time.time):
        self.app,self.clients,self.vision,self.clock=app,clients,vision,clock
        self.lock=threading.RLock()
        self.closed=threading.Event()
        self.worker=None
        self.instance_id=uid();self.acquired=False
        for client in clients.values():
            if hasattr(client,"call"):client.renew_lease=self.acquire

    def acquire(self):
        with self.app.app_context(),transaction() as db:
            lease=db.execute('SELECT * FROM V1_CONTROLLER_LEASE WHERE id=1').fetchone()
            if self.acquired:
                require(lease and lease['instance_id']==self.instance_id,'공정 제어권을 잃었습니다.',503,'DEVICE_UNAVAILABLE')
            else:
                require(not lease or lease['heartbeat']<time.time()-5,'다른 서버가 공정 제어권을 사용 중입니다.',503,'DEVICE_UNAVAILABLE')
                if self.cycle(db):
                    self._hold(db,'서버 재시작 · 이전 명령 결과와 실물 대조가 필요합니다.')
                    db.execute("UPDATE V1_COMMANDS SET state='UNKNOWN' WHERE attempted=1 AND state IN('ACCEPTED','RUNNING')")
                    db.execute("UPDATE V1_ARM_CHECKPOINTS SET after_frame=? WHERE state='WAIT_FRAME'",(time.monotonic(),))
                db.execute('UPDATE V1_PLANT SET auto_enabled=0 WHERE id=1')
                db.execute("UPDATE V1_DEVICES SET boot_id=?,last_seq=0 WHERE device_id='VISION'",(uid(),))
                self.acquired=True
            db.execute('INSERT INTO V1_CONTROLLER_LEASE VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET instance_id=excluded.instance_id,heartbeat=excluded.heartbeat',(self.instance_id,time.time()))

    def assert_owner(self,db):
        row=db.execute('SELECT instance_id FROM V1_CONTROLLER_LEASE WHERE id=1').fetchone()
        require(self.acquired and row and row[0]==self.instance_id,'활성 제어 주체가 아닙니다.',503,'DEVICE_UNAVAILABLE')

    def queue(self,db,device,cycle,action,params=None,ticket=None):
        require(device in self.required_devices,'시험 모드에서 제외된 장비입니다.',409,'BAD_TRANSITION')
        row=db.execute('SELECT boot_id FROM V1_DEVICES WHERE device_id=?',(device,)).fetchone()
        command=dict(command_id=uid(),device_id=device,cycle_id=cycle,action=action,params=params or {})
        contracts.command(command)
        db.execute('INSERT INTO V1_COMMANDS(command_id,device_id,cycle_id,action,params,expected_boot_id,ticket_id,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (command['command_id'],device,cycle,action,dump(command['params']),row[0],ticket,now()))
        return command['command_id']

    def cycle(self,db):return db.execute('SELECT * FROM V1_CYCLES WHERE closed=0').fetchone()

    def stage(self,db,cycle,stage):
        db.execute('UPDATE V1_CYCLES SET stage=?,updated_at=? WHERE cycle_id=?',(stage,now(),cycle['cycle_id']))
        db.execute('UPDATE V1_PLANT SET stage=?,updated_at=? WHERE id=1',(stage,now()))
        if cycle['pallet_id']:db.execute('UPDATE V1_PALLETS SET stage=?,updated_at=? WHERE pallet_id=?',(stage,now(),cycle['pallet_id']))

    def hold(self,message,device=None,command_id=None):
        with self.app.app_context(),transaction() as db:
            self.assert_owner(db)
            self._hold(db,message,device,command_id)

    def _hold(self,db,message,device=None,command_id=None):
        db.execute("UPDATE V1_PLANT SET state='RECOVERY_REQUIRED',updated_at=? WHERE id=1 AND state!='RECOVERY_REQUIRED'",(now(),))
        cycle=self.cycle(db)
        if command_id:
            command=db.execute('SELECT ticket_id FROM V1_COMMANDS WHERE command_id=?',(command_id,)).fetchone()
            if command and command['ticket_id']:db.execute("UPDATE V1_TICKETS SET state='UNKNOWN' WHERE request_id=? AND state!='COMPLETED'",(command['ticket_id'],))
        if cycle:
            message=((device or 'SERVER')+' · '+message)[:400]
            if db.execute("SELECT 1 FROM V1_HISTORY WHERE cycle_id=? AND kind='RECOVERY_REQUIRED' AND message=? AND command_id IS ?",(cycle['cycle_id'],message,command_id)).fetchone():return
            error=dict(code='RECONCILE_REQUIRED',message=message,retry='OPERATOR',trace_id=uid())
            history(db,cycle,'RECOVERY_REQUIRED',message,command_id,extra=dict(error=error,device_id=device,last_normal_stage=cycle['stage'],operator_action='장비 상태와 실물을 확인한 후 현장 확인을 기록하세요.'))

    def ready(self,db):
        if not set(self.required_devices)<=self.clients.keys():return False
        devices=self.device_states(db)
        if not (contracts.valid_order(self.vision.part_order) and placements.supports_refresh(db)):
            return False
        cameras=self.vision.ready()
        required_cameras=('cam1','cam2','cam4',self.vision.inspection_camera or 'cam3')
        return (len(devices)==len(self.required_devices)
                and all(d['online'] and d['state']=='IDLE' and d['mode']=='AUTO' for d in devices)
                and all(cameras.get(c,False) for c in required_cameras)
                and self.vision.assembly_ready() and self.vision.inspection_ready())

    def start_next(self,db):
        plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
        if not plant['auto_enabled'] or plant['state'] not in ('READY','RUNNING') or self.cycle(db) or not self.ready(db):return
        order=db.execute("SELECT * FROM V1_ORDERS WHERE factory_status='QUEUED' ORDER BY approval_seq LIMIT 1").fetchone()
        if not order:return
        cycle=uid()
        db.execute("INSERT INTO V1_CYCLES(cycle_id,order_id,stage,created_at,updated_at,flow_version) VALUES(?,?,'DISPENSING',?,?,'0.3.0')",
                   (cycle,order['order_id'],now(),now()))
        db.execute("UPDATE V1_ORDERS SET factory_status='IN_PROGRESS',progress='IN_PRODUCTION',cycle_id=?,updated_at=? WHERE order_id=?",(cycle,now(),order['order_id']))
        db.execute("UPDATE V1_PLANT SET state='RUNNING',stage='DISPENSING',cycle_id=?,updated_at=? WHERE id=1",(cycle,now()))
        cmd=self.queue(db,'LINE',cycle,'DISPENSE',ticket=plant['action_request'])
        history(db,self.cycle(db),'DISPENSE_REQUESTED','수락 순서에 따라 한 장 배출 요청을 저장했습니다.',cmd)

    def plant(self):
        with self.app.app_context(),transaction() as db:
            row=db.execute('SELECT * FROM V1_PLANT').fetchone()
            if row['state'] in ('NOT_READY','READY'):
                state='READY' if self.ready(db) else 'NOT_READY'
                if state!=row['state']:
                    db.execute('UPDATE V1_PLANT SET state=?,updated_at=? WHERE id=1',(state,now()))
                    row=db.execute('SELECT * FROM V1_PLANT').fetchone()
            cycle=self.cycle(db)
            qr=json.loads(cycle['qr']) if cycle and cycle['qr'] else None
            return dict(state=row['state'],stage=row['stage'],cycle_id=row['cycle_id'],
                devices=[json.loads(r[0]) for r in db.execute("SELECT status FROM V1_DEVICES WHERE device_id IN('LINE','ARM','AGV') ORDER BY device_id")],
                camera_ready=self.vision.ready(),updated_at=row['updated_at'],
                feed_qr={k:qr[k] for k in ('read_id','attempt','state')} if qr else None)

    def action(self,actor,data):
        object_fields(data,('request_id','action'),('reason',))
        identifier(data['request_id']);require(data['action'] in ('START','PAUSE','RESUME'),'공정 동작 오류')
        if 'reason' in data:text(data['reason'],200)
        scope='plant:'+actor
        with self.lock,self.app.app_context(),transaction() as db:
            self.assert_owner(db)
            prior=cached(db,scope,data['request_id'],data)
            if prior:return self.ticket(db,data['request_id']),202
            plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
            cycle=self.cycle(db)
            cycle_id=cycle['cycle_id'] if cycle else None
            action=data['action']
            if action=='START':
                require(plant['state'] in ('READY','NOT_READY') and not cycle,'현재 공정에서 시작할 수 없습니다.',409,'BAD_TRANSITION')
                require(self.ready(db),'세 장비, 사용 중인 카메라, 검사 해상도와 보정을 확인하세요.',503,'DEVICE_UNAVAILABLE','QUERY_FIRST')
                db.execute("UPDATE V1_PLANT SET state='READY',auto_enabled=1,action_request=?,updated_at=? WHERE id=1",(data['request_id'],now()))
                db.execute("INSERT INTO V1_TICKETS VALUES(?,'PLANT','ACCEPTED',NULL,NULL)",(data['request_id'],))
                self.start_next(db)
                if not self.cycle(db):db.execute("UPDATE V1_TICKETS SET state='COMPLETED' WHERE request_id=?",(data['request_id'],))
            elif action=='PAUSE':
                require(plant['state'] in ('READY','RUNNING','NOT_READY'),'정지 요청 중이거나 현장 복구가 필요합니다.',409,'BAD_TRANSITION')
                pause_ids={device:self.queue(db,device,cycle_id,'PAUSE',ticket=data['request_id']) for device in self.required_devices}
                db.execute("UPDATE V1_PLANT SET state='PAUSING',pause_ids=?,action_request=?,updated_at=? WHERE id=1",(dump(pause_ids),data['request_id'],now()))
                db.execute("INSERT INTO V1_TICKETS VALUES(?,'PLANT','ACCEPTED',?,NULL)",(data['request_id'],cycle_id))
            else:
                require(plant['state']=='PAUSED','모든 장비의 정상 정지 확인 후에만 해제할 수 있습니다.',409,'BAD_TRANSITION')
                require(not cycle or cycle['flow_version']=='0.3.0',
                        '이전 공정의 미완료 회차는 현장에서 대조해야 합니다.',409,'RECONCILE_REQUIRED','OPERATOR')
                ids=json.loads(plant['pause_ids'])
                require(set(ids)==set(self.required_devices),'정지 명령 대조가 필요합니다.',409,'RECONCILE_REQUIRED','OPERATOR')
                for device in self.required_devices:self.queue(db,device,cycle_id,'RESUME',{'pause_command_id':ids[device]},data['request_id'])
                db.execute("UPDATE V1_PLANT SET state='RESUMING',action_request=?,updated_at=? WHERE id=1",(data['request_id'],now()))
                db.execute("INSERT INTO V1_TICKETS VALUES(?,'PLANT','ACCEPTED',?,NULL)",(data['request_id'],cycle_id))
            response=self.ticket(db,data['request_id'])
            remember(db,scope,data['request_id'],data,response,202)
            return response,202

    def ticket(self,db,request_id):
        ticket=db.execute("SELECT * FROM V1_TICKETS WHERE request_id=? AND kind='PLANT'",(identifier(request_id),)).fetchone()
        require(ticket is not None,'공정 요청이 없습니다.',404,'NOT_FOUND')
        state=db.execute('SELECT state FROM V1_PLANT').fetchone()[0]
        return dict(request_id=request_id,state=ticket['state'],plant_state=state)

    def record(self,data):
        contracts.event(data)
        with self.lock,self.app.app_context(),transaction() as db:
            self.assert_owner(db)
            old=db.execute('SELECT * FROM V1_EVENTS WHERE event_id=? OR (device_id=? AND boot_id=? AND event_seq=?)',(data['event_id'],data['device_id'],data['boot_id'],data['event_seq'])).fetchone()
            device=db.execute('SELECT * FROM V1_DEVICES WHERE device_id=?',(data['device_id'],)).fetchone()
            if old:
                require(old['body']==dump(data),'같은 이벤트 ID 또는 순번의 내용이 다릅니다.',409,'CONFLICT','QUERY_FIRST')
                if device['boot_id']!=data['boot_id'] or data['event_seq']<=device['last_seq']:
                    return dict(event_id=data['event_id'],disposition='DUPLICATE' if old['processed'] else 'HELD_FOR_REVIEW')
            else:
                db.execute("INSERT INTO V1_EVENTS VALUES(?,?,?,?,?,'HELD_FOR_REVIEW',0)",(data['event_id'],data['device_id'],data['boot_id'],data['event_seq'],dump(data)))
            if device['boot_id']!=data['boot_id']:
                self._hold(db,'이전 부팅 또는 미확인 부팅의 회신입니다.',data['device_id'],data['command_id'])
            else:
                seq=device['last_seq']
                if data['event_seq']>seq+1:self._hold(db,'이벤트 순번 공백 · 누락 구간 복원 후 대조가 필요합니다.',data['device_id'],data['command_id'])
                while True:
                    row=db.execute('SELECT * FROM V1_EVENTS WHERE device_id=? AND boot_id=? AND event_seq=?',(data['device_id'],data['boot_id'],seq+1)).fetchone()
                    if not row:break
                    event=json.loads(row['body'])
                    if placements.awaiting_ack(db,event):
                        break
                    accepted=self.on_event(db,event)
                    # Sequence is consumed even when the stage is invalid; never replay motion.
                    seq+=1
                    db.execute('UPDATE V1_EVENTS SET processed=?,disposition=? WHERE event_id=?',(int(accepted),'RECORDED' if accepted else 'HELD_FOR_REVIEW',row['event_id']))
                    db.execute('UPDATE V1_DEVICES SET last_seq=? WHERE device_id=?',(seq,data['device_id']))
            saved=db.execute('SELECT disposition FROM V1_EVENTS WHERE event_id=?',(data['event_id'],)).fetchone()[0]
            return dict(event_id=data['event_id'],disposition=saved)

    def on_event(self,db,event):
        cycle=self.cycle(db)
        kind=event['kind']
        if kind in ('FEED_QR_READ','INSPECTION_CAPTURED','INSPECTION_UNAVAILABLE'):
            if not cycle or cycle['cycle_id']!=event['cycle_id']:
                self._hold(db,'활성 회차와 다른 비전 회신입니다.','VISION');return False
            return self.vision_event(db,cycle,event)
        command=db.execute('SELECT * FROM V1_COMMANDS WHERE command_id=?',(event['command_id'],)).fetchone()
        if not command or command['device_id']!=event['device_id'] or command['cycle_id']!=event['cycle_id'] or command['expected_boot_id']!=event['boot_id']:
            self._hold(db,'장비·명령·회차·부팅이 일치하지 않습니다.',event['device_id'],event['command_id']);return False
        if not command['attempted']:
            self._hold(db,'전송 전 명령에 대한 장비 회신입니다.',event['device_id'],event['command_id']);return False
        if kind=='ARM_PLACEMENT_REQUESTED':
            if (not cycle or cycle['cycle_id']!=event['cycle_id'] or cycle['stage']!='ASSEMBLING'
                    or command['action']!='ASSEMBLE' or command['state'] not in ('RUNNING','ACCEPTED')
                    or not placements.request_checkpoint(db,cycle,command,event)):
                self._hold(db,'ARM 장착 순서 또는 명령 상태 불일치','ARM',command['command_id']);return False
            return True
        if kind=='DEVICE_FAILED':
            db.execute("UPDATE V1_COMMANDS SET state='FAILED',error=?,updated_at=? WHERE command_id=?",(dump(event['data']),now(),command['command_id']))
            self._hold(db,event['data']['message'],event['device_id'],command['command_id']);return True
        expected='PAUSE' if kind=='DEVICE_PAUSED' else 'RESUME' if kind=='DEVICE_RESUMED' else contracts.EVENT_RULES.get(kind,(None,None))[1]
        if command['action']!=expected or command['state'] in ('FAILED','STOPPED'):
            self._hold(db,'예상하지 않은 명령 완료 종류입니다.',event['device_id'],command['command_id']);return False
        if command['state']=='COMPLETED':return True
        if expected=='ASSEMBLE' and 'part_order' in json.loads(command['params']):
            if not {1,2} <= placements.accepted_indices(db,command['command_id']):
                self._hold(db,'부품 사이 CAM2 좌표 수락 이력이 부족합니다.','ARM',command['command_id']);return False
        if expected in ('DELIVER','UNLOAD') and event['data']['destination']!=json.loads(command['params'])['destination']:
            self._hold(db,'배송 또는 하역 목적지가 다릅니다.',event['device_id'],command['command_id']);return False
        db.execute("UPDATE V1_COMMANDS SET state='COMPLETED',result_event_id=?,error=NULL,updated_at=? WHERE command_id=?",(event['event_id'],now(),command['command_id']))
        plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
        if expected in ('PAUSE','RESUME'):
            pending=db.execute("SELECT 1 FROM V1_COMMANDS WHERE ticket_id=? AND state!='COMPLETED'",(command['ticket_id'],)).fetchone()
            if not pending:
                state='PAUSED' if expected=='PAUSE' else ('RUNNING' if cycle else 'READY')
                db.execute('UPDATE V1_PLANT SET state=?,updated_at=? WHERE id=1',(state,now()))
                db.execute("UPDATE V1_TICKETS SET state='COMPLETED' WHERE request_id=?",(command['ticket_id'],))
                if expected=='RESUME' and cycle and cycle['pending_event']:
                    self.advance(db,cycle,json.loads(cycle['pending_event']))
                    db.execute('UPDATE V1_CYCLES SET pending_event=NULL WHERE cycle_id=?',(cycle['cycle_id'],))
            return True
        if not cycle or cycle['cycle_id']!=event['cycle_id']:
            self._hold(db,'이미 종료되었거나 다른 회차의 완료입니다.',event['device_id'],command['command_id']);return False
        if plant['state'] in ('PAUSING','PAUSED','RESUMING','RECOVERY_REQUIRED'):
            db.execute('UPDATE V1_CYCLES SET pending_event=? WHERE cycle_id=?',(dump(event),cycle['cycle_id']))
            history(db,cycle,'COMPLETED_WHILE_HELD','완료 증거를 보존하고 후속 명령은 보류했습니다.',command['command_id']);return True
        return self.advance(db,cycle,event)

    def advance(self,db,cycle,event):
        kind=event['kind'];action=contracts.EVENT_RULES[kind][1]
        if action not in TRANSITIONS or cycle['flow_version'] != '0.3.0':
            self._hold(db,'개정 공정과 완료 이벤트를 대조하세요.',event['device_id'],event['command_id'])
            return False
        before,after,device,next_action=TRANSITIONS[action]
        if cycle['stage']!=before:
            self._hold(db,'공정 단계와 완료 이벤트 순서가 다릅니다.',event['device_id'],event['command_id']);return False
        self.stage(db,cycle,after)
        history(db,cycle,kind,'동일 회차의 장비 완료 증거를 확인했습니다.',event['command_id'])
        if action=='DISPENSE':
            self.queue(db,device,cycle['cycle_id'],next_action)
            db.execute("UPDATE V1_TICKETS SET state='COMPLETED' WHERE request_id=(SELECT ticket_id FROM V1_COMMANDS WHERE command_id=?)",(event['command_id'],))
        elif action=='MOVE_TO_ASSEMBLY':
            qr=dict(read_id=uid(),attempt=0,state='READING')
            db.execute('UPDATE V1_CYCLES SET qr=?,qr_due=? WHERE cycle_id=?',(dump(qr),self.clock(),cycle['cycle_id']))
        elif action=='ASSEMBLE':
            self.start_inspection(db,cycle,event['command_id'])
        elif action=='RETURN_STANDBY':
            order=find_order(db,cycle['order_id'])
            normal=order['dispatch_station']==order['target_station'] and order['outcome']!='ERROR'
            db.execute('UPDATE V1_ORDERS SET factory_status=?,progress=?,outcome=?,updated_at=? WHERE order_id=?',
                ('COMPLETED' if normal else 'ERROR_DELIVERED','HUB_ARRIVED' if normal else 'SHIPPING','NORMAL' if normal else 'ERROR',now(),cycle['order_id']))
            db.execute("UPDATE V1_CYCLES SET closed=1,stage='CLOSED',updated_at=? WHERE cycle_id=?",(now(),cycle['cycle_id']))
            db.execute("UPDATE V1_PALLETS SET cycle_id=NULL,stage='AWAIT_MANUAL_RETURN',updated_at=? WHERE pallet_id=?",(now(),cycle['pallet_id']))
            db.execute("UPDATE V1_PLANT SET state='READY',stage='READY',cycle_id=NULL,updated_at=? WHERE id=1",(now(),))
        elif next_action:
            params={}
            if next_action in ('PICK_UP','DELIVER','UNLOAD'):
                inspection=db.execute('SELECT body FROM V1_INSPECTIONS WHERE inspection_id=? AND cycle_id=?',
                                      (cycle['inspection_id'],cycle['cycle_id'])).fetchone()
                if not inspection or not inspection['body'] or json.loads(inspection['body'])['verdict']!='PASS':
                    self._hold(db,'같은 회차의 저장된 검사 합격을 확인하세요.',event['device_id'],event['command_id'])
                    return False
                if next_action!='PICK_UP':
                    order=find_order(db,cycle['order_id'])
                    require(order['dispatch_station']==order['target_station'],'합격품 목적지가 주문과 다릅니다.',409,'CONFLICT')
                    params=dict(destination=order['dispatch_station'],inspection_id=cycle['inspection_id'])
                    db.execute("UPDATE V1_ORDERS SET progress='SHIPPING',updated_at=? WHERE order_id=?",(now(),cycle['order_id']))
            self.queue(db,device,cycle['cycle_id'],next_action,params)
        return True

    def start_inspection(self,db,cycle,command_id):
        previous=cycle['inspection_id']
        attempt=1
        if previous:
            parent=db.execute('SELECT attempt_number FROM V1_INSPECTIONS WHERE cycle_id=? AND inspection_id=?',
                              (cycle['cycle_id'],previous)).fetchone()
            rework=db.execute("SELECT 1 FROM V1_HISTORY WHERE cycle_id=? AND command_id=? AND inspection_id=? AND kind='REWORK_REQUESTED'",
                              (cycle['cycle_id'],command_id,previous)).fetchone()
            require(parent and rework,'재작업 명령과 이전 검사를 대조하세요.',409,'RECONCILE_REQUIRED','OPERATOR')
            attempt=parent['attempt_number']+1
        inspection_id=uid()
        db.execute('''INSERT INTO V1_INSPECTIONS(inspection_id,cycle_id,order_id,attempt_number,previous_inspection_id,
            rework_command_id,source_command_id) VALUES(?,?,?,?,?,?,?)''',
            (inspection_id,cycle['cycle_id'],cycle['order_id'],attempt,previous,command_id if previous else None,command_id))
        db.execute('UPDATE V1_CYCLES SET inspection_id=? WHERE cycle_id=?',(inspection_id,cycle['cycle_id']))
        db.execute("UPDATE V1_ORDERS SET progress='INSPECTING',updated_at=? WHERE order_id=?",(now(),cycle['order_id']))
        self.job(db,cycle,'INSPECT',{'inspection_id':inspection_id})

    def job(self,db,cycle,action,payload=None):
        data=dict(payload or {})
        data.update(after_frame=time.monotonic(),controller=self.instance_id)
        db.execute('INSERT INTO V1_VISION_JOBS(job_id,cycle_id,action,payload) VALUES(?,?,?,?)',(uid(),cycle['cycle_id'],action,dump(data)))

    def bind(self,db,cycle,qr):
        pallet=qr['pallet_id']
        row=db.execute('SELECT * FROM V1_PALLETS WHERE pallet_id=?',(pallet,)).fetchone()
        require(row is not None and row['stage']=='READY' and row['cycle_id'] is None,'팔레트가 사용 가능 상태가 아닙니다.',409,'CONFLICT','OPERATOR')
        db.execute('UPDATE V1_CYCLES SET pallet_id=? WHERE cycle_id=?',(pallet,cycle['cycle_id']))
        db.execute('UPDATE V1_ORDERS SET pallet_id=?,updated_at=? WHERE order_id=?',(pallet,now(),cycle['order_id']))
        db.execute("UPDATE V1_PALLETS SET cycle_id=?,stage='ASSEMBLING',updated_at=? WHERE pallet_id=?",(cycle['cycle_id'],now(),pallet))
        qr['state']='BOUND'
        db.execute('UPDATE V1_CYCLES SET qr=?,qr_due=NULL WHERE cycle_id=?',(dump(qr),cycle['cycle_id']))
        self.stage(db,cycle,'ASSEMBLING')
        self.job(db,cycle,'LOCATE')
        history(db,self.cycle(db),'QR_BOUND','IR1 정차 후 QR·주문·회차 배정 저장 완료',extra={'pallet_id':pallet})

    def vision_event(self,db,cycle,event):
        data=event['data']
        if event['kind']=='FEED_QR_READ':
            qr=json.loads(cycle['qr'] or '{}')
            if cycle['stage']!='WAIT_FEED_QR' or qr.get('read_id')!=data['read_id'] or qr.get('state')!='READING' or data['attempt']!=qr.get('attempt',0)+1:
                history(db,cycle,'STALE_QR','이전 판독 묶음 또는 시도 회신을 보류했습니다.');return False
            qr.update(data)
            if data['pallet_id'] is None:
                qr['state']='UNREADABLE' if data['attempt']==3 else 'READING'
                db.execute('UPDATE V1_CYCLES SET qr=?,qr_due=? WHERE cycle_id=?',(dump(qr),None if data['attempt']==3 else self.clock()+2,cycle['cycle_id']))
                history(db,cycle,'QR_UNREADABLE','투입 QR 판독 실패 · 벨트 정차 유지')
            else:
                db.execute('SAVEPOINT qr_binding')
                try:self.bind(db,cycle,qr)
                except (sqlite3.Error,Fault):
                    db.execute('ROLLBACK TO qr_binding')
                    qr['state']='SAVE_FAILED'
                    db.execute('UPDATE V1_CYCLES SET qr=?,qr_due=NULL WHERE cycle_id=?',(dump(qr),cycle['cycle_id']))
                    history(db,cycle,'QR_SAVE_FAILED','성공 판독을 보존했습니다. 배정 저장만 재시도하세요.')
                finally:db.execute('RELEASE qr_binding')
            for ticket in db.execute("SELECT * FROM V1_TICKETS WHERE cycle_id=? AND kind='QR' AND read_id=?",(cycle['cycle_id'],qr['read_id'])).fetchall():
                db.execute('UPDATE V1_TICKETS SET state=? WHERE request_id=?',('COMPLETED' if qr['state']=='BOUND' else 'FAILED' if qr['state'] in ('UNREADABLE','SAVE_FAILED') else 'RUNNING',ticket['request_id']))
            return True
        inspection=db.execute('SELECT * FROM V1_INSPECTIONS WHERE cycle_id=? AND inspection_id=?',
                              (cycle['cycle_id'],data.get('inspection_id'))).fetchone()
        if (cycle['stage']!='INSPECTING' or not inspection or inspection['inspection_id']!=cycle['inspection_id']
                or inspection['body'] or (event['kind']=='INSPECTION_CAPTURED' and data['cycle_id']!=cycle['cycle_id'])):
            history(db,cycle,'STALE_INSPECTION','이전 검사 또는 회차의 응답을 보류했습니다.')
            return False
        if db.execute('SELECT state FROM V1_PLANT WHERE id=1').fetchone()[0]!='RUNNING':
            history(db,cycle,'INSPECTION_WHILE_HELD','정지 중 검사 응답을 보류했습니다.',inspection_id=inspection['inspection_id'])
            return False
        order=find_order(db,cycle['order_id'])
        if event['kind']=='INSPECTION_UNAVAILABLE':
            verdict,reasons,destination='INDETERMINATE',['NO_FRAME'],None
            frame=captured=image_ref=None
        else:
            verdict,reasons,destination=contracts.inspect_result(data,json.loads(order['spec']),cycle['pallet_id'],order['target_station'])
            frame,captured,image_ref=data['frame_id'],data['captured_at'],data['image_ref']
        image=self.vision.inspection_image(image_ref) if image_ref else None
        if image_ref:require(image is not None,'동일 검사 프레임의 보관 증거가 없습니다.',422,'CONFLICT','QUERY_FIRST')
        result=dict(order_id=cycle['order_id'],inspection_id=inspection['inspection_id'],cycle_id=cycle['cycle_id'],expected_pallet_id=cycle['pallet_id'],
                    verdict=verdict,reasons=reasons,dispatch_station=destination,captured_at=captured,frame_id=frame,image_ref=image_ref,
                    attempt_number=inspection['attempt_number'],previous_inspection_id=inspection['previous_inspection_id'],
                    rework_command_id=inspection['rework_command_id'])
        db.execute('UPDATE V1_INSPECTIONS SET body=?,vision=?,image=?,archive_id=? WHERE inspection_id=?',(dump(result),dump(data) if event['kind']=='INSPECTION_CAPTURED' else None,image,uid() if image else None,inspection['inspection_id']))
        inspection_id=inspection['inspection_id']
        if verdict=='PASS':
            db.execute("UPDATE V1_ORDERS SET dispatch_station=?,updated_at=? WHERE order_id=?",(destination,now(),cycle['order_id']))
            self.stage(db,cycle,'TO_PICKUP')
            self.queue(db,'LINE',cycle['cycle_id'],'MOVE_TO_PICKUP')
            message='검사 합격 저장 · IR2 이송 요청'
        elif (self.app.config['V1_MODE']=='SIMULATION' and inspection['attempt_number']==1
              and set(reasons)&{'PART_MISSING','WRONG_PART'} and 'QR_MISMATCH' not in reasons):
            # A single simulated rework demonstrates the correlation. Real rework has no approved command contract yet.
            self.stage(db,cycle,'ASSEMBLING')
            self.job(db,cycle,'LOCATE',{'previous_inspection_id':inspection_id})
            message='결함 판정 저장 · 모의 재작업 대기'
        else:
            message='검사 결과 저장 · 벨트 정차 유지 · 현장 확인 필요'
        history(db,self.cycle(db),'INSPECTION_'+verdict,message,inspection_id=inspection_id)
        return True

    def retry_qr(self,actor,cycle_id,data):
        object_fields(data,('request_id','action','previous_read_id'))
        identifier(cycle_id);identifier(data['request_id']);identifier(data['previous_read_id'])
        require(data['action'] in ('READ_QR','SAVE_BINDING'),'QR 재시도 동작 오류')
        with self.app.app_context(),transaction() as db:
            prior=cached(db,'qr:'+actor,data['request_id'],dict(cycle_id=cycle_id,**data))
            if prior:return self.qr_ticket(db,cycle_id,data['request_id']),202
            cycle=self.cycle(db)
            require(cycle and cycle['cycle_id']==cycle_id,'활성 회차가 아닙니다.',409,'STALE_CYCLE')
            require(cycle['stage']=='WAIT_FEED_QR','현재 단계에서 QR 재시도를 할 수 없습니다.',409,'BAD_TRANSITION')
            qr=json.loads(cycle['qr'])
            require(qr['read_id']==data['previous_read_id'],'현재 판독 묶음이 아닙니다.',409,'CONFLICT')
            if data['action']=='READ_QR':
                require(qr['state']=='UNREADABLE','저장 실패는 SAVE_BINDING을 사용하세요.',409,'BAD_TRANSITION')
                qr=dict(read_id=uid(),attempt=0,state='READING')
                db.execute('UPDATE V1_CYCLES SET qr=?,qr_due=? WHERE cycle_id=?',(dump(qr),self.clock(),cycle_id))
                state='ACCEPTED'
            else:
                require(qr['state']=='SAVE_FAILED' and qr.get('pallet_id'),'저장할 성공 QR이 없습니다.',409,'BAD_TRANSITION')
                self.bind(db,cycle,qr);state='COMPLETED'
            db.execute("INSERT INTO V1_TICKETS VALUES(?,'QR',?,?,?)",(data['request_id'],state,cycle_id,qr['read_id']))
            result=self.qr_ticket(db,cycle_id,data['request_id'])
            remember(db,'qr:'+actor,data['request_id'],dict(cycle_id=cycle_id,**data),result,202)
            return result,202

    def qr_ticket(self,db,cycle_id,request_id):
        row=db.execute("SELECT * FROM V1_TICKETS WHERE request_id=? AND kind='QR' AND cycle_id=?",(identifier(request_id),identifier(cycle_id))).fetchone()
        require(row is not None,'QR 재시도 요청이 없습니다.',404,'NOT_FOUND')
        return dict(request_id=request_id,cycle_id=cycle_id,state=row['state'],read_id=row['read_id'])

    def internal_event(self,cycle,kind,data):
        with self.app.app_context(),transaction() as db:
            source=db.execute("SELECT * FROM V1_DEVICES WHERE device_id='VISION'").fetchone()
            # The engine is a single producer; pending events are included in allocation.
            seq=db.execute("SELECT COALESCE(MAX(event_seq),0)+1 FROM V1_EVENTS WHERE device_id='VISION' AND boot_id=?",(source['boot_id'],)).fetchone()[0]
        return dict(event_id=uid(),device_id='VISION',boot_id=source['boot_id'],event_seq=seq,cycle_id=cycle,command_id=None,occurred_at=now(),kind=kind,data=data)

    def tick(self):
        with self.lock,self.app.app_context():
            self.acquire()
            self.refresh_devices()
            with transaction() as db:
                self.start_next(db)
                plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
                cycle=self.cycle(db)
                if cycle and plant['state']=='RUNNING' and cycle['stage']=='WAIT_FEED_QR' and cycle['qr_due'] is not None and cycle['qr_due']<=self.clock():
                    if not db.execute("SELECT 1 FROM V1_VISION_JOBS WHERE cycle_id=? AND action='QR' AND state='PENDING'",(cycle['cycle_id'],)).fetchone():self.job(db,cycle,'QR',json.loads(cycle['qr']))
                commands=db.execute("SELECT * FROM V1_COMMANDS WHERE state IN('ACCEPTED','RUNNING','UNKNOWN') ORDER BY rowid").fetchall()
            for row in commands:
                control=row['action'] in ('PAUSE','RESUME')
                if not control and plant['state']!='RUNNING' and not row['attempted']:continue
                client=self.clients.get(row['device_id'])
                if client is None:continue
                try:
                    if not row['attempted']:
                        with transaction() as db:
                            self.assert_owner(db)
                            current_state=db.execute('SELECT state FROM V1_PLANT').fetchone()[0]
                            if not control and current_state!='RUNNING':continue
                            changed=db.execute("UPDATE V1_COMMANDS SET attempted=1,updated_at=? WHERE command_id=? AND attempted=0",(now(),row['command_id'])).rowcount
                        if not changed:continue
                        response=client.submit({k:row[k] for k in ('command_id','device_id','cycle_id','action')}|{'params':json.loads(row['params'])},row['expected_boot_id'])
                    else:response=client.command(row['command_id'])
                    require(response['command_id']==row['command_id'] and response['device_id']==row['device_id'] and response['cycle_id']==row['cycle_id'],'장비 명령 조회 결과가 다릅니다.',409,'CONFLICT')
                    state=response['state']
                    require(state in ('ACCEPTED','RUNNING','COMPLETED','FAILED','STOPPED','UNKNOWN'),'명령 상태 코드 오류')
                    # Completion evidence must enter through recordDeviceEvent, never HTTP 202/COMPLETED alone.
                    if state in ('FAILED','STOPPED','UNKNOWN'):
                        with transaction() as db:db.execute('UPDATE V1_COMMANDS SET state=?,error=?,updated_at=? WHERE command_id=?',(state,dump(response.get('error')),now(),row['command_id']))
                        self.hold('장비 동작 결과 확인 또는 현장 대조가 필요합니다.',row['device_id'],row['command_id'])
                    elif state!='COMPLETED':
                        with transaction() as db:db.execute('UPDATE V1_COMMANDS SET state=?,updated_at=? WHERE command_id=? AND state!=\'COMPLETED\'',(state,now(),row['command_id']))
                except Exception as error:
                    if not row['attempted'] and isinstance(error,Fault) and error.status==422:
                        with transaction() as db:
                            self.assert_owner(db)
                            db.execute("UPDATE V1_COMMANDS SET state='FAILED',error=?,updated_at=? WHERE command_id=?",
                                       (dump(error.body),now(),row['command_id']))
                            self._hold(db,f"{row['action']} 거절 · {error}",row['device_id'],row['command_id'])
                        continue
                    with transaction() as db:db.execute("UPDATE V1_COMMANDS SET state='UNKNOWN',updated_at=? WHERE command_id=? AND state!='COMPLETED'",(now(),row['command_id']))
                    self.hold('명령 응답 유실 · 조회 우선 · 자동 재전송하지 않습니다.',row['device_id'],row['command_id'])
            self.run_vision_jobs()
            placements.process(self)

    def refresh_devices(self):
        for device,client in self.clients.items():
            if device not in self.required_devices:continue
            try:
                status=contracts.device_status(client.status(),device)
                with transaction() as db:
                    self.assert_owner(db)
                    old=db.execute('SELECT * FROM V1_DEVICES WHERE device_id=?',(device,)).fetchone()
                    if old['boot_id']!=status['boot_id']:
                        active=db.execute("SELECT * FROM V1_COMMANDS WHERE device_id=? AND state IN('ACCEPTED','RUNNING','UNKNOWN') AND attempted=1",(device,)).fetchall()
                        if active or self.cycle(db):self._hold(db,'장비 재부팅 · 이전 명령 결과와 실물 확인이 필요합니다.',device)
                        db.execute('UPDATE V1_DEVICES SET last_seq=0 WHERE device_id=?',(device,))
                    db.execute('UPDATE V1_DEVICES SET boot_id=?,status=?,last_seen=? WHERE device_id=?',(status['boot_id'],dump(status),self.clock(),device))
                    if self.cycle(db) and (not status['online'] or status['state'] in ('FAULT','UNKNOWN') or status['mode']!='AUTO'):
                        self._hold(db,'장비 준비·모드·상태를 확인해야 합니다.',device)
                    seq=db.execute('SELECT last_seq FROM V1_DEVICES WHERE device_id=?',(device,)).fetchone()[0]
                for event in client.events(status['boot_id'],seq):self.record(event)
            except Exception:
                with transaction() as db:
                    self.assert_owner(db)
                    row=db.execute('SELECT * FROM V1_DEVICES WHERE device_id=?',(device,)).fetchone()
                    status=json.loads(row['status']);status.update(online=False,state='UNKNOWN',observed_at=now())
                    db.execute('UPDATE V1_DEVICES SET status=? WHERE device_id=?',(dump(status),device))
                    if self.cycle(db):self._hold(db,'장비 통신 단절 · 상태 확인 전 후속 명령 보류',device)

    def run_vision_jobs(self):
        with transaction() as db:
            self.assert_owner(db)
            plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
            if plant['state']!='RUNNING':return
            job=db.execute("SELECT * FROM V1_VISION_JOBS WHERE state='PENDING' ORDER BY rowid LIMIT 1").fetchone()
            if not job:return
            cycle=self.cycle(db)
            if not cycle or cycle['cycle_id']!=job['cycle_id']:
                db.execute("UPDATE V1_VISION_JOBS SET state='CANCELLED' WHERE job_id=?",(job['job_id'],));return
            order=find_order(db,cycle['order_id'])
        try:
            payload=json.loads(job['payload'])
            sample=None
            if self.app.config['V1_MODE']=='HARDWARE':
                camera={'QR':'cam1','LOCATE':'cam2','INSPECT':self.vision.inspection_camera}[job['action']]
                # Pending jobs may predate this process (or the monotonic clock epoch).
                if payload.get('controller')!=self.instance_id:
                    payload.update(controller=self.instance_id,after_frame=time.monotonic())
                    with transaction() as db:
                        db.execute('UPDATE V1_VISION_JOBS SET payload=? WHERE job_id=?',(dump(payload),job['job_id']))
                    return
                sample=self.vision.process_frame(camera,payload['after_frame'])
                if sample is None:
                    if time.monotonic()-payload['after_frame']<2:return
                    sample=(None,None,None)
            if job['action']=='QR':
                data=self.vision.read_feed_qr(cycle['cycle_id'],payload['read_id'],payload['attempt']+1,sample)
                self.record(self.internal_event(cycle['cycle_id'],'FEED_QR_READ',data))
            elif job['action']=='LOCATE':
                poses=self.vision.locate_assembly(cycle['cycle_id'],json.loads(order['spec']),sample)
                contracts.pose_set(poses)
                self.vision.validate_poses(poses)
                with transaction() as db:
                    current=self.cycle(db)
                    require(current and current['cycle_id']==cycle['cycle_id'] and current['stage']=='ASSEMBLING' and poses['spec']==json.loads(find_order(db,cycle['order_id'])['spec']),'활성 회차·주문 사양이 달라졌습니다.',409,'STALE_CYCLE')
                    order_parts=self.vision.part_order
                    require(contracts.valid_order(order_parts) and placements.supports_refresh(db),'승인된 장착 순서와 ARM 좌표 갱신 기능이 필요합니다.',422,'UNSUPPORTED_VERSION')
                    previous=payload.get('previous_inspection_id')
                    if previous:
                        require(self.app.config['V1_MODE']=='SIMULATION' and current['inspection_id']==previous,
                                '승인된 재작업 명령이 필요합니다.',409,'RECONCILE_REQUIRED','OPERATOR')
                    command_id=self.queue(db,'ARM',cycle['cycle_id'],'ASSEMBLE',dict(pose_policy='REFRESH_BETWEEN_PARTS',part_order=order_parts,initial_poses=poses))
                    if previous:
                        history(db,current,'REWORK_REQUESTED','모의 재작업 요청',command_id,inspection_id=previous)
                    db.execute("UPDATE V1_VISION_JOBS SET state='DONE' WHERE job_id=?",(job['job_id'],))
                return
            else:
                data=self.vision.inspect_product(cycle['cycle_id'],payload['inspection_id'],json.loads(order['spec']),cycle['pallet_id'],sample)
                kind='INSPECTION_CAPTURED' if data is not None else 'INSPECTION_UNAVAILABLE'
                data=data or dict(inspection_id=payload['inspection_id'],reason='NO_FRAME')
                try:
                    result=self.record(self.internal_event(cycle['cycle_id'],kind,data))
                finally:
                    if kind=='INSPECTION_CAPTURED':self.vision.release_image(data['image_ref'])
                if result['disposition']=='HELD_FOR_REVIEW':
                    with transaction() as db:
                        current=self.cycle(db)
                        state=db.execute('SELECT state FROM V1_PLANT WHERE id=1').fetchone()[0]
                        if current and current['inspection_id']==payload['inspection_id'] and state in ('PAUSING','PAUSED','RESUMING'):
                            payload['after_frame']=time.monotonic()
                            db.execute('UPDATE V1_VISION_JOBS SET payload=? WHERE job_id=?',(dump(payload),job['job_id']))
                            return
            with transaction() as db:db.execute("UPDATE V1_VISION_JOBS SET state='DONE' WHERE job_id=?",(job['job_id'],))
        except Exception:
            with transaction() as db:db.execute("UPDATE V1_VISION_JOBS SET state='FAILED' WHERE job_id=?",(job['job_id'],))
            self.hold('비전·보정 프로필 또는 촬영 결과를 확인하세요. 임의 좌표로 진행하지 않습니다.','VISION')

    def start(self):
        if self.worker:return
        self.acquire()
        def loop():
            with self.app.app_context():
                while not self.closed.wait(.2):
                    try:self.tick()
                    except Exception:
                        try:self.hold('공정 처리 오류 · 후속 동작 보류')
                        except Exception:pass
        self.worker=threading.Thread(target=loop,name='factory-v1',daemon=True);self.worker.start()

    def close(self):
        self.closed.set()
        if self.worker:self.worker.join(timeout=3)
        self.vision.close()
        if self.acquired:
            try:
                with self.app.app_context(),transaction() as db:db.execute('DELETE FROM V1_CONTROLLER_LEASE WHERE instance_id=?',(self.instance_id,))
            except sqlite3.Error:pass
