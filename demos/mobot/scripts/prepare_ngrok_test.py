"""Create a private outbound-ngrok bench bundle, separate from production devices."""
import argparse
import hashlib
import json
import secrets
import sqlite3
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def build_bundle(pi_config,target=None,extra_files=()):
    target=Path(target) if target else ROOT/'releases/mobot-ngrok-test.zip'
    target.parent.mkdir(parents=True,exist_ok=True)
    draft=target.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(draft,'w',zipfile.ZIP_DEFLATED) as archive:
            for file in ('__init__.py','__main__.py','cycle_progress.py','requirements.txt','README.md'):archive.write(ROOT/'wireless_pi'/file,'wireless_pi/'+file)
            archive.write(pi_config,'wireless_pi/config.json')
            archive.write(ROOT/'firmware/MobotWirelessBench/MobotWirelessBench.ino','firmware/MobotWirelessBench/MobotWirelessBench.ino')
            archive.write(ROOT/'pallet_system_uno_sim/firmware/RobotWorkSimulation/RobotWorkSimulation.ino','firmware/RobotWorkSimulation/RobotWorkSimulation.ino')
            archive.write(ROOT/'CONVEYOR_SIMULATION.md','CONVEYOR_SIMULATION.md')
            for source,name in extra_files:archive.write(source,name)
        draft.replace(target)
    finally:
        draft.unlink(missing_ok=True)
    return target


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default=str(ROOT/'instance/transport-test/mobot.sqlite3'));p.add_argument('--server-url',default='https://YOUR_TUNNEL.ngrok-free.dev');args=p.parse_args()
    db_path=Path(args.db).resolve();url=urlsplit(args.server_url)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:p.error('HTTPS 서버 기본 주소를 지정하세요.')
    if not db_path.is_file():p.error('실행할 시험 서버 DB가 없습니다.')
    with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('SELECT count(*) FROM V1_CYCLES WHERE closed=0').fetchone()[0]:p.error('진행 중인 공정부터 종료하세요.')
    config=db_path.parent/'wireless-test.json';pi_config=db_path.parent/'wireless-test-pi-config.json'
    if config.exists():
        if not pi_config.exists():p.error('기존 시험 키의 Pi 설정 파일이 없습니다. 키를 임의 재발급하지 않습니다.')
        private=json.loads(pi_config.read_text(encoding='utf-8-sig'));pc=json.loads(config.read_text(encoding='utf-8-sig'))
        if hashlib.sha256(private['device_token'].encode()).hexdigest()!=pc['token_hash']:p.error('시험 키가 일치하지 않습니다.')
    else:
        private=dict(server_url=args.server_url.rstrip('/'),device_token=secrets.token_urlsafe(32),serial_port='AUTO')
        pc=dict(enabled=True,token_hash=hashlib.sha256(private['device_token'].encode()).hexdigest())
    private['server_url']=args.server_url.rstrip('/');pc['enabled']=True
    private['transport']=pc['transport']='NGROK_HTTPS_TEST'
    config.write_text(json.dumps(pc,indent=2)+'\n',encoding='utf-8');pi_config.write_text(json.dumps(private,indent=2)+'\n',encoding='utf-8')
    target=build_bundle(pi_config)
    print('ngrok 단독 시험 활성화 설정·Pi ZIP 생성 완료. 시험 인증키는 출력하지 않았습니다.')
    print(target)


if __name__=='__main__':main()
