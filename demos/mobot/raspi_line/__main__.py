"""Run: python -m raspi_line --config /absolute/path/config.json."""
import argparse
import json
import logging
import os
import secrets
import signal
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from flask import Flask,request
from werkzeug.serving import make_server,WSGIRequestHandler
from factory_v1.common import Fault,require
from factory_v1.devices import create_adapter_api
from .adapter import LineAdapter

LOG=logging.getLogger('mobot.line')


class ProcessLock:
    """Take ownership before opening the journal or the serial port."""
    def __init__(self,path):
        self.file=open(str(path)+'.lock','a+b')
        if os.name=='nt':
            import msvcrt
            if self.file.tell()==0:self.file.write(b'0');self.file.flush()
            self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)

    def close(self):self.file.close()


def validate_config(config):
    require(isinstance(config.get('serial_port'),str) and bool(config['serial_port']),'실제 UNO 시리얼 포트를 설정하세요.')
    require(type(config.get('listen_port',8101)) is int and 1<=config.get('listen_port',8101)<=65535,'Pi 포트 번호를 확인하세요.')
    require(config.get('baudrate',115200)==115200,'시리얼 속도는 v1의 115200입니다.')
    require(isinstance(config.get('controller_token'),str) and len(config['controller_token'])>=32,'32자 이상의 제어 인증키를 설정하세요.')
    require(isinstance(config.get('device_token'),str) and len(config['device_token'])>=32,'32자 이상의 이벤트 인증키를 설정하세요.')
    require(config['controller_token']!=config['device_token'],'제어 키와 이벤트 키를 분리하세요.')
    url=urllib.parse.urlsplit(config.get('server_url',''))
    require(not url.username and not url.password and not url.query and not url.fragment and url.path in ('','/'),'서버 기본 주소를 확인하세요.')
    require(url.scheme=='https' or url.scheme=='http' and url.hostname=='127.0.0.1','서버는 내부 HTTPS 또는 SSH 터널의 로컬 HTTP 주소여야 합니다.')
    require(url.hostname and 'ngrok' not in url.hostname.lower(),'공개 ngrok은 v1 장비 연결에 사용할 수 없습니다.')
    require(config.get('listen_host','127.0.0.1') in ('127.0.0.1','::1') or config.get('tls_certificate') and config.get('tls_private_key'),'Pi를 원격 주소에 바인딩하려면 TLS 인증서와 키를 설정하세요.')


def create_app(adapter,config):
    app=Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=8192,V1_ADAPTER_DEVICE='LINE',V1_CONTROLLER_TOKEN=config['controller_token'],V1_INTERNAL_NETWORKS=config['internal_networks'])
    app.extensions['factory_v1']=SimpleNamespace(clients={'LINE':adapter})
    app.register_blueprint(create_adapter_api())

    @app.before_request
    def owner_seen():
        # Only a successfully authenticated, internal controller renews the lease.
        from factory_v1.api import internal_client
        token=request.headers.get('Authorization','')
        if request.path.startswith('/device/v1/') and internal_client() and secrets.compare_digest(token,'Bearer '+config['controller_token']):adapter.touch()

    @app.errorhandler(Fault)
    def fault(error):return error.body,error.status

    @app.errorhandler(400)
    @app.errorhandler(413)
    def bad_request(error):return Fault(400,'BAD_REQUEST','잘못된 요청 본문입니다.').body,400

    @app.errorhandler(500)
    def unavailable(error):return Fault(503,'DEVICE_UNAVAILABLE','장비 처리 결과를 조회하고 현장을 확인하세요.','QUERY_FIRST').body,503

    @app.after_request
    def headers(response):response.headers['Cache-Control']='no-store';return response
    return app


def deliver_once(adapter,config):
    """Same durable event ID on every retry. Old-boot evidence remains queryable."""
    with adapter.lock:
        rows=adapter.db.execute('SELECT body FROM events WHERE delivered=0 AND boot=? ORDER BY seq LIMIT 50',(adapter.boot,)).fetchall()
    for row in rows:
        event=json.loads(row[0])
        req=urllib.request.Request(config['server_url'].rstrip('/')+'/api/v1/device-events',data=row[0].encode(),headers={'Authorization':'Bearer '+config['device_token'],'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=2) as response:
                ack=json.load(response)
                if response.status!=200 or ack.get('event_id')!=event['event_id']:return
            with adapter.transaction() as db:db.execute('UPDATE events SET delivered=1 WHERE event_id=?',(event['event_id'],))
        except (OSError,ValueError,urllib.error.URLError):return


class QuietRequests(WSGIRequestHandler):
    def log_request(self,*args,**kwargs):pass  # Do not log identifiers or bearer credentials.


def main():
    parser=argparse.ArgumentParser(description='MOBOT live UNO LINE v1 adapter')
    parser.add_argument('--config',default='raspi_line/config.json')
    parser.add_argument('--init-config',action='store_true')
    args=parser.parse_args();path=Path(args.config).resolve()
    if args.init_config:
        require(not path.exists(),'기존 설정 파일을 덮어쓰지 않습니다.',409,'CONFLICT')
        config=json.loads(Path(__file__).with_name('config.example.json').read_text(encoding='utf-8-sig'))
        config.update(controller_token=secrets.token_urlsafe(32),device_token=secrets.token_urlsafe(32))
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8');path.chmod(0o600)
        print('설정 파일을 생성했습니다. 주소·포트·실측 프로필을 설정하세요.');return
    config=json.loads(path.read_text(encoding='utf-8-sig'));validate_config(config)
    journal=Path(config['journal_path']);config['journal_path']=str(journal if journal.is_absolute() else path.parent/journal)
    Path(config['journal_path']).parent.mkdir(parents=True,exist_ok=True)
    ownership=ProcessLock(config['journal_path'])
    adapter=LineAdapter(config)
    if not adapter.configured():LOG.warning('운전 차단: 실측·승인 프로필과 통신 감시 시간을 설정하세요.')
    serial_thread=threading.Thread(target=adapter.run_serial,name='uno-v1',daemon=True);serial_thread.start()
    def events():
        while not adapter.stop.wait(.25):
            try:deliver_once(adapter,config)
            except Exception:LOG.error('이벤트 전송 보류: 저장된 이벤트는 삭제하지 않았습니다.')
    threading.Thread(target=events,name='line-events',daemon=True).start()
    app=create_app(adapter,config)
    # Direct TLS for a private Pi, or loopback HTTP behind an encrypted SSH/TLS tunnel.
    tls=(config['tls_certificate'],config['tls_private_key']) if config.get('tls_certificate') and config.get('tls_private_key') else None
    server=make_server(config.get('listen_host','127.0.0.1'),config.get('listen_port',8101),app,threaded=True,ssl_context=tls,request_handler=QuietRequests)
    def close(*_):adapter.close();threading.Thread(target=server.shutdown,daemon=True).start()
    for name in ('SIGINT','SIGTERM'):signal.signal(getattr(signal,name),close)
    try:server.serve_forever()
    finally:adapter.close();serial_thread.join(timeout=2);server.server_close();ownership.close()


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    main()
