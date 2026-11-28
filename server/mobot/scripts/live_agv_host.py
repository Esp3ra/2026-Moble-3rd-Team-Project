"""Local lifecycle of the real AGV adapter; common HTTP command API stays intact."""
import threading
import time
import uuid
import json
from datetime import datetime,timezone
from agv_client import AGVClient
from device_adapter import Adapter,serve
from run_live_agv import AdapterReference,check_journal_before_control
from pathlib import Path


class LiveAgvHost:
    def __init__(self,profile,key,controller,event):
        self.profile,self.key,self.event=profile,key,event
        self.reference=AdapterReference()
        self.server=serve(self.reference,controller,(profile['listen_host'],profile['listen_port']))
        self.adapter=None
        self.thread=None
        self.diagnostic_stop=threading.Event()
        self.diagnostic_thread=None

    def _write_diagnostics(self):
        path=Path(self.profile['journal_path']).with_name('agv-transport.jsonl')
        previous_client=None;last_sequence=0
        try:
            with path.open('a',encoding='utf-8') as output:
                while True:
                    client=self.adapter.client if self.adapter else None
                    if client is not previous_client:previous_client=client;last_sequence=0
                    if client and hasattr(client,'transport_diagnostics'):
                        for record in client.transport_diagnostics():
                            if record['sequence']>last_sequence:
                                output.write(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),**record))+'\n')
                                last_sequence=record['sequence']
                        output.flush()
                    if self.diagnostic_stop.wait(.5):break
        except OSError:
            # Diagnostics never block or alter the physical controller.
            pass

    def _new_adapter(self):
        p=self.profile
        client=AGVClient(p['device_url'],self.key,heartbeat_seconds=p['heartbeat_seconds'],request_timeout=p['device_request_timeout_seconds'])
        adapter=Adapter(client,p['journal_path'],p['command_timeout_seconds'])
        self.adapter=self.reference.target=adapter
        return adapter

    def _observe(self,condition,seconds=5):
        deadline=time.monotonic()+seconds
        while True:
            raw=self.adapter.client.status()
            if condition(raw):return raw
            if time.monotonic()>=deadline:raise TimeoutError('Standby condition was not observed')
            time.sleep(.1)

    def start(self,confirmed_position=None):
        check_journal_before_control(Path(self.profile['journal_path']).resolve())
        adapter=self._new_adapter()
        if confirmed_position==2:
            raw=adapter.client.status()
            if raw['control_connected'] or raw['fork_moving'] or raw['state'] in ('moving','reverse_testing','paused'):
                raise ValueError('Operator standby registration requires idle unclaimed hardware')
            adapter.client.connect();adapter.client.set_position(2)
            self._observe(lambda s:s['state']=='ready' and s['position']==2)
        adapter.start(self.profile['events_url'],self.event,self.profile['poll_seconds'])
        self.thread=threading.Thread(target=self.server.serve_forever,name='agv-http',daemon=True)
        self.thread.start()
        self.diagnostic_thread=threading.Thread(target=self._write_diagnostics,name='agv-diagnostics',daemon=True)
        self.diagnostic_thread.start()

    def recover_standby(self,reason):
        """Caller owns engine.lock and has checked no active cycle/pending business work."""
        old=self.adapter
        with old.lock:
            raw=old.client.status()
            if old._pending() or old.lane['cycle_id'] is not None:
                raise ValueError('An active AGV cycle requires business reconciliation')
            if raw['state'] in ('moving','reverse_testing') or raw['fork_moving'] or raw['mode']!='auto' or raw['servo_angle']<0:
                raise ValueError('Confirm stopped, unloaded hardware in automatic mode')
        old.close()
        adapter=self._new_adapter()
        try:
            if not adapter.lane['recovery']:adapter._fault('Operator requested idle standby reconciliation')
            self._observe(lambda s:not s['control_connected'])
            client=adapter.client;client.connect()
            raw=client.status()
            if raw['servo_angle']!=85:
                operation=str(uuid.uuid4());client.set_fork(85,operation_id=operation)
                self._observe(lambda s:s['operation_id']==operation and s['servo_angle']==85 and not s['fork_moving'],12)
            client.set_position(2)  # Explicit operator-confirmed physical position, once only.
            self._observe(lambda s:s['state']=='ready' and s['position']==2 and s['servo_angle']==85)
            adapter.confirm_standby_recovery(reason)
            adapter.start(self.profile['events_url'],self.event,self.profile['poll_seconds'])
        except Exception:
            adapter._fault('Standby recovery incomplete; operator reconciliation required')
            raise

    def close(self):
        if self.thread:
            self.server.shutdown();self.thread.join(timeout=3)
        self.server.server_close()
        if self.adapter:self.adapter.close()
        self.diagnostic_stop.set()
        if self.diagnostic_thread:self.diagnostic_thread.join(timeout=2)
