"""Durable v1 LINE-to-UNO bridge. Completion requires a matching serial reply."""
import json
import math
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from factory_v1 import contracts
from factory_v1.common import Fault, dump, identifier, now, require, uid

OPS={'DISPENSE':'DISP','MOVE_TO_ASSEMBLY':'ASM','MOVE_TO_PICKUP':'END','PAUSE':'PAUSE','RESUME':'RESUME'}
DONE={'DISPENSE':('DISP_TIMER','PALLET_DISPENSED',{'evidence':'TIMER'}),
      'MOVE_TO_ASSEMBLY':('IR1_STOP','ASSEMBLY_STATION_STOPPED',{'ir1_detected':True,'belt_command':'STOPPED'}),
      'MOVE_TO_PICKUP':('IR2_STOP','PICKUP_STATION_STOPPED',{'ir2_detected':True,'belt_command':'STOPPED'}),
      'PAUSE':('BOTH_PAUSED','DEVICE_PAUSED',contracts.PAUSED['LINE']),
      'RESUME':('RESUMED','DEVICE_RESUMED',{})}


class LineAdapter:
    def __init__(self,config,clock=time.monotonic):
        self.config,self.clock=config,clock
        self.lock=threading.RLock();self.stop=threading.Event();self.serial=None
        self.buffer=bytearray();self.discard=False;self.synced=False;self.fault=False
        self.last_rx=0.;self.last_ping=0.;self.last_query=0.;self.last_controller=clock()
        self.sid=secrets.randbelow(2**32-1)+1;self.boot=uid()
        self.db=sqlite3.connect(config['journal_path'],check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS commands(command_id TEXT PRIMARY KEY,body TEXT NOT NULL,boot TEXT NOT NULL,sid INTEGER NOT NULL,cyc INTEGER NOT NULL,seq INTEGER NOT NULL UNIQUE,state TEXT NOT NULL,attempted INTEGER NOT NULL DEFAULT 0,result_event_id TEXT,error TEXT,updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS cycles(cycle_id TEXT PRIMARY KEY,cyc INTEGER NOT NULL UNIQUE);
          CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,boot TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,UNIQUE(boot,seq));
          CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,kind TEXT NOT NULL,detail TEXT NOT NULL,recorded_at TEXT NOT NULL);''')
        with self.transaction() as db:
            rows=db.execute("SELECT * FROM commands WHERE state IN('ACCEPTED','RUNNING','UNKNOWN')").fetchall()
            if rows:self.fault=True
            for row in rows:self._failed(db,row,'Pi restarted; inspect the physical state before recovery.',unknown=True)
        self.paused=False

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield self.db;self.db.commit()
            except BaseException:self.db.rollback();raise

    def configured(self):
        c=self.config
        keys=('heartbeat_interval_seconds','serial_response_timeout_seconds','controller_timeout_seconds')
        return c.get('hardware_profile_approved') is True and all(type(c.get(k)) in (int,float) and math.isfinite(c[k]) and c[k]>0 for k in keys) and c['heartbeat_interval_seconds']<c['serial_response_timeout_seconds']

    def touch(self):
        with self.lock:self.last_controller=self.clock()

    @staticmethod
    def result(row):
        body=json.loads(row['body'])
        return dict(command_id=row['command_id'],device_id='LINE',cycle_id=body['cycle_id'],state=row['state'],updated_at=row['updated_at'],result_event_id=row['result_event_id'],error=json.loads(row['error']) if row['error'] else None)

    def _event(self,db,row,kind,data):
        body=json.loads(row['body']);seq=db.execute('SELECT coalesce(max(seq),0)+1 FROM events WHERE boot=?',(self.boot,)).fetchone()[0]
        event=dict(event_id=uid(),device_id='LINE',boot_id=self.boot,event_seq=seq,cycle_id=body['cycle_id'],command_id=row['command_id'],occurred_at=now(),kind=kind,data=data)
        contracts.event(event)
        db.execute('INSERT INTO events(event_id,boot,seq,body) VALUES(?,?,?,?)',(event['event_id'],self.boot,seq,dump(event)))
        return event['event_id']

    def _failed(self,db,row,message,unknown=False):
        error=dict(code='RECONCILE_REQUIRED' if unknown else 'DEVICE_UNAVAILABLE',message=message[:400],retry='OPERATOR',trace_id=uid())
        event=self._event(db,row,'DEVICE_FAILED',error)
        db.execute('UPDATE commands SET state=?,error=?,result_event_id=?,updated_at=? WHERE command_id=?',('UNKNOWN' if unknown else 'FAILED',dump(error),event,now(),row['command_id']))

    def uncertain(self,message):
        with self.transaction() as db:
            self.fault=True;self.synced=False
            rows=db.execute("SELECT * FROM commands WHERE state IN('ACCEPTED','RUNNING')").fetchall()
            for row in rows:self._failed(db,row,message,unknown=True)
            db.execute('INSERT INTO audit VALUES(?,?,?,?)',(uid(),'LINK_UNCERTAIN',message[:400],now()))

    def submit(self,data,boot):
        data=json.loads(dump(data));contracts.command(data)
        for key in ('command_id','cycle_id'):
            if data[key] is not None:data[key]=identifier(data[key])
        if data['action']=='RESUME':data['params']['pause_command_id']=identifier(data['params']['pause_command_id'])
        require(data['device_id']=='LINE','명령 수신 장비가 다릅니다.',403,'FORBIDDEN')
        require(data['action'] in OPS,'LINE에서 지원하지 않는 동작입니다.',422,'UNSUPPORTED_VERSION')
        self.touch()
        with self.transaction() as db:
            require(boot==self.boot,'부팅 세대가 다릅니다. 상태를 다시 조회하세요.',409,'CONFLICT','QUERY_FIRST')
            old=db.execute('SELECT * FROM commands WHERE command_id=?',(data['command_id'],)).fetchone()
            if old:
                require(old['body']==dump(data),'같은 명령 ID의 내용이 다릅니다.',409,'CONFLICT')
                return self.result(old)
            require(self.configured() and self.synced and not self.fault,'실측 프로필·UNO v1 협상·현장 복구가 필요합니다.',503,'DEVICE_UNAVAILABLE','OPERATOR')
            active=db.execute("SELECT * FROM commands WHERE state IN('ACCEPTED','RUNNING') ORDER BY seq").fetchall()
            motions=[r for r in active if json.loads(r['body'])['action'] not in ('PAUSE','RESUME')]
            controls=[r for r in active if json.loads(r['body'])['action'] in ('PAUSE','RESUME')]
            action=data['action']
            require(not controls,'정지·해제 처리 중입니다.',409,'CONFLICT')
            current=motions[0] if motions else None
            if current:require(json.loads(current['body'])['cycle_id']==data['cycle_id'],'활성 회차가 다릅니다.',409,'STALE_CYCLE')
            if action=='RESUME':
                prior=db.execute('SELECT * FROM commands WHERE command_id=?',(data['params']['pause_command_id'],)).fetchone()
                require(self.paused and prior and prior['state']=='COMPLETED' and json.loads(prior['body'])['action']=='PAUSE' and json.loads(prior['body'])['cycle_id']==data['cycle_id'],'정지 명령·회차를 대조하세요.',409,'BAD_TRANSITION')
            elif action!='PAUSE':
                require(not current and not self.paused,'장비가 작업 또는 정지 중입니다.',409,'BAD_TRANSITION')
                last=db.execute("SELECT body,state FROM commands WHERE json_extract(body,'$.action') IN('DISPENSE','MOVE_TO_ASSEMBLY','MOVE_TO_PICKUP') ORDER BY seq DESC LIMIT 1").fetchone()
                prior=json.loads(last['body']) if last else None
                if action=='DISPENSE':require(not prior or last['state']=='COMPLETED' and prior['action']=='MOVE_TO_PICKUP' and prior['cycle_id']!=data['cycle_id'],'이전 LINE 회차가 끝나지 않았습니다.',409,'BAD_TRANSITION')
                else:require(prior and last['state']=='COMPLETED' and prior['cycle_id']==data['cycle_id'] and prior['action']==('DISPENSE' if action=='MOVE_TO_ASSEMBLY' else 'MOVE_TO_ASSEMBLY'),'LINE 공정 순서가 다릅니다.',409,'BAD_TRANSITION')
            cyc=0
            if data['cycle_id'] is not None:
                found=db.execute('SELECT cyc FROM cycles WHERE cycle_id=?',(data['cycle_id'],)).fetchone()
                if not found:
                    cyc=db.execute('SELECT coalesce(max(cyc),0)+1 FROM cycles').fetchone()[0];db.execute('INSERT INTO cycles VALUES(?,?)',(data['cycle_id'],cyc))
                else:cyc=found[0]
            seq=db.execute('SELECT coalesce(max(seq),0)+1 FROM commands').fetchone()[0]
            require(seq<2**32 and cyc<2**32,'시리얼 순번 범위가 찼습니다.',503,'RECONCILE_REQUIRED','OPERATOR')
            db.execute("INSERT INTO commands(command_id,body,boot,sid,cyc,seq,state,updated_at) VALUES(?,?,?,?,?,?,'ACCEPTED',?)",(data['command_id'],dump(data),self.boot,self.sid,cyc,seq,now()))
            return self.result(db.execute('SELECT * FROM commands WHERE command_id=?',(data['command_id'],)).fetchone())

    def status(self):
        with self.lock:
            active=self.db.execute("SELECT * FROM commands WHERE state IN('ACCEPTED','RUNNING') ORDER BY CASE WHEN json_extract(body,'$.action') IN('PAUSE','RESUME') THEN 1 ELSE 0 END,seq LIMIT 1").fetchone()
            last=self.db.execute("SELECT command_id FROM commands WHERE state='COMPLETED' ORDER BY seq DESC LIMIT 1").fetchone()
            online=bool(self.serial is not None and self.synced and self.clock()-self.last_rx<self.config.get('serial_response_timeout_seconds',0))
            return dict(device_id='LINE',boot_id=self.boot,mode='AUTO',state='FAULT' if self.fault else 'UNKNOWN' if not online or not self.configured() else 'PAUSED' if self.paused else 'BUSY' if active else 'IDLE',cycle_id=json.loads(active['body'])['cycle_id'] if active else None,active_command_id=active['command_id'] if active else None,last_completed_command_id=last[0] if last else None,observed_at=now(),online=online)

    def command(self,command_id):
        command_id=identifier(command_id)
        with self.lock:
            row=self.db.execute('SELECT * FROM commands WHERE command_id=?',(command_id,)).fetchone()
            require(row is not None,'명령이 없습니다.',404,'NOT_FOUND');return self.result(row)

    def events(self,boot,after):
        with self.lock:
            require(boot==self.boot or self.db.execute('SELECT 1 FROM events WHERE boot=?',(boot,)).fetchone(),'부팅 세대가 없습니다.',409,'CONFLICT')
            return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM events WHERE boot=? AND seq>? ORDER BY seq',(boot,after))]

    def write(self,line):
        raw=line.encode('ascii');require(len(raw)<=63,'시리얼 전문 길이 오류')
        if self.serial is None:raise OSError('UNO disconnected')
        written=self.serial.write(raw+b'\n')
        if written!=len(raw)+1:raise OSError('Partial serial write; query before recovery')

    def receive(self,line):
        with self.lock:
            if line=='READY V1':
                if self.synced:
                    self.uncertain('UNO rebooted or renegotiated; do not repeat physical motion.')
                    self.boot=uid();self.sid=secrets.randbelow(2**32-1)+1
                self.write(f'V1 SYNC {self.sid}');return
            if line==f'V1 OK {self.sid}':self.synced=True;self.last_rx=self.clock();return
            if line==f'V1 ALIVE {self.sid}':self.last_rx=self.clock();return
            if line.startswith('ERR '):self.uncertain('UNO protocol fault: '+line[4:]);return
            # Unrecognized/legacy text is never mapped into a completion event.

    def serial_reply(self,line):
        parts=line.split()
        if len(parts)!=6 or parts[0]!='R':return self.receive(line)
        try:sid,cyc,seq=map(int,parts[1:4])
        except ValueError:return
        with self.transaction() as db:
            if sid!=self.sid:return
            row=db.execute('SELECT * FROM commands WHERE seq=? AND sid=? AND cyc=? AND boot=?',(seq,sid,cyc,self.boot)).fetchone()
            if row is None or row['state'] not in ('ACCEPTED','RUNNING'):return
            self.last_rx=self.clock();state,detail=parts[4:6];action=json.loads(row['body'])['action']
            if state in ('ACK','RUN','HOLD'):
                db.execute("UPDATE commands SET state='RUNNING',updated_at=? WHERE command_id=?",(now(),row['command_id']));return
            if state=='DONE':
                wanted,kind,data=DONE[action]
                if detail!=wanted:
                    self.fault=True;self._failed(db,row,'Completion evidence did not match the command.',unknown=True);return
                event=self._event(db,row,kind,data)
                db.execute("UPDATE commands SET state='COMPLETED',result_event_id=?,updated_at=? WHERE command_id=?",(event,now(),row['command_id']))
                if action in ('PAUSE','RESUME'):self.paused=action=='PAUSE'
            elif state=='FAIL':
                self.fault=True;self._failed(db,row,'UNO: '+detail,unknown=detail=='UNKNOWN_SEQ')

    def consume(self,raw):
        for byte in raw:
            if byte==10:
                if not self.discard:
                    try:
                        body=self.buffer[:-1] if self.buffer.endswith(b'\r') else self.buffer
                        if len(body)<=63:self.serial_reply(body.decode('ascii'))
                    except UnicodeDecodeError:pass
                self.buffer.clear();self.discard=False
            elif len(self.buffer)<64 and not self.discard:self.buffer.append(byte)
            else:self.discard=True

    def pump(self):
        """One I/O iteration; C is written once, subsequent recovery uses Q only."""
        self.consume(self.serial.read(256))
        with self.lock:
            current=self.clock()
            if not self.synced:return
            if current-self.last_rx>self.config['serial_response_timeout_seconds']:
                raise OSError('UNO heartbeat/reply timeout')
            if current-self.last_controller>self.config['controller_timeout_seconds']:
                if not self.fault:self.uncertain('Main controller lease expired; UNO watchdog must stop outputs.')
                return
            if self.fault:return  # Stop heartbeat so firmware watchdog stops outputs.
            if current-self.last_ping>=self.config['heartbeat_interval_seconds']:
                self.write(f'H {self.sid}');self.last_ping=current
            pending=self.db.execute("SELECT * FROM commands WHERE state='ACCEPTED' AND attempted=0 ORDER BY seq LIMIT 1").fetchone()
            if pending:
                # Commit intent before serial write. A failure cannot requeue C.
                with self.transaction() as db:db.execute('UPDATE commands SET attempted=1 WHERE command_id=?',(pending['command_id'],))
                self.write(f"C {self.sid} {pending['cyc']} {pending['seq']} {OPS[json.loads(pending['body'])['action']]}")
            if current-self.last_query>=self.config['heartbeat_interval_seconds']*2:
                for row in self.db.execute("SELECT * FROM commands WHERE state IN('ACCEPTED','RUNNING') AND attempted=1").fetchall():self.write(f"Q {self.sid} {row['cyc']} {row['seq']}")
                self.last_query=current

    def run_serial(self):
        import serial
        while not self.stop.wait(.02):
            try:
                if not self.configured():self.stop.wait(1);continue
                if self.fault and self.serial is None:self.stop.wait(1);continue
                if self.serial is None:
                    self.serial=serial.Serial(self.config['serial_port'],115200,timeout=0,write_timeout=1,exclusive=True)
                    self.buffer.clear();self.discard=False;self.synced=False
                    self.write(f'V1 SYNC {self.sid}');opened=self.clock()
                self.pump()
                if not self.synced and self.clock()-opened>self.config['serial_response_timeout_seconds']:raise OSError('UNO v1 negotiation failed')
            except Exception:
                self.uncertain('Serial disconnected or negotiation failed; inspect outputs before recovery.')
                if self.serial:
                    try:self.serial.close()
                    except OSError:pass
                self.serial=None;self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.serial:
            try:self.serial.close()
            except OSError:pass
