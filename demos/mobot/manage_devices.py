"""Local provisioning utility. Never prints a device token to the terminal."""
import argparse
import json
import os
import re
import secrets
from contextlib import closing
from pathlib import Path

from auth import token_hash
from orders import connect, initialize_orders

ROOT=Path(__file__).resolve().parent


def provision(database, device, hardware_mode, url, output, activate=False):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,50}',device):raise ValueError('장비 ID 형식 오류')
    if not url.startswith('https://'):raise ValueError('HTTPS ngrok 주소가 필요합니다.')
    initialize_orders(database)
    token=secrets.token_urlsafe(40)
    with closing(connect(database)) as db, db:
        if db.execute('SELECT 1 FROM EQUIPMENT_RUNS WHERE finished_at IS NULL').fetchone():
            raise ValueError('진행 중인 회차를 종료한 뒤 등록하세요.')
        db.execute("INSERT INTO REMOTE_DEVICES(device_id,token_hash,hardware_mode) VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET token_hash=excluded.token_hash,hardware_mode=excluded.hardware_mode,boot_id=NULL,last_seen=0,sequence=-1,status_json='{}',frame_jpeg=NULL",(device,token_hash(token),hardware_mode))
        db.execute("UPDATE REMOTE_COMMANDS SET state='CANCELLED' WHERE device_id=? AND state IN ('PENDING','SENT')",(device,))
    output.parent.mkdir(parents=True,exist_ok=True)
    config=dict(server_url=url.rstrip('/'),device_id=device,device_token=token,
                demo=hardware_mode=='DEMO',serial_port='/dev/ttyACM0',camera_index=0)
    output.write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
    try:output.chmod(0o600)
    except OSError:pass
    if activate:
        settings=database.parent/'remote-equipment.json'
        settings.write_text(json.dumps({'mode':'REMOTE','device_id':device},indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--device',default='uno-pi')
    parser.add_argument('--hardware-mode',choices=['DEMO','LIVE'],default='LIVE')
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'instance'/'raspi-config.json')
    parser.add_argument('--activate',action='store_true')
    args=parser.parse_args()
    provision(Path(os.environ.get('MOBOT_DB',ROOT/'instance'/'mobot.sqlite3')),args.device,args.hardware_mode,args.url,args.output,args.activate)
    print('장비 등록 완료. 토큰은 설정 파일에만 저장했습니다:',args.output)
