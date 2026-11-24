"""Prepare an isolated transport-test DB; never copy production orders or sessions."""
import argparse
import json
import os
import sqlite3
import sys
from uuid import uuid4
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from orders import initialize_orders,connect
from factory_v1.common import initialize


def prepare(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination:raise ValueError('시험 DB와 운영 DB는 달라야 합니다.')
    if not source.is_file():raise ValueError('계정을 가져올 운영 DB가 없습니다.')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('SELECT count(*) FROM V1_CYCLES WHERE closed=0').fetchone()[0]:raise ValueError('운영 공정 진행 중에는 시험 모드로 전환하지 않습니다.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        with closing(sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT value FROM V1_META WHERE key='database_scope'").fetchone()
            if not row or row[0]!='TRANSPORT_TEST':raise ValueError('기존 파일은 시험 DB가 아닙니다. 덮어쓰지 않습니다.')
            if db.execute('SELECT count(*) FROM V1_CYCLES WHERE closed=0').fetchone()[0]:raise ValueError('이전 시험 회차를 먼저 현장에서 확인하세요. 진행 중 이력을 덮어쓰지 않습니다.')
    else:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
            accounts=db.execute('SELECT account_id,role_id,login_id,password_hash,name,created_at FROM ACCOUNTS').fetchall()
            roles=db.execute('SELECT role_id,role_code,role_name,description FROM ROLES').fetchall()
            pallets=db.execute('SELECT pallet_id,stage FROM V1_PALLETS').fetchall()
        if not accounts or len(pallets)!=8:raise ValueError('운영 계정과 팔레트 8개를 확인하세요.')
        draft=destination.with_name('prepare-'+str(uuid4())+'.sqlite3')
        try:
            initialize_orders(draft);initialize(draft)
            with closing(connect(draft)) as db,db:
                db.execute('DELETE FROM ROLES')
                db.executemany('INSERT INTO ROLES VALUES(?,?,?,?)',roles)
                db.executemany('INSERT INTO ACCOUNTS(account_id,role_id,login_id,password_hash,name,created_at) VALUES(?,?,?,?,?,?)',accounts)
                db.execute("INSERT INTO V1_META VALUES('database_scope','TRANSPORT_TEST')")
                for pallet,stage in pallets:db.execute('UPDATE V1_PALLETS SET stage=? WHERE pallet_id=?',('READY' if stage=='READY' else 'UNAVAILABLE',pallet))
                if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('시험 DB 참조 오류')
            os.replace(draft,destination)
        finally:
            if draft.exists():draft.unlink()
    source_settings=source.parent/'factory-v1.json';test_settings=destination.parent/'factory-v1.json'
    settings=json.loads(source_settings.read_text(encoding='utf-8-sig')) if source_settings.exists() else {}
    settings['mode']='TRANSPORT_TEST'
    draft=test_settings.with_suffix('.tmp');draft.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(draft,test_settings)
    print('운송 시험 DB 준비 완료 · 기존 시험 이력 유지 · 운영 주문·재고·세션 미복사')


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',default=str(ROOT/'instance/mobot.sqlite3'));p.add_argument('--destination',default=str(ROOT/'instance/transport-test/mobot.sqlite3'));args=p.parse_args()
    prepare(args.source,args.destination)


if __name__=='__main__':main()
