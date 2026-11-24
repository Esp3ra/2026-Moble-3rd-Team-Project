"""Prepare internal HTTP bench mode; keep existing keys, orders and device journal."""
import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from scripts.prepare_ngrok_test import build_bundle,ROOT
from wireless_pi.__main__ import validate_server_url


def detect_lan_ip():
    explicit=os.environ.get('MOBOT_LAN_IP')
    if explicit:return explicit
    candidates={ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback}
    if len(candidates)!=1:raise ValueError('내부 IP가 여러 개이거나 없습니다. --server-url http://메인PC_IP:8000 또는 MOBOT_LAN_IP를 지정하세요.')
    return candidates.pop()


def prepare(database,server_url=None,target=None):
    database=Path(database).resolve()
    url=server_url or f'http://{detect_lan_ip()}:8000'
    validate_server_url(dict(server_url=url,transport='LAN_HTTP_TEST'))
    if not database.is_file():raise ValueError('먼저 운송 시험 DB를 준비하세요.')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('SELECT count(*) FROM V1_CYCLES WHERE closed=0').fetchone()[0]:raise ValueError('진행 중인 공정을 종료한 뒤 통신 모드를 변경하세요.')
    folder=database.parent;pc_path=folder/'wireless-test.json';private_path=folder/'lan-test-pi-config.json'
    bench=folder/'wireless-test.sqlite3'
    if bench.exists():
        with closing(sqlite3.connect(bench.as_uri()+'?mode=ro',uri=True)) as db:
            active=db.execute("SELECT count(*) FROM commands WHERE (state='PENDING' AND expires>?) OR (state='SENT' AND created > ? - CASE WHEN action='RUN_CYCLE' THEN 150 WHEN action IN ('ACTUATOR_FORWARD','ACTUATOR_REVERSE') THEN 18 ELSE 8 END)",(time.time(),time.time())).fetchone()[0]
            if active:raise ValueError('단독 시험 명령이 진행 중입니다. 정지·결과를 확인한 뒤 전환하세요.')
    originals=[private_path,folder/'wireless-test-pi-config.json']
    key_path=next((p for p in originals if p.exists()),None)
    if pc_path.exists():
        if key_path is None:raise ValueError('기존 Pi 인증키 파일이 없어 모드를 변경하지 않았습니다. 키를 임의 재발급하지 않습니다.')
        pc=json.loads(pc_path.read_text(encoding='utf-8-sig'));private=json.loads(key_path.read_text(encoding='utf-8-sig'))
        if hashlib.sha256(private['device_token'].encode()).hexdigest()!=pc['token_hash']:raise ValueError('기존 인증키 불일치 · 설정 변경 안 함')
    else:
        if key_path is not None:raise ValueError('Pi 키만 남아 있습니다. PC 설정을 먼저 복구하세요.')
        private=dict(device_token=secrets.token_urlsafe(32),serial_port='AUTO')
        pc=dict(token_hash=hashlib.sha256(private['device_token'].encode()).hexdigest())
    private.update(server_url=url.rstrip('/'),transport='LAN_HTTP_TEST');pc.update(enabled=True,transport='LAN_HTTP_TEST')
    # Keep the original ngrok private configuration for a later explicit switch.
    private_path.write_text(json.dumps(private,indent=2)+'\n',encoding='utf-8')
    pc_path.write_text(json.dumps(pc,indent=2)+'\n',encoding='utf-8')
    extras=[(ROOT/'테스트 서버/Server/test_raspi_client.py','test_lan_connection.py'),(ROOT/'LAN_TEST.md','LAN_TEST.md')]
    archive=build_bundle(private_path,target or ROOT/'releases/mobot-lan-test.zip',extras)
    print(f'LAN test ready: {url} · shared key unchanged/not printed · {archive}')
    return private_path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,default=ROOT/'instance/transport-test/mobot.sqlite3');parser.add_argument('--server-url');args=parser.parse_args()
    try:prepare(args.db,args.server_url)
    except (ValueError,OSError) as error:parser.error(str(error))


if __name__=='__main__':main()
