"""Read-only USB serial -> existing authenticated Flask -> WPF test."""
import argparse
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from raspi_agent.agent import Client


def report(events, connected=True):
    # Diagnostic presence is never permission to operate the conveyor.
    return dict(mode='LIVE',state='IDLE',connected=connected,ready=False,protocol_ok=False,
                clear_view=False,camera_ok=False,ir_clear=False,ir2_clear=False,
                timings={'values':{'ir1_delay_ms':5500,'robot_wait_ms':3000,'ir2_delay_ms':2000},'status':'waiting'},
                trial={},events=list(events),error='',diagnostic='SERIAL_ONLY')


def forward(client, boot, sequence, events, results):
    reply=client.post('poll',dict(boot_id=boot,sequence=sequence,run_id=None,
                                status=report(events),results=results),boot)
    command=reply.get('command')
    # Do not execute even a stop: this utility never writes to the serial port.
    return ([{'command_id':command['command_id'],'ok':False,
              'error':'시리얼 진단 모드에서는 장비 제어를 실행하지 않습니다.'}] if command else [])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--port',help='e.g. /dev/ttyACM0; defaults to config serial_port')
    parser.add_argument('--baud',type=int,default=115200)
    args=parser.parse_args()
    path=Path(args.config).resolve()
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('demo') is True:
        raise ValueError('이 진단은 실제 USB용 LIVE 등록 장비에서 실행하세요.')
    root=path.parent/'runtime'
    root.mkdir(exist_ok=True)
    import fcntl
    import serial
    with (root/'agent.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        client=Client(config)
        boot=str(uuid4())
        events=deque(maxlen=5)
        sequence=0
        results=[]
        next_report=0.
        port=args.port or config['serial_port']
        print(f'USB 수신 전용: {port}, {args.baud} baud. 종료 Ctrl+C.',flush=True)
        # Opening an UNO port may reset the board. No serial.write() is used.
        with serial.Serial(port,args.baud,timeout=.1,exclusive=True) as device:
            while True:
                raw=device.readline(512)
                if raw:
                    text=raw.decode('utf-8',errors='replace').strip()[:200]
                    if text:
                        events.append(dict(at=datetime.now(timezone.utc).isoformat(),event='UNO',text=text))
                        print('[USB] '+text,flush=True)
                if time.monotonic()>=next_report:
                    sequence+=1
                    try:
                        results=forward(client,boot,sequence,events,results)
                        print('[SERVER OK] '+(events[-1]['text'] if events else '아직 USB 메시지 없음'),flush=True)
                    except Exception as error:
                        print('[SERVER ERROR] '+str(error)[:300],flush=True)
                    next_report=time.monotonic()+.5


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\n시리얼 진단 종료')
