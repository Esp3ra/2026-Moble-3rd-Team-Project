"""Run on the Pi. Outbound HTTPS only; hardware is controlled locally."""
import argparse
import json
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4
from contextlib import closing

from pallet_system_uno_sim.app import camera_worker, monitor_worker, serial_reconnect_worker
from pallet_system_uno_sim.controller import Bench, MOTION


class Client:
    def __init__(self, config):
        self.url=config['server_url'].rstrip('/')
        if not self.url.startswith('https://') and not (config.get('allow_local_http') and self.url.startswith('http://127.0.0.1:')):
            raise ValueError('서버 주소는 HTTPS여야 합니다.')
        self.device=config['device_id']
        self.token=config['device_token']

    def post(self, action, data, boot, raw=False):
        headers={'Authorization':'Bearer '+self.token,'ngrok-skip-browser-warning':'1',
                 'X-Device-Boot':boot,'Content-Type':'image/jpeg' if raw else 'application/json'}
        request=urllib.request.Request(self.url+'/api/devices/'+self.device+'/'+action,
                                      data=data if raw else json.dumps(data).encode(),headers=headers,method='POST')
        with urllib.request.urlopen(request,timeout=2) as response:
            body=response.read()
            return json.loads(body) if body else None


class Journal:
    def __init__(self, path):
        self.path=path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,result TEXT NOT NULL)')

    def claim(self, command_id):
        # Commit BEFORE touching hardware. A crash must never replay a motion.
        with closing(sqlite3.connect(self.path)) as db, db:
            try:
                db.execute('INSERT INTO commands VALUES(?,?)',(command_id,json.dumps({'command_id':command_id,'ok':False,'error':'처리 도중 프로세스 종료 · 재실행 없음'})))
                return True
            except sqlite3.IntegrityError:return False

    def finish(self, result):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE commands SET result=? WHERE id=?',(json.dumps(result),result['command_id']))

    def result(self, command_id):
        with closing(sqlite3.connect(self.path)) as db:
            return json.loads(db.execute('SELECT result FROM commands WHERE id=?',(command_id,)).fetchone()[0])


class Agent:
    def __init__(self, config, root):
        self.config=config
        self.root=root
        root.mkdir(parents=True,exist_ok=True)
        self.client=Client(config)
        self.bench=Bench(root/'runs',demo=config.get('demo') is True)
        self.journal=Journal(root/'commands.sqlite3')
        self.boot=str(uuid4())
        self.run_id=None
        self.last_contact=0.
        self.results=[]
        self.closed=threading.Event()
        self.lock=threading.RLock()

    def execute(self, command, server_time, elapsed):
        command_id=command['command_id']
        if not self.journal.claim(command_id):return self.journal.result(command_id)
        result={'command_id':command_id,'ok':False,'error':''}
        try:
            if command['expires_at']-server_time-elapsed<=0:raise ValueError('유효 시간이 지난 명령 · 실행하지 않음')
            action=command['action']
            run=command.get('run_id')
            payload=command.get('payload',{})
            if action=='start':
                self.bench.start(run,payload.get('position_ready'))
                self.run_id=run
            elif action=='stop':self.bench.abort('중앙 서버 정지',manual=True)
            elif action=='timings':self.bench.update_timings(payload.get('values'))
            elif action=='confirm':
                if run!=self.run_id:raise ValueError('배출 확인 회차 불일치')
                self.bench.confirm_ejection(run)
            else:raise ValueError('허용되지 않은 장비 명령')
            result['ok']=True
        except Exception as error:
            result['error']=str(error)[:500]
        self.journal.finish(result)
        return result

    def watchdog(self):
        while not self.closed.wait(.1):
            self.check_link()

    def check_link(self):
        with self.lock:
            if time.monotonic()-self.last_contact>3.5 and self.bench.state in MOTION|{'AWAIT_CONFIRM'}:
                self.bench.abort('중앙 서버 통신 단절 · 로컬 정지 · 자동 재운전 없음')

    def upload_frames(self):
        while not self.closed.wait(.5):
            with self.bench.lock:image=self.bench.jpeg
            if image:
                try:self.client.post('frame',image,self.boot,raw=True)
                except Exception:pass  # Command heartbeat never waits for video upload.

    def run(self):
        port='DEMO' if self.bench.demo else self.config['serial_port']
        self.bench.connect(port)
        for target,args in [(camera_worker,(self.bench,self.config.get('camera_index',0),5,10)),
                            (monitor_worker,(self.bench,)),(serial_reconnect_worker,(self.bench,)),
                            (self.watchdog,()),(self.upload_frames,())]:
            threading.Thread(target=target,args=args,daemon=True).start()
        sequence=0
        try:
            while not self.closed.is_set():
                sequence+=1
                status=self.bench.snapshot()
                status['events']=status['events'][-5:]
                # Keep compact reports. QR raw content is already in trial.pallet_id.
                status.pop('qr_current',None)
                status['trial']['qr_result']=None
                body=dict(boot_id=self.boot,sequence=sequence,run_id=self.run_id,status=status,results=self.results)
                started=time.monotonic()
                try:
                    reply=self.client.post('poll',body,self.boot)
                    elapsed=time.monotonic()-started
                    with self.lock:
                        self.last_contact=time.monotonic()
                        self.results=[]
                        command=reply.get('command')
                        permitted=reply.get('allow_run') is True and reply.get('run_id')==self.run_id
                        if permitted and self.run_id:self.bench.heartbeat(self.run_id)
                        elif self.bench.state in MOTION|{'AWAIT_CONFIRM'}:
                            self.bench.abort('중앙 운전 허가 종료 · 자동 재운전 없음')
                        if command:
                            if command['action']=='start' and (reply.get('allow_run') is not True or reply.get('run_id')!=command.get('run_id')):
                                raise ValueError('시작 명령과 중앙 운전 허가 불일치')
                            self.results.append(self.execute(command,reply['server_time'],elapsed))
                except Exception as error:
                    # No hardware commands are retried. The next poll reports state.
                    print(type(error).__name__+': '+str(error)[:300],flush=True)
                self.closed.wait(.4)
        finally:
            self.closed.set()
            self.bench.shutdown()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default=str(Path(__file__).with_name('config.json')))
    args=parser.parse_args()
    path=Path(args.config).resolve()
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    root=path.parent/'runtime'
    root.mkdir(exist_ok=True)
    # Linux kernel releases this lock even on SIGKILL; never open USB twice.
    import fcntl
    with (root/'agent.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        agent=Agent(config,root)
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:agent.closed.set())
        agent.run()


if __name__=='__main__':main()
