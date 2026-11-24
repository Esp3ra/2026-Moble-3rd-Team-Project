"""Run the integrated PC LAN bench server, not a separate accepting dummy."""
import argparse
import os
import runpy
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    from scripts.prepare_transport_test import prepare as prepare_db
    from scripts.prepare_lan_test import prepare,detect_lan_ip
    parser=argparse.ArgumentParser(description="통합 Flask 내부망 HTTP 시험 서버")
    parser.add_argument('--ip',help="Pi가 접근할 메인 PC 사설 IP")
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('포트 범위 오류')
    destination=ROOT/'instance/transport-test/mobot.sqlite3'
    prepare_db(ROOT/'instance/mobot.sqlite3',destination)
    ip=args.ip or detect_lan_ip()
    prepare(destination,f'http://{ip}:{args.port}')
    os.environ.update(MOBOT_V1_MODE='TRANSPORT_TEST',MOBOT_DB=str(destination),MOBOT_HOST='0.0.0.0',MOBOT_PORT=str(args.port))
    print(f'Pi 연결 주소: http://{ip}:{args.port} · WPF/QR ROI/UNO 시험 통합',flush=True)
    runpy.run_path(str(ROOT/'app.py'),run_name='__main__')


if __name__=='__main__':main()
