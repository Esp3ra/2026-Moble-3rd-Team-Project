"""Common device contract clients and explicitly selected simulation adapters."""
import urllib.request
import urllib.error
import urllib.parse
from .common import *
from . import contracts


class HttpDevice:
    def __init__(self,device,config):self.device,self.config=device,config

    def call(self,path,data=None,boot=None,method=None):
        renew=getattr(self,'renew_lease',None)
        if renew:renew()
        base=self.config.get('base_url','').rstrip('/')
        require(base.startswith('https://') or base.startswith('http://127.0.0.1:'),'장비 HTTPS 주소 또는 로컬 연결 설정이 필요합니다.',503,'DEVICE_UNAVAILABLE')
        headers={'Authorization':'Bearer '+self.config['controller_token'],'Content-Type':'application/json'}
        if boot:headers['X-Expected-Boot-Id']=boot
        req=urllib.request.Request(base+path,data=dump(data).encode() if data is not None else None,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=self.config.get('request_timeout_seconds',2)) as response:
                if method=='PUT':
                    require(response.status==204,'좌표 접수 응답은 204여야 합니다.',409,'CONFLICT')
                    return
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:payload=json.loads(error.read())
            except Exception:payload={}
            raise Fault(error.code if error.code in (400,401,403,404,409,422,503) else 503,payload.get('code','DEVICE_UNAVAILABLE'),payload.get('message','장비 요청 실패'),'QUERY_FIRST')

    def status(self):return self.call('/device/v1/status')
    def placement(self,command_id,key,body,boot):
        self.call(f'/device/v1/commands/{command_id}/placements/{key}',body,boot,'PUT')
    def submit(self,data,boot):return self.call('/device/v1/commands',data,boot)
    def command(self,command_id):return self.call('/device/v1/commands/'+command_id)
    def events(self,boot,after):
        result=[];cursor=None
        for _ in range(20):
            query=f'?boot_id={boot}&after_seq={after}&limit=100'+('&cursor='+urllib.parse.quote(cursor) if cursor else '')
            page=self.call('/device/v1/events'+query)
            result.extend(page['events']);cursor=page['next_cursor']
            if not cursor:return result
        raise Fault(503,'RECONCILE_REQUIRED','이벤트 복원 범위가 큽니다. 대조를 계속해야 합니다.','QUERY_FIRST')


class SimDevice:
    def __init__(self,device,clock=time.time):
        self.device,self.clock=device,clock
        self.boot=uid();self.records={};self.payloads={};self.log=[]
        self.active=None;self.control=None;self.due=0.;self.left=0.;self.paused=False
        self.last=None;self.lock=__import__('threading').RLock()
        self.delay=.5
        self.checkpoint=None
        self.part_index=0
        self.placements={}

    def result(self,command,state,event_id=None):
        return dict(command_id=command['command_id'],cycle_id=command['cycle_id'],device_id=self.device,state=state,updated_at=now(),result_event_id=event_id,error=None)

    def submit(self,data,boot):
        contracts.command(data)
        with self.lock:
            require(boot==self.boot,'장비 부팅 세대가 다릅니다.',409,'CONFLICT','QUERY_FIRST')
            require(data['device_id']==self.device,'명령 수신 장비가 다릅니다.',403,'FORBIDDEN')
            old=self.records.get(data['command_id'])
            if old:
                require(self.payloads[data['command_id']]==dump(data),'같은 명령 ID의 내용이 다릅니다.',409,'CONFLICT')
                return old.copy()
            action=data['action']
            allowed={rule[1] for rule in contracts.EVENT_RULES.values() if rule[0]==self.device}|{'PAUSE','RESUME'}
            require(action in allowed,'장비에서 지원하지 않는 동작입니다.',422,'UNSUPPORTED_VERSION')
            if action in ('PAUSE','RESUME'):
                require(self.control is None,'제어권 변경 요청 처리 중입니다.',409,'CONFLICT')
                if action=='RESUME':
                    prior=self.records.get(data['params']['pause_command_id'])
                    require(self.paused and prior and prior['state']=='COMPLETED','정지 명령 대조 실패',409,'CONFLICT')
                self.control=(data,self.clock()+.15)
            else:
                require(self.active is None and not self.paused,'장비가 사용 중입니다.',409,'CONFLICT')
                self.active=data;self.due=self.clock()+self.delay
                self.part_index=0;self.checkpoint=None
            self.payloads[data['command_id']]=dump(data)
            self.records[data['command_id']]=self.result(data,'ACCEPTED')
            return self.records[data['command_id']].copy()

    def emit(self,command,kind,data):
        event=dict(event_id=uid(),device_id=self.device,boot_id=self.boot,event_seq=len(self.log)+1,cycle_id=command['cycle_id'],command_id=command['command_id'],occurred_at=now(),kind=kind,data=data)
        contracts.event(event)
        self.log.append(event)
        self.records[command['command_id']]=self.result(command,'COMPLETED',event['event_id'])
        self.last=command['command_id']

    def update(self):
        if self.control and self.clock()>=self.control[1]:
            command=self.control[0];self.control=None
            if command['action']=='PAUSE':
                self.left=max(0,self.due-self.clock());self.paused=True
                self.emit(command,'DEVICE_PAUSED',contracts.PAUSED[self.device])
            else:
                self.paused=False;self.due=self.clock()+self.left
                self.emit(command,'DEVICE_RESUMED',{})
        if self.active and not self.paused and self.clock()>=self.due:
            command=self.active
            if command['action']=='ASSEMBLE' and 'part_order' in command['params']:
                if self.checkpoint:return
                order=command['params']['part_order']
                self.part_index+=1
                if self.part_index<len(order):
                    self.checkpoint=uid()
                    self.log.append(dict(event_id=self.checkpoint,device_id='ARM',boot_id=self.boot,event_seq=len(self.log)+1,
                        cycle_id=command['cycle_id'],command_id=command['command_id'],occurred_at=now(),kind='ARM_PLACEMENT_REQUESTED',
                        data=dict(completed_parts=order[:self.part_index],next_part=order[self.part_index],gripper_clear=True)))
                    self.records[command['command_id']]=self.result(command,'RUNNING')
                    return
            self.active=None
            kind=next(kind for kind,rule in contracts.EVENT_RULES.items() if rule[1]==command['action'])
            detail={k:(command['params']['destination'] if k=='destination' else v) for k,v in contracts.EVENT_RULES[kind][2].items()}
            if command['action']=='DISPENSE':detail['evidence']='TIMER'
            self.emit(command,kind,detail)

    def status(self):
        with self.lock:
            self.update()
            return dict(device_id=self.device,boot_id=self.boot,mode='AUTO',state='PAUSED' if self.paused else 'BUSY' if self.active or self.control else 'IDLE',
                cycle_id=self.active['cycle_id'] if self.active else None,active_command_id=self.active['command_id'] if self.active else None,
                last_completed_command_id=self.last,observed_at=now(),online=True,
                **({'capabilities':[contracts.REFRESH_CAPABILITY]} if self.device=='ARM' else {}))

    def placement(self,command_id,key,body,boot):
        with self.lock:
            require(self.device=='ARM' and boot==self.boot,'장비 또는 부팅 불일치',409,'CONFLICT')
            if (command_id,key) in self.placements:
                require(self.placements[command_id,key]==dump(body),'좌표 입력 충돌',409,'CONFLICT')
                return
            require(self.active and self.active['command_id']==command_id and self.checkpoint==key and not self.paused,
                    '현재 ARM 장착 대기점이 아닙니다.',409,'BAD_TRANSITION')
            require(body['cycle_id']==self.active['cycle_id'] and body['part']==self.active['params']['part_order'][self.part_index],
                    '장착 회차 또는 부품 불일치',409,'STALE_CYCLE')
            self.placements[command_id,key]=dump(body)
            self.checkpoint=None;self.due=self.clock()+self.delay

    def command(self,command_id):
        with self.lock:
            self.update();require(command_id in self.records,'명령 결과가 없습니다.',404,'NOT_FOUND')
            return self.records[command_id].copy()

    def events(self,boot,after):
        with self.lock:
            require(boot==self.boot,'부팅 세대가 다릅니다.',409,'CONFLICT')
            self.update();return [r.copy() for r in self.log if r['event_seq']>after]


def create_adapter_api():
    from flask import Blueprint
    from .api import internal_client,body
    api=Blueprint('device_v1',__name__,url_prefix='/device/v1')

    def adapter():
        device=current_app.config['V1_ADAPTER_DEVICE']
        result=current_app.extensions['factory_v1'].clients.get(device)
        require(result is not None,'v1 장비 연결 모듈이 설정되지 않았습니다.',503,'DEVICE_UNAVAILABLE')
        return result

    @api.before_request
    def auth():
        require(internal_client(),'장비 제어는 내부망에서만 가능합니다.',403,'FORBIDDEN')
        token=current_app.config.get('V1_CONTROLLER_TOKEN','')
        require(token and secrets.compare_digest(token_hash(token),token_hash(bearer_token())),'제어 주체 인증 실패',401,'UNAUTHORIZED')

    @api.errorhandler(Fault)
    def fail(error):return error.body,error.status

    @api.post('/commands')
    def accept():
        boot=identifier(request.headers.get('X-Expected-Boot-Id'))
        data=contracts.command(request.get_json(silent=True))
        result=adapter().submit(data,boot)
        return result,202 if result['state'] in ('ACCEPTED','RUNNING') else 200

    @api.get('/commands/<command_id>')
    def command_result(command_id):return adapter().command(identifier(command_id))

    @api.put('/commands/<command_id>/placements/<checkpoint_id>')
    def put_placement(command_id,checkpoint_id):
        boot=identifier(request.headers.get('X-Expected-Boot-Id'))
        data=request.get_json(silent=True)
        contracts.placement(data)
        adapter().placement(identifier(command_id),identifier(checkpoint_id),data,boot)
        return '',204

    @api.get('/status')
    def status():return adapter().status()

    @api.get('/events')
    def events():
        limit,cursor=query_page();boot=identifier(request.args.get('boot_id'))
        try:after=int(request.args.get('after_seq','0'))
        except ValueError:raise Fault(400,'BAD_REQUEST','이벤트 순번 오류')
        require(after>=0,'이벤트 순번 오류')
        rows=adapter().events(boot,after)
        rows,next_cursor=page(rows,'event_id','device-events:'+boot+':'+str(after),limit,cursor)
        return dict(events=rows,next_cursor=next_cursor)
    return api
