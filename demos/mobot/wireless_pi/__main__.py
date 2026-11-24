"""Pi outbound HTTPS bench test. Run alone; no production agent on the USB port."""
import argparse
import collections
import json
import os
import queue
import signal
import sqlite3
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import ipaddress
from contextlib import contextmanager,closing
from pathlib import Path
from uuid import uuid4,UUID
from .cycle_progress import progress,MESSAGES

OPS={'PING':('PING',0),'STOP':('STOP',0),'BELT':('BELT',500),'ACTUATOR_FORWARD':('EXTEND',1000),'ACTUATOR_REVERSE':('RETRACT',1000),'RUN_CYCLE':('CYCLE',120000),'QR_CONFIRM':('QR',0)}
FINAL={'COMPLETED','FAILED','STOPPED','UNKNOWN'}


def validate_server_url(config):
    url=config['server_url'].rstrip('/');parsed=urllib.parse.urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('서버 기본 주소만 지정하세요.')
    transport=config.get('transport','NGROK_HTTPS_TEST')
    if transport=='LAN_HTTP_TEST':
        try:address=ipaddress.ip_address(parsed.hostname)
        except ValueError:raise ValueError('내부망 시험에는 메인 PC의 숫자 IPv4/IPv6 주소를 지정하세요.') from None
        internal=any(address in ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8','::1/128') if address.version==ipaddress.ip_network(cidr).version)
        if parsed.scheme!='http' or not internal:raise ValueError('LAN_HTTP_TEST는 사설망/루프백 HTTP 주소만 허용합니다.')
    elif transport=='NGROK_HTTPS_TEST':
        if parsed.scheme!='https':raise ValueError('ngrok 시험에는 HTTPS 주소를 사용하세요.')
    else:raise ValueError('transport 설정은 LAN_HTTP_TEST 또는 NGROK_HTTPS_TEST입니다.')
    if parsed.port is not None and not 1<=parsed.port<=65535:raise ValueError('서버 포트 오류')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise urllib.error.HTTPError(req.full_url,code,'시험 장비 인증키 보호를 위해 리디렉션을 따르지 않습니다.',headers,fp)


class Journal:
    def __init__(self,path):
        self.path=path;self.lock=threading.RLock()
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands(seq INTEGER PRIMARY KEY AUTOINCREMENT,command_id TEXT NOT NULL UNIQUE,body TEXT NOT NULL,state TEXT NOT NULL,message TEXT NOT NULL DEFAULT \'\',ack INTEGER NOT NULL DEFAULT 0)')
            db.execute("UPDATE commands SET state='UNKNOWN',message='Pi 재시작 · 자동 재실행 없음' WHERE state NOT IN('COMPLETED','FAILED','STOPPED','UNKNOWN')")

    @contextmanager
    def tx(self):
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            db.row_factory=sqlite3.Row;yield db

    def claim(self,command):
        body=json.dumps(command,sort_keys=True)
        with self.tx() as db:
            prior=db.execute('SELECT * FROM commands WHERE command_id=?',(command['command_id'],)).fetchone()
            if prior:
                old=json.loads(prior['body'])
                if any(old[k]!=command[k] for k in ('action','duration_ms')):raise ValueError('Command ID conflict')
                return None
            cursor=db.execute("INSERT INTO commands(command_id,body,state) VALUES(?,?,'QUEUED')",(command['command_id'],body))
            return cursor.lastrowid

    def finish(self,command_id,state,message):
        with self.tx() as db:db.execute('UPDATE commands SET state=?,message=? WHERE command_id=? AND state NOT IN(\'COMPLETED\',\'FAILED\',\'STOPPED\',\'UNKNOWN\')',(state,message[:100],command_id))

    def attempted(self,command_id):
        with self.tx() as db:db.execute("UPDATE commands SET state='RUNNING' WHERE command_id=? AND state='QUEUED'",(command_id,))

    def results(self):
        with self.tx() as db:return [dict(r) for r in db.execute("SELECT command_id,state,message FROM commands WHERE ack=0 AND state IN('COMPLETED','FAILED','STOPPED','UNKNOWN') ORDER BY seq LIMIT 5")]

    def acknowledge(self,ids):
        with self.tx() as db:db.executemany('UPDATE commands SET ack=1 WHERE command_id=?',[(x,) for x in ids])


class SerialBench:
    def __init__(self,config,journal,network_only=False):
        self.config,self.journal,self.network_only=config,journal,network_only
        self.lock=threading.RLock();self.closed=threading.Event();self.commands=queue.PriorityQueue()
        self.serial=None;self.protocol='NETWORK_ONLY' if network_only else 'UNKNOWN';self.lines=collections.deque(maxlen=10)
        self.message='Pi 통신만 시험' if network_only else 'USB 연결 대기';self.active=None;self.last_contact=0.;self.counter=0
        self.buffer=bytearray();self.discard=False
        self.cycle=None;self.qr_pending=None;self.stopping_cycle_serial=None;self.cycle_serial=None

    def status(self):
        with self.lock:
            value=dict(serial_connected=self.serial is not None,serial_ready=self.serial is not None and self.protocol in ('LEGACY_BENCH','WIRELESS_BENCH','WIRELESS_BENCH_10S','UNO_SIM_QR_V2'),protocol=self.protocol,message=(self.config.get('test_label','')+' · '+self.message).strip(' ·')[:240],lines=list(self.lines))
            if self.cycle:value['cycle']=dict(self.cycle)
            return value

    def accept(self,command,elapsed=0):
        fields={'command_id','action','duration_ms','valid_for_ms'}
        if not isinstance(command,dict) or set(command) not in (fields,fields|{'parent_command_id'}):raise ValueError('Invalid command fields')
        UUID(command['command_id']);action=command['action']
        if action=='QR_CONFIRM':UUID(command.get('parent_command_id',''))
        elif 'parent_command_id' in command:raise ValueError('Unexpected parent command')
        if action not in OPS or command['duration_ms']!=OPS[action][1] or type(command['valid_for_ms']) is not int or not 1<=command['valid_for_ms']<=30000:raise ValueError('Invalid bench action')
        seq=self.journal.claim(command)
        if seq is None:return
        deadline=time.monotonic()+command['valid_for_ms']/1000-elapsed
        with self.lock:
            self.counter+=1;self.commands.put((0 if action=='STOP' else 1 if action=='QR_CONFIRM' else 2,self.counter,(command,seq,deadline)))

    def write(self,line):
        raw=(line+'\n').encode('ascii')
        if self.serial is None or self.serial.write(raw)!=len(raw):raise OSError('Incomplete USB write')

    def complete(self,state,message):
        if self.active:
            command=self.active[0];self.journal.finish(command['command_id'],state,message)
            if command['action']=='RUN_CYCLE' and state in ('UNKNOWN','FAILED','STOPPED'):
                self.cycle=progress(command['command_id'],state,message)
        if self.qr_pending:
            self.journal.finish(self.qr_pending[0]['command_id'],'UNKNOWN','회차 중단 · QR 확인 결과 미확인');self.qr_pending=None
        self.active=None

    def line(self,text):
        self.lines.append(text[:120])
        if text=='READY UNO_SIM_QR_V2':
            if self.active:self.complete('UNKNOWN','UNO 재시작 · 현장 확인 필요')
            self.protocol='UNO_SIM_QR_V2';self.message='QR 성공 대기형 컨베이어 시뮬레이션 연결';return
        if text=='ALIVE UNO_SIM_QR_V2':self.protocol='UNO_SIM_QR_V2';self.message='QR 성공 대기형 컨베이어 시뮬레이션 연결';return
        if text.startswith('EV '):
            parts=text.split(' ',2)
            if len(parts)!=3 or not parts[1].isdigit() or self.active is None:return
            event_seq=int(parts[1]);action=self.active[0]['action']
            expected=self.stopping_cycle_serial if action=='STOP' else self.active[1]
            if event_seq!=expected:return
            text=parts[2]
            if action=='RUN_CYCLE':
                stage=MESSAGES.get(text)
                if stage:self.cycle=progress(self.active[0]['command_id'],stage,text)
                if text=='QR:RECOGNIZED_ROBOT_WAIT' and self.qr_pending:
                    self.journal.finish(self.qr_pending[0]['command_id'],'COMPLETED','UNO QR 성공 확인 · 모의 조립 대기 시작');self.qr_pending=None
                if text=='CYCLE_DONE:READY_A':self.complete('COMPLETED','컨베이어 회차 완료 · 실제 로봇팔 조립 아님');return
                if text.startswith(('ERR:','BUSY:')):
                    self.stop_outputs();self.complete('FAILED',text);return
        elif self.active and self.active[0]['action']=='RUN_CYCLE':
            # Untagged stale lines must never advance an owned cycle.
            if not text.startswith(('READY','TIMING:')):return
        if text in ('READY MOBOT_WIRELESS_TEST','READY MOBOT_WIRELESS_TEST_10S'):
            if self.active:self.complete('UNKNOWN','UNO 재시작 · 자동 재실행 없음')
            self.protocol='WIRELESS_BENCH_10S' if text.endswith('_10S') else 'WIRELESS_BENCH';self.message='UNO 연결 · 액추에이터 1초 시험';return
        if text in ('ALIVE MOBOT_WIRELESS_TEST','ALIVE MOBOT_WIRELESS_TEST_10S'):self.protocol='WIRELESS_BENCH_10S' if text.endswith('_10S') else 'WIRELESS_BENCH';self.message='UNO 연결 · 액추에이터 1초 시험';return
        if text.startswith('READY UNO_SIM_V1') or text.startswith('TIMING:'):
            if text.startswith('READY') and self.active:self.complete('UNKNOWN','UNO 재시작 · 자동 재실행 없음')
            if self.protocol!='UNO_SIM_QR_V2':self.protocol='LEGACY_BENCH';self.message='기존 UNO · 액추에이터 1초 시험 가능'
        if text=='READY V1':self.protocol='UNKNOWN';self.message='MobotLineV1 감지 · 단독 시험은 MobotWirelessBench 업로드 필요';return
        if not self.active:return
        command,seq,_=self.active
        if self.protocol in ('WIRELESS_BENCH','WIRELESS_BENCH_10S'):
            parts=text.split()
            if len(parts)!=4 or parts[:2]!=['R',str(seq)]:return
            if parts[2]=='DONE':self.complete('COMPLETED','UNO '+parts[3]+' · 센서 위치 확인 아님')
            elif parts[2]=='STOPPED':self.complete('STOPPED','UNO 정지 출력 확인')
            elif parts[2]=='FAIL':self.complete('FAILED','UNO '+parts[3])
        elif self.protocol in ('LEGACY_BENCH','UNO_SIM_QR_V2'):
            action=command['action']
            if action in ('ACTUATOR_FORWARD','ACTUATOR_REVERSE') and text=='JOG:DONE_POSITION_UNVERIFIED':self.complete('COMPLETED','기존 UNO 1초 구동 종료 · 위치 미확인')
            elif action=='STOP' and text.startswith('STOPPED:'):
                if self.cycle:self.cycle=progress(self.cycle['command_id'],'STOPPED',text)
                self.complete('COMPLETED','기존 UNO 정지 출력 확인')
            elif action=='PING' and text.startswith('TIMING:'):self.complete('COMPLETED','UNO 시리얼 응답 확인')
            elif text.startswith(('ERR:','BUSY:')):self.complete('FAILED',text)

    def consume(self,raw):
        for byte in raw:
            if byte==10:
                if not self.discard:
                    try:self.line(self.buffer.rstrip(b'\r').decode('ascii'))
                    except UnicodeDecodeError:pass
                self.buffer.clear();self.discard=False
            elif len(self.buffer)<160 and not self.discard:self.buffer.append(byte)
            else:self.discard=True

    def stop_outputs(self):
        if self.protocol in ('WIRELESS_BENCH','WIRELESS_BENCH_10S'):self.write('T 0 STOP 0')
        elif self.protocol in ('LEGACY_BENCH','UNO_SIM_QR_V2'):self.write('s')

    def tick(self):
        with self.lock:
            if self.serial:self.consume(self.serial.read(512))
            if self.active and time.monotonic()-self.last_contact>1.5:
                self.stop_outputs();self.complete('UNKNOWN','메인 통신 단절 · 정지 요청 · 자동 재실행 없음')
            if self.active and time.monotonic()>self.active[2]:
                self.stop_outputs();self.complete('UNKNOWN','UNO 완료 회신 시간 초과 · 정지 요청')
            if self.qr_pending and time.monotonic()>self.qr_pending[2]:
                self.stop_outputs();self.complete('UNKNOWN','QR 성공 회신 미확인 · 정지 요청')
            try:_,_,entry=self.commands.queue[0]
            except IndexError:return
            if self.active and entry[0]['action'] not in ('STOP','QR_CONFIRM'):return
            _,_,(command,seq,deadline)=self.commands.get_nowait()
            action=command['action']
            if time.monotonic()>deadline:self.journal.finish(command['command_id'],'FAILED','명령 유효시간 초과 · 실행 안 함');return
            if action=='QR_CONFIRM':
                if self.qr_pending or not self.active or self.active[0]['action']!='RUN_CYCLE' or command['parent_command_id']!=self.active[0]['command_id'] or not self.cycle or self.cycle['stage']!='WAIT_QR' or self.protocol!='UNO_SIM_QR_V2':
                    self.journal.finish(command['command_id'],'FAILED','현재 회차의 QR 대기 상태가 아닙니다.');return
                self.journal.attempted(command['command_id']);self.qr_pending=(command,seq,time.monotonic()+2)
                self.write('q '+str(self.active[1]));return
            if action=='PING' and (not self.serial or self.protocol=='UNKNOWN'):
                link='내부망' if self.config.get('transport')=='LAN_HTTP_TEST' else 'ngrok'
                self.journal.finish(command['command_id'],'COMPLETED',(self.config.get('test_label') or 'Pi')+' '+link+' 통신 정상 · UNO 연결은 별도 확인');return
            if not self.serial or self.protocol not in ('LEGACY_BENCH','WIRELESS_BENCH','WIRELESS_BENCH_10S','UNO_SIM_QR_V2'):
                self.journal.finish(command['command_id'],'FAILED','지원되는 UNO 연결 없음 · 출력 확인 불가');return
            if action=='BELT' and self.protocol not in ('WIRELESS_BENCH','WIRELESS_BENCH_10S'):self.journal.finish(command['command_id'],'FAILED','컨베이어 단독 시험 펌웨어 필요');return
            if action=='RUN_CYCLE' and self.protocol!='UNO_SIM_QR_V2':self.journal.finish(command['command_id'],'FAILED','QR 대기형 RobotWorkSimulation 펌웨어 업로드 필요');return
            if action=='STOP':
                self.stopping_cycle_serial=self.active[1] if self.active and self.active[0]['action']=='RUN_CYCLE' else self.cycle_serial
                self.complete('STOPPED','운영자 정지 요청으로 중단')
            self.journal.attempted(command['command_id'])
            self.active=(command,seq,time.monotonic()+command['duration_ms']/1000+2)
            if action=='RUN_CYCLE':
                self.cycle_serial=seq;self.cycle=progress(command['command_id'],'STARTING','작업 시작 전송 · UNO 회신 대기');self.write('a '+str(seq));return
            if self.protocol in ('WIRELESS_BENCH','WIRELESS_BENCH_10S'):self.write(f'T {seq} {OPS[action][0]} {command["duration_ms"]}')
            else:self.write({'PING':'?','STOP':'s','ACTUATOR_FORWARD':'j','ACTUATOR_REVERSE':'k'}[action])

    def run(self):
        if not self.network_only:
            import serial
            from serial.tools import list_ports
        query_at=0.;opened_at=0.
        while not self.closed.wait(.01):
            try:
                if not self.network_only and self.serial is None:
                    port=self.config.get('serial_port','AUTO')
                    if port=='AUTO':
                        ports=[p for p in list_ports.comports() if p.vid is not None or p.device.startswith(('/dev/ttyACM','/dev/ttyUSB'))]
                        if len(ports)!=1:raise OSError('USB 포트가 없거나 여러 개입니다. config.json의 serial_port를 지정하세요.')
                        port=ports[0].device
                    self.serial=serial.Serial(port,115200,timeout=0,write_timeout=.5,exclusive=True)
                    self.protocol='UNKNOWN';self.buffer.clear();self.discard=False;opened_at=time.monotonic();self.message='UNO 펌웨어 식별 중'
                self.tick()
                if self.serial and time.monotonic()-opened_at>2 and time.monotonic()-query_at>1:
                    self.write('?');query_at=time.monotonic()
            except Exception as error:
                with self.lock:
                    self.complete('UNKNOWN','USB 단절 · 자동 재실행 없음')
                    if self.serial:
                        try:self.serial.close()
                        except OSError:pass
                    self.serial=None;self.protocol='UNKNOWN';self.message=str(error)[:240]
                # Still consume network PING/STOP without an attached UNO.
                self.tick();self.closed.wait(1)

    def close(self):
        self.closed.set()
        with self.lock:
            try:
                if self.serial:self.stop_outputs();self.serial.close()
            except OSError:pass
            self.complete('UNKNOWN','시험 프로그램 종료 · 재실행 없음')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='wireless_pi/config.json');parser.add_argument('--network-only',action='store_true');parser.add_argument('--label',default='');parser.add_argument('--seconds',type=float,default=600,help='Diagnostic duration, default 10 minutes');args=parser.parse_args()
    if not __import__('math').isfinite(args.seconds) or not 0<args.seconds<=3600:parser.error('--seconds must be between 0 and 3600')
    path=Path(args.config).resolve();config=json.loads(path.read_text(encoding='utf-8-sig'));url=validate_server_url(config)
    config['test_label']=args.label[:80]
    opener=urllib.request.build_opener(NoRedirect())
    lock=(path.parent/'wireless-test.lock').open('a+b')
    if os.name=='nt':
        import msvcrt
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    journal=Journal(path.parent/'wireless-test-journal.sqlite3');bridge=SerialBench(config,journal,args.network_only)
    for name in ('SIGINT','SIGTERM'):signal.signal(getattr(signal,name),lambda *_:bridge.close())
    worker=threading.Thread(target=bridge.run,daemon=True);worker.start();boot=str(uuid4());sequence=0
    end=time.monotonic()+args.seconds
    try:
        while not bridge.closed.is_set() and time.monotonic()<end:
            sequence+=1;packet=dict(boot_id=boot,sequence=sequence,status=bridge.status(),results=journal.results())
            started=time.monotonic()
            try:
                request=urllib.request.Request(url+'/wireless-test/v1/poll',data=json.dumps(packet,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+config['device_token'],'Content-Type':'application/json','ngrok-skip-browser-warning':'1'})
                with opener.open(request,timeout=2) as response:reply=json.load(response)
                bridge.last_contact=time.monotonic();journal.acknowledge(reply['acknowledged'])
                if reply['command']:bridge.accept(reply['command'],elapsed=time.monotonic()-started)
                print('통신 정상 | '+bridge.status()['message'],flush=True)
            except Exception as error:print(type(error).__name__+': '+str(error)[:180],flush=True)
            bridge.closed.wait(.2)
    finally:bridge.close();worker.join(timeout=2);lock.close();print('무선 시험 종료',flush=True)


if __name__=='__main__':main()
