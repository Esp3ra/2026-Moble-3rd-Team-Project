"""Shared reconciliation procedure for the authenticated API and local operator CLI."""
from .common import *


def reconcile(worker,actor,command_id,data):
    identifier(command_id);text(data['reason'],300)
    if 'evidence_ref' in data:text(data['evidence_ref'],256)
    require(data['observation'] in ('COMPLETED','NOT_EXECUTED','UNRESOLVED'),'관측 코드 오류')
    payload=dict(command_id=command_id,**data);scope='reconcile:'+actor
    with transaction() as db:
        prior=cached(db,scope,data['request_id'],payload)
        if prior:return prior
        row=db.execute('SELECT * FROM V1_COMMANDS WHERE command_id=?',(command_id,)).fetchone()
        require(row is not None,'명령이 없습니다.',404,'NOT_FOUND')
        plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
        require(plant['state']=='RECOVERY_REQUIRED' and row['state'] in ('UNKNOWN','FAILED','STOPPED','COMPLETED','RUNNING'),'현장 대조가 필요한 명령이 아닙니다.',409,'BAD_TRANSITION')
        devices=worker.device_states(db)
        available=len(devices)==len(worker.required_devices) and all(d['online'] and d['state'] in ('IDLE','PAUSED') and d['active_command_id'] is None for d in devices)
        other_unknown=db.execute("SELECT 1 FROM V1_COMMANDS WHERE state IN('UNKNOWN','FAILED') AND command_id!=?",(command_id,)).fetchone()
        resolved=False
        if available:
            if data['observation']=='COMPLETED' and row['state']=='COMPLETED' and row['result_event_id']:
                resolved=True
            elif data['observation']=='NOT_EXECUTED' and row['state'] in ('UNKNOWN','FAILED'):
                db.execute("UPDATE V1_COMMANDS SET state='STOPPED',updated_at=? WHERE command_id=?",(now(),command_id))
                if row['action'] not in ('PAUSE','RESUME'):worker.queue(db,row['device_id'],row['cycle_id'],row['action'],json.loads(row['params']))
                resolved=True
        if resolved and not other_unknown:
            ticket=uid()
            pause_ids={device:worker.queue(db,device,row['cycle_id'],'PAUSE',ticket=ticket) for device in worker.required_devices}
            db.execute("INSERT INTO V1_TICKETS VALUES(?,'PLANT','ACCEPTED',?,NULL)",(ticket,row['cycle_id']))
            db.execute("UPDATE V1_PLANT SET state='PAUSING',pause_ids=?,action_request=?,updated_at=? WHERE id=1",(dump(pause_ids),ticket,now()))
        result=dict(command_id=command_id,resolution=data['observation'],requires_review=not resolved or bool(other_unknown),recorded_at=now())
        db.execute('INSERT INTO V1_AUDIT VALUES(?,?,?,?,?)',(uid(),actor,'RECONCILE',dump(payload),now()))
        return remember(db,scope,data['request_id'],payload,result)
