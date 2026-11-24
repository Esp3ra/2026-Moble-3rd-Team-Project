"""Install approved LINE connectivity and optional PC camera IDs, preserving other settings."""
import argparse
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--pi-config',required=True,help='Private JSON created by python -m raspi_line --init-config')
    p.add_argument('--line-url',required=True,help='Private HTTPS adapter, or http://127.0.0.1:port over an SSH tunnel')
    p.add_argument('--settings',default=str(ROOT/'instance/factory-v1.json'))
    p.add_argument('--cameras',nargs=4,type=int,metavar=('CAM1','CAM2','CAM3','CAM4'))
    args=p.parse_args();url=urlsplit(args.line_url)
    if not (url.scheme=='https' or url.scheme=='http' and url.hostname=='127.0.0.1') or not url.hostname or 'ngrok' in url.hostname.lower() or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        p.error('내부 HTTPS 또는 암호화 터널의 로컬 HTTP 주소를 입력하세요.')
    pi=json.loads(Path(args.pi_config).read_text(encoding='utf-8-sig'))
    if not all(isinstance(pi.get(k),str) and len(pi[k])>=32 for k in ('controller_token','device_token')) or pi['controller_token']==pi['device_token']:p.error('Pi 제어 키·이벤트 키를 각각 설정하세요.')
    path=Path(args.settings);path.parent.mkdir(parents=True,exist_ok=True)
    settings=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if path.exists():
        backup=path.with_name(path.name+'.before-line-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.bak');backup.write_bytes(path.read_bytes())
    settings['mode']='HARDWARE'
    settings.setdefault('devices',{})['LINE']={'base_url':args.line_url.rstrip('/'),'controller_token':pi['controller_token']}
    settings.setdefault('device_tokens',{})['LINE']=pi['device_token']
    if args.cameras:
        if any(n<0 for n in args.cameras) or len(set(args.cameras))!=4:p.error('서로 다른 카메라 번호 4개를 지정하세요.')
        settings['camera_indices']=dict(zip(('cam1','cam2','cam3','cam4'),args.cameras))
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(temp,path)
    print('실물 LINE 연결 설정을 저장했습니다. 서버를 재시작하세요. 인증키는 출력하지 않았습니다.')


if __name__=='__main__':main()
