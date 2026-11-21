"""USB/camera-free Pi -> Flask -> WPF test; Python standard library only."""
import argparse
import json
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


MESSAGES = ('READY UNO_SIM_V1','IR:CLEAR','IR2:CLEAR',
            'ACTUATOR:PUSH_10000MS','RETURN_AND_BELT:STARTED',
            'SIM:STATION_STOPPED','SIM:BELT_RESTARTED',
            'BELT_STOPPED:IR2_DELAY_DONE','CYCLE_DONE:READY_A')


def make_report(config, boot, sequence, events, results):
    return dict(boot_id=boot,sequence=sequence,run_id=None,results=results,status=dict(
        mode='DEMO' if config.get('demo') is True else 'LIVE',state='IDLE',
        connected=False,ready=False,protocol_ok=False,clear_view=False,camera_ok=False,
        ir_clear=False,ir2_clear=False,trial={},events=list(events),error='',
        diagnostic='NETWORK_ONLY_SIMULATED_MESSAGES',
        timings={'values':{'ir1_delay_ms':5500,'robot_wait_ms':3000,'ir2_delay_ms':2000},'status':'waiting'}))


def send(config, body):
    url=config['server_url'].rstrip('/')
    if not url.startswith('https://'):
        raise ValueError('server_url은 HTTPS 주소여야 합니다.')
    request=urllib.request.Request(url+'/api/devices/'+config['device_id']+'/poll',
        data=json.dumps(body).encode(),method='POST',headers={
            'Authorization':'Bearer '+config['device_token'],
            'Content-Type':'application/json','ngrok-skip-browser-warning':'1',
            'X-Device-Boot':body['boot_id']})
    with urllib.request.urlopen(request,timeout=3) as response:
        return json.load(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--count',type=int,default=0,help='0: until Ctrl+C')
    args=parser.parse_args()
    path=Path(args.config).resolve()
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    runtime=path.parent/'runtime'
    runtime.mkdir(exist_ok=True)
    # Use the same lock as the physical agent: never impersonate a running device.
    import fcntl
    with (runtime/'agent.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        boot=str(uuid4())
        events=deque(maxlen=5)
        results=[]
        sequence=0
        print('통신 테스트: USB·카메라·모터 미사용. 시험 메시지만 생성합니다. Ctrl+C로 종료.',flush=True)
        while args.count==0 or sequence<args.count:
            text=MESSAGES[sequence%len(MESSAGES)]
            sequence+=1
            events.append(dict(at=datetime.now(timezone.utc).isoformat(),event='UNO TEST',text=text))
            print(f'[TX {sequence:04d}] {text}',flush=True)
            try:
                reply=send(config,make_report(config,boot,sequence,events,results))
                results=[]
                print('[SERVER OK] 장비 인증·메시지 전달 성공',flush=True)
                command=reply.get('command')
                if command:
                    results=[dict(command_id=command['command_id'],ok=False,
                                  error='무선 통신 진단 · 제어 명령 실행하지 않음')]
            except Exception as error:
                print('[SERVER ERROR] '+str(error)[:300],flush=True)
            time.sleep(1)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\n통신 테스트 종료')
