"""Server-side CAM2 checkpoints for one active ARM assembly command."""
import json
import time
from . import contracts
from .common import Fault, dump, history, require, transaction


def supports_refresh(db):
    row = db.execute("SELECT status FROM V1_DEVICES WHERE device_id='ARM'").fetchone()
    return row and contracts.REFRESH_CAPABILITY in json.loads(row['status']).get('capabilities', [])


def accepted_indices(db, command_id):
    return {r[0] for r in db.execute(
        "SELECT part_index FROM V1_ARM_CHECKPOINTS WHERE command_id=? AND state='ACCEPTED'",
        (command_id,))}


def awaiting_ack(db, event):
    if event['kind'] not in ('ARM_PLACEMENT_REQUESTED','ARM_STANDBY_AFTER_ASSEMBLY'):
        return False
    index = len(event['data'].get('completed_parts', [])) if event['kind']=='ARM_PLACEMENT_REQUESTED' else 3
    return db.execute("""SELECT 1 FROM V1_ARM_CHECKPOINTS WHERE command_id=? AND state='READY'
                         AND part_index<?""", (event['command_id'],index)).fetchone() is not None


def request_checkpoint(db, cycle, command, event):
    order = json.loads(command['params']).get('part_order')
    done = event['data']['completed_parts']
    part = event['data']['next_part']
    index = len(done)
    existing = db.execute(
        'SELECT MAX(part_index) FROM V1_ARM_CHECKPOINTS WHERE command_id=?',
        (command['command_id'],)).fetchone()[0]
    required_indices = set(range(1,index))
    if index and db.execute("SELECT 1 FROM V1_ARM_CHECKPOINTS WHERE command_id=? AND part_index=0",
                            (command['command_id'],)).fetchone():
        required_indices.add(0)
    if (not contracts.valid_order(order) or order[:index] != done or order[index] != part
            or existing is not None and existing > index
            or not required_indices <= accepted_indices(db, command['command_id'])):
        return False
    # A fresh request for the same unfinished part supersedes its previous key.
    db.execute("""UPDATE V1_ARM_CHECKPOINTS SET state='SUPERSEDED'
                  WHERE command_id=? AND part_index=?""", (command['command_id'],index))
    db.execute("""INSERT INTO V1_ARM_CHECKPOINTS(checkpoint_id,command_id,part_index,part,after_frame)
                  VALUES(?,?,?,?,?)""", (event['event_id'],command['command_id'],index,part,time.monotonic()))
    db.execute("UPDATE V1_COMMANDS SET state='RUNNING' WHERE command_id=?", (command['command_id'],))
    history(db, cycle, 'ARM_PLACEMENT_REQUESTED', f'{part} 장착 전 CAM2 새 영상 대기', command['command_id'])
    return True


def process(engine):
    with transaction() as db:
        engine.assert_owner(db)
        row = db.execute("""SELECT p.*, c.cycle_id,c.expected_boot_id,o.spec FROM V1_ARM_CHECKPOINTS p
            JOIN V1_COMMANDS c USING(command_id) JOIN V1_CYCLES y USING(cycle_id)
            JOIN V1_ORDERS o ON o.order_id=y.order_id JOIN V1_DEVICES d ON d.device_id='ARM'
            JOIN V1_PLANT plant ON plant.cycle_id=y.cycle_id
            WHERE p.state IN ('WAIT_FRAME','READY') AND c.state='RUNNING' AND y.closed=0
            AND y.stage='ASSEMBLING' AND plant.state='RUNNING' AND d.boot_id=c.expected_boot_id
            ORDER BY p.rowid LIMIT 1""").fetchone()
        if not row:return
        if not supports_refresh(db):
            engine._hold(db,'ARM 좌표 갱신 기능 확인 필요','ARM',row['command_id'])
            return
    key = row['checkpoint_id']
    body = json.loads(row['body']) if row['body'] else None
    if body is None:
        sample = engine.vision.process_frame('cam2',row['after_frame'])
        if sample is None:
            if not row['note']:
                with transaction() as db:
                    history(db,engine.cycle(db),'CAM2_WAIT','장착 요청 이후 촬영된 CAM2 프레임 대기',row['command_id'])
                    db.execute("UPDATE V1_ARM_CHECKPOINTS SET note='WAIT_FRAME' WHERE checkpoint_id=?",(key,))
            return
        try:
            spec = json.loads(row['spec'])
            poses = engine.vision.locate_assembly(row['cycle_id'],spec,sample)
            contracts.pose_set(poses)
            engine.vision.validate_poses(poses)
            place = poses['points'][row['part'].lower()+'_place']
            require(poses['spec']==spec and place['captured_at']==sample[2], '다른 회차 사양 또는 촬영 시각',422,'STALE_CALIBRATION')
            body = dict(cycle_id=row['cycle_id'],part=row['part'],camera_id='cam2',frame_id=sample[1],place=place)
        except (Fault, ValueError, RuntimeError) as error:
            with transaction() as db:
                if not row['note']:
                    history(db,engine.cycle(db),'CAM2_WAIT',str(error)[:300],row['command_id'])
                db.execute("UPDATE V1_ARM_CHECKPOINTS SET note=?,after_frame=? WHERE checkpoint_id=?",
                           (str(error)[:300],time.monotonic(),key))
            return
    with transaction() as db:
        engine.assert_owner(db)
        if db.execute("SELECT state FROM V1_PLANT").fetchone()[0]!='RUNNING':return
        db.execute("UPDATE V1_ARM_CHECKPOINTS SET body=?,state='READY' WHERE checkpoint_id=?",(dump(body),key))
    try:
        engine.clients['ARM'].placement(row['command_id'],key,body,row['expected_boot_id'])
    except (OSError, TimeoutError):
        return  # Retry the identical persisted request, including after a lost 204.
    except Fault as error:
        if error.status>=500:return
        with transaction() as db:
            db.execute("UPDATE V1_ARM_CHECKPOINTS SET state='REJECTED',note=? WHERE checkpoint_id=?",(str(error),key))
            history(db,engine.cycle(db),'CAM2_REJECTED',str(error),row['command_id'])
            if error.status not in (409,422):
                engine._hold(db,'장착 좌표 전달 오류','ARM',row['command_id'])
        return
    with transaction() as db:
        engine.assert_owner(db)
        db.execute("UPDATE V1_ARM_CHECKPOINTS SET state='ACCEPTED' WHERE checkpoint_id=?",(key,))
        history(db,engine.cycle(db),'ARM_PLACEMENT_ACCEPTED',f"{row['part']} 장착 좌표 접수 확인",row['command_id'])
