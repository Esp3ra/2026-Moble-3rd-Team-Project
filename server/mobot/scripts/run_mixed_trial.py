"""Run the actual app + actual AGV HTTP adapter, with isolated trial storage."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from run_live_agv import load_configuration,check_journal_before_control
from factory_v1.mixed_trial import PROFILE
import json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',type=Path,required=True)
    parser.add_argument('--confirmed-position',type=int,choices=(2,),help='Operator confirmed the physical standby at point 2')
    parser.add_argument('--recover-idle-standby',action='store_true',help='Local operator requests the registered idle recovery procedure; requires --confirmed-position 2')
    parser.add_argument('--reconcile-completed-dispense',help='Preserve a recorded DISPENSE completion and recover into PAUSED; never resume automatically')
    args=parser.parse_args();path=args.settings.resolve()
    if (args.recover_idle_standby or args.reconcile_completed_dispense) and args.confirmed_position!=2:parser.error('Local recovery requires explicit physical point-2 confirmation')
    if args.recover_idle_standby and args.reconcile_completed_dispense:parser.error('Choose idle recovery or active dispense reconciliation')
    settings=json.loads(path.read_text(encoding='utf-8-sig'))
    if settings.get('mixed_trial',{}).get('profile')!=PROFILE:parser.error('Mixed trial profile required')
    if settings['mixed_trial'].get('retired'):parser.error('Archived trial profile; use the new prepared trial directory')
    os.environ.update(MOBOT_DB=str(path.with_name('mobot.sqlite3')),MOBOT_MIXED_TRIAL='1',MOBOT_V1_MODE='HARDWARE',MOBOT_V1_BACKGROUND='1',MOBOT_HOST='127.0.0.1',MOBOT_PORT='8000')
    agv,event,profile=load_configuration(path)
    check_journal_before_control(Path(profile['journal_path']).resolve())
    # Startup must not replace a running listener or silently start a second controller.
    import socket
    for port in (8000,profile['listen_port']):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port))
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'AGV'))
    from agv_client import AGVClient
    from run_live_agv import read_device_key
    probe=AGVClient(profile['device_url'],read_device_key(profile['device_key_file']),request_timeout=profile['device_request_timeout_seconds'])
    raw=probe.status()  # GET only: never close()/stop an unclaimed controller.
    allowed_states=('ready','arrived','stopped','line_lost','control_lost','wifi_lost','timeout','unknown') if args.confirmed_position==2 else ('ready','arrived')
    position_verified=args.confirmed_position==2 or raw['position']==2
    if (raw['state'] not in allowed_states or not position_verified or raw['servo_angle']!=85
            or raw['fork_moving'] or raw['control_connected'] or raw['mode']!='auto'):
        raise ValueError('Verify physical point 2 / fork 85 / auto / idle / no other controller first')
    from live_agv_host import LiveAgvHost
    host=LiveAgvHost(profile,read_device_key(profile['device_key_file']),agv['controller_token'],event)
    try:
        host.start(args.confirmed_position)
        print('REAL HARDWARE MIXED TRIAL: LINE/AGV real, ARM/assembly vision simulated. No operation starts until WPF START.',flush=True)
        from app import main as serve_app,create_app
        def factory():
            app=create_app()
            app.extensions['mixed_trial_recovery'].host=host
            if args.recover_idle_standby or args.reconcile_completed_dispense:
                # This is the same registered procedure as the admin API, invoked
                # by a local operator holding the private device profile. No web auth bypass.
                import time
                from factory_v1.common import uid
                from factory_v1.trial_recovery import PROCEDURE
                app.extensions['mobot_start']()
                recovery=app.extensions['mixed_trial_recovery']
                with app.app_context():
                    deadline=time.monotonic()+8
                    while True:
                        with app.extensions['factory_v1'].lock:
                            app.extensions['factory_v1'].refresh_devices()
                        from orders import connect
                        from contextlib import closing
                        with closing(connect(app.config['ORDER_DB'])) as db:
                            line=json.loads(db.execute("SELECT status FROM V1_DEVICES WHERE device_id='LINE'").fetchone()[0])
                        if line['online'] and line['state']=='IDLE':break
                        if time.monotonic()>deadline:raise RuntimeError('LINE is not idle; operator recovery held')
                        time.sleep(.2)
                    if args.recover_idle_standby:
                        response,_=recovery.submit('LOCAL_OPERATOR',dict(request_id=uid(),device_id='AGV',procedure_id=PROCEDURE,
                            reason='Local operator explicitly requested recovery and confirmed physical point 2, unloaded AGV, stopped conveyor/actuator and empty conveyor. Preserve prior commands and end only uncertain idle control requests.'))
                        recovery.thread.join(timeout=20)
                        if recovery.result(response['command_id'])['state']!='COMPLETED':raise RuntimeError('Local recovery is not verified; inspect the stored manual result')
                        print('IDLE_STANDBY_RECOVERY_COMPLETE: no order completion fabricated; auto operation remains disabled.',flush=True)
                    else:
                        from factory_v1.reconciliation import reconcile
                        from factory_v1.common import transaction,require
                        worker=app.extensions['factory_v1']
                        with worker.lock,transaction() as db:
                            row=db.execute("SELECT c.*,e.body,e.processed,e.disposition FROM V1_COMMANDS c JOIN V1_EVENTS e ON e.event_id=c.result_event_id WHERE c.command_id=?",(args.reconcile_completed_dispense,)).fetchone()
                            plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
                            require(row and row['action']=='DISPENSE' and row['state']=='COMPLETED' and row['processed']==1 and row['disposition']=='RECORDED' and row['cycle_id']==plant['cycle_id'],
                                    'Stored typed dispense completion for the active cycle is required',409,'RECONCILE_REQUIRED')
                            require(json.loads(row['body'])['kind']=='PALLET_DISPENSED','Dispense evidence kind mismatch',409,'CONFLICT')
                        with worker.lock:
                            result,_=reconcile(worker,'LOCAL_OPERATOR',args.reconcile_completed_dispense,dict(request_id=uid(),observation='COMPLETED',
                                reason='Operator requested recovery after AGV control lease expiry before pickup. Preserve the already recorded PALLET_DISPENSED event. No dispense/travel replay; pause all devices before any resume.'))
                        if result['requires_review']:raise RuntimeError('Device observations still require reconciliation')
                        deadline=time.monotonic()+15
                        while True:
                            state=worker.plant()['state']
                            if state=='PAUSED':break
                            if state=='RECOVERY_REQUIRED' or time.monotonic()>deadline:raise RuntimeError('Verified plant pause was not reached')
                            time.sleep(.2)
                        print('ACTIVE_CYCLE_RECOVERED_TO_PAUSED: dispense evidence preserved; no automatic resume or travel.',flush=True)
            return app
        serve_app(factory)
    finally:
        host.close()


if __name__=='__main__':main()
