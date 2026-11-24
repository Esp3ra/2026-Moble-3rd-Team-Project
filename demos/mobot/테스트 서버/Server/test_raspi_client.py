"""Authenticated LAN HTTP diagnostic. Does not open USB or submit process events."""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parent
if not (ROOT/'wireless_pi').is_dir():ROOT=ROOT.parents[1]
sys.path.insert(0,str(ROOT))
from wireless_pi.__main__ import validate_server_url,NoRedirect


def check(base,token,device='LINE'):
    opener=urllib.request.build_opener(NoRedirect())
    for path,payload in [('/ping',None),('/wireless-test/v1/probe',dict(device_id=device,message='Raspi LAN connection check'))]:
        headers={'Content-Type':'application/json'}
        if payload is not None:headers['Authorization']='Bearer '+token
        request=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
        started=time.perf_counter()
        with opener.open(request,timeout=5) as response:
            body=json.load(response)
            if response.status!=200 or body.get('status')!='ok' or body.get('transport')!='LAN_HTTP_TEST':raise ValueError('예상한 통합 LAN 서버 응답이 아닙니다.')
        print(f'OK {path} · {(time.perf_counter()-started)*1000:.1f} ms',flush=True)
    print('내부망 HTTP 연결·인증 정상. USB·모터 동작·정식 공정 검증은 별도입니다.',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',help='메인 PC 내부 HTTP 주소 (기존 인자 유지)')
    parser.add_argument('--device',choices=('LINE',),default='LINE')
    parser.add_argument('--config',type=Path,default=ROOT/'wireless_pi/config.json')
    args=parser.parse_args()
    try:
        config=json.loads(args.config.read_text(encoding='utf-8-sig'))
        base=args.server or os.environ.get('MOBOT_SERVER_URL') or config['server_url']
        base=validate_server_url(dict(server_url=base,transport='LAN_HTTP_TEST'))
        check(base,config['device_token'],args.device)
    except (OSError,ValueError,KeyError,urllib.error.URLError) as error:
        print('연결 실패: '+str(error)[:200],file=sys.stderr)
        print('메인 서버 실행, 실제 PC IP:8000, 방화벽 Private/LocalSubnet, 같은 LAN, ZIP 설정 파일을 확인하세요.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
