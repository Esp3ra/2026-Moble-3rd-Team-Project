"""Bounded real loopback HTTP test; synthetic key, isolated DB, no USB or motors."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import test_factory_v1 as fixtures
from app import create_app
from factory_v1.common import uid
from werkzeug.serving import make_server,WSGIRequestHandler


def main():
    case=fixtures.FactoryTests('test_stock_shared_and_public_privacy');case.setUp();case.engine.close()
    server=None;worker=None
    try:
        folder=case.path.parent;token='synthetic-loopback-test-only'
        (folder/'wireless-test.json').write_text(json.dumps(dict(enabled=True,transport='LAN_HTTP_TEST',token_hash=hashlib.sha256(token.encode()).hexdigest())))
        with patch.dict(os.environ,{'MOBOT_DB':str(case.path),'MOBOT_V1_MODE':'SIMULATION','MOBOT_V1_BACKGROUND':'0'}):app=create_app()
        case.addCleanup(app.extensions['factory_v1'].close)
        class Quiet(WSGIRequestHandler):
            def log_request(self,*args,**kwargs):pass
        server=make_server('127.0.0.1',0,app,threaded=True,request_handler=Quiet)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        config=folder/'pi-config.json'
        config.write_text(json.dumps(dict(server_url=f'http://127.0.0.1:{server.server_port}',transport='LAN_HTTP_TEST',device_token=token,serial_port='AUTO')))
        bridge=app.extensions['wireless_bench'];command_id=uid();failures=[]
        def queue_ping():
            try:
                until=time.monotonic()+4
                while time.monotonic()<until:
                    if bridge.snapshot()['online']:bridge.queue(command_id,'PING');return
                    time.sleep(.02)
                raise AssertionError('Pi did not come online')
            except Exception as error:failures.append(str(error))
        sender=threading.Thread(target=queue_ping,daemon=True);sender.start()
        result=subprocess.run([sys.executable,'-B','-m','wireless_pi','--config',str(config),'--network-only','--seconds','3','--label','LOOPBACK_TEST_NOT_REAL_PI'],cwd=ROOT,capture_output=True,text=True,timeout=12)
        sender.join(timeout=1)
        assert result.returncode==0,result.stderr
        assert not failures,failures
        snapshot=bridge.snapshot();command=next(c for c in snapshot['commands'] if c['command_id']==command_id)
        assert command['state']=='COMPLETED',command
        assert not snapshot['serial_connected']
        with bridge.tx() as db:boot=db.execute('SELECT boot FROM device').fetchone()[0]
        diagnostic=subprocess.run([sys.executable,'-B',str(ROOT/'테스트 서버/Server/test_raspi_client.py'),'--config',str(config)],cwd=ROOT,capture_output=True,text=True,timeout=12)
        assert diagnostic.returncode==0,diagnostic.stdout+diagnostic.stderr
        with bridge.tx() as db:assert db.execute('SELECT boot FROM device').fetchone()[0]==boot,'Probe changed active boot'
        print('PASS: real loopback HTTP auth, Pi network-only polling, queued PING/result and diagnostic probe. No USB or hardware commands.')
    finally:
        if server:server.shutdown();server.server_close()
        if worker:worker.join(timeout=2)
        case.doCleanups()


if __name__=='__main__':main()
