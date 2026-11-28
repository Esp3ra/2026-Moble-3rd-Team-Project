"""Prepare an isolated hardware trial profile. Never copy orders or sessions."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from configure_live_agv import settings_path
from factory_v1.mixed_trial import PROFILE
from run_live_agv import check_journal_before_control


def prepare(source, target, admin_login):
    source,target=Path(source).resolve(),Path(target).resolve()
    if target==source.parent or target.exists():
        raise ValueError('Use a new trial directory; existing profiles are never overwritten')
    settings=json.loads(source.read_text(encoding='utf-8-sig'))
    profile=json.loads(source.with_name('agv-adapter.json').read_text(encoding='utf-8-sig'))
    check_journal_before_control(Path(profile['journal_path']).resolve())
    if not {'LINE','AGV'} <= settings.get('devices',{}).keys():raise ValueError('Configure real LINE and AGV first')
    production=source.with_name('mobot.sqlite3')
    with closing(sqlite3.connect(production.as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT role_id,login_id,name,password_hash FROM ACCOUNTS WHERE login_id=? AND role_id IN(1,2)',(admin_login,)).fetchone()
    if not row:raise ValueError('The selected existing admin account was not found')
    selected={k:settings[k] for k in ('camera_indices','camera_profiles') if k in settings}
    selected.update(mode='HARDWARE',mixed_trial={'profile':PROFILE},
        devices={k:settings['devices'][k] for k in ('LINE','AGV')},
        device_tokens={k:settings['device_tokens'][k] for k in ('LINE','AGV')},
        camera_indices={**dict.fromkeys(('cam1','cam2','cam3','cam4')),**selected.get('camera_indices',{})})
    # ARM/vision secrets and real inference plugins are deliberately not copied.
    selected['devices']['LINE']={**selected['devices']['LINE'],'controller_keepalive_seconds':.2}
    profile.update(journal_path=str(target/'agv_journal.sqlite3'),events_url='http://127.0.0.1:8000/api/v1/device-events')
    profile['device_key_file']=str(Path(profile['device_key_file']).resolve())
    # Short request limits stay separate from the whole pickup/delivery timeout.
    profile['command_timeout_seconds']=max(float(profile['command_timeout_seconds']),90)
    target.mkdir(parents=True)
    (target/'factory-v1.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
    (target/'agv-adapter.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    if source.with_name('qr-roi.json').exists():shutil.copyfile(source.with_name('qr-roi.json'),target/'qr-roi.json')
    # Initialize only the new DB, with no background device I/O.
    previous={k:os.environ.get(k) for k in ('MOBOT_DB','MOBOT_V1_MODE','MOBOT_V1_BACKGROUND','MOBOT_MIXED_TRIAL')}
    try:
        os.environ.update(MOBOT_DB=str(target/'mobot.sqlite3'),MOBOT_V1_MODE='HARDWARE',MOBOT_V1_BACKGROUND='0',MOBOT_MIXED_TRIAL='1')
        from app import create_app
        app=create_app();app.extensions['factory_v1'].close()
        with closing(sqlite3.connect(target/'mobot.sqlite3')) as db,db:
            db.execute('INSERT INTO ACCOUNTS(role_id,login_id,name,password_hash) VALUES(?,?,?,?)',row)
    finally:
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-settings',type=Path,default=settings_path())
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--admin-login',required=True,help='Copy only this admin login/hash; no sessions or business data')
    args=parser.parse_args()
    try:result=prepare(args.source_settings,args.directory,args.admin_login)
    except Exception as error:parser.exit(1,'Trial preparation failed ('+type(error).__name__+'); existing runtime was not modified.\n')
    print('Mixed trial profile prepared: '+str(result))
    print('Existing admin login is available. Real LINE/AGV credentials were preserved privately; none were printed.')


if __name__=='__main__':main()
