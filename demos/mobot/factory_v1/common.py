import base64
import hashlib
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing,contextmanager
from datetime import datetime,timezone
from functools import wraps
from pathlib import Path
from uuid import UUID,uuid4

from flask import current_app,g,request
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash
from auth import bearer_token,token_hash,ACCOUNT_SELECT as LEGACY_ACCOUNT_SELECT,DUMMY_HASH
from orders import connect

SPEC={'target_head_id':('H1','H2'),'target_arm_l_id':('L1','L2','L3'),'target_arm_r_id':('R1','R2','R3')}
ACCOUNT_SELECT=LEGACY_ACCOUNT_SELECT.replace('SELECT a.account_id,','SELECT a.account_id, a.role_id,')
DEVICES=('LINE','ARM','AGV')


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def uid():return str(uuid4())
def dump(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(dump(value).encode()).hexdigest()


class Fault(Exception):
    def __init__(self,status,code,message,retry='NONE'):
        self.status=status
        self.body=dict(code=code,message=message,retry=retry,trace_id=uid())
        super().__init__(message)


def require(condition,message='요청 형식을 확인하세요.',status=400,code='BAD_REQUEST',retry='NONE'):
    if not condition:raise Fault(status,code,message,retry)


def object_fields(data,required,optional=()):
    require(isinstance(data,dict),'JSON 객체가 필요합니다.')
    require(set(required)<=data.keys() and data.keys()<=set(required)|set(optional),'필수 필드와 허용 필드를 확인하세요.')
    return data


def identifier(value,v4=False):
    require(isinstance(value,str),'UUID 문자열이 필요합니다.')
    try:parsed=UUID(value)
    except (ValueError,TypeError,AttributeError):raise Fault(400,'BAD_REQUEST','UUID 형식이 아닙니다.')
    require(str(parsed)==value.lower() and (not v4 or parsed.version==4),'UUID 형식 또는 버전을 확인하세요.')
    return str(parsed)


def text(value,limit=300):
    require(isinstance(value,str) and 0<len(value.strip())<=limit,'문자열 길이를 확인하세요.')
    return value.strip()


def spec(value):
    object_fields(value,SPEC)
    require(all(value[k] in allowed for k,allowed in SPEC.items()),'부품 코드를 확인하세요.',422,'CONFLICT')
    return {k:value[k] for k in SPEC}


def utc(value):
    require(isinstance(value,str) and value.endswith('Z'),'UTC Z 시각이 필요합니다.')
    try:datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise Fault(400,'BAD_REQUEST','시각 형식을 확인하세요.')
    return value


@contextmanager
def transaction():
    with closing(connect(current_app.config['ORDER_DB'])) as db, db:
        db.execute('BEGIN IMMEDIATE')
        yield db


def initialize(path):
    with closing(connect(path)) as db, db:
        db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        db.execute('INSERT OR IGNORE INTO V1_PLANT(id,updated_at) VALUES(1,?)',(now(),))
        for row in db.execute('SELECT pallet_id,current_status FROM PALLETS').fetchall():
            db.execute('INSERT OR IGNORE INTO V1_PALLETS(pallet_id,stage,updated_at) VALUES(?,?,?)',
                       (row['pallet_id'],'READY' if row['current_status']=='READY' else 'LEGACY_OCCUPIED',now()))
        for device in (*DEVICES,'VISION'):
            boot=uid()
            status=dict(device_id=device,boot_id=boot,mode='AUTO',state='UNKNOWN',cycle_id=None,
                        active_command_id=None,last_completed_command_id=None,observed_at=now(),online=False)
            db.execute('INSERT OR IGNORE INTO V1_DEVICES(device_id,boot_id,status) VALUES(?,?,?)',(device,boot,dump(status)))


def account(row):
    return dict(account_id=str(row['account_id']),role='SUPER_ADMIN' if row['role_code']=='ADMIN' else 'ADMIN',display_name=row['name'])


def admin(super_only=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            with transaction() as db:
                row=db.execute(ACCOUNT_SELECT+''' JOIN V1_SESSIONS s ON s.account_id=a.account_id
                    WHERE s.token_hash=? AND s.role_id=a.role_id AND r.role_code IN ('ADMIN','OPERATOR')''',(token_hash(bearer_token()),)).fetchone()
            require(row is not None,'관리자 로그인이 필요합니다.',401,'UNAUTHORIZED')
            g.account=account(row)
            require(not super_only or g.account['role']=='SUPER_ADMIN','최상위 관리자 권한이 필요합니다.',403,'FORBIDDEN')
            return fn(*args,**kwargs)
        return wrapped
    return decorate


def cached(db,scope,request_id,payload):
    identifier(request_id)
    row=db.execute('SELECT * FROM V1_REQUESTS WHERE scope=? AND request_id=?',(scope,request_id)).fetchone()
    if row:
        same=check_password_hash(row['payload_hash'],dump(payload)) if row['payload_hash'].startswith('scrypt:') else row['payload_hash']==digest(payload)
        require(same,'같은 요청 ID의 내용이 다릅니다.',409,'CONFLICT')
        return json.loads(row['response']),row['status']


def remember(db,scope,request_id,payload,response,status=200):
    fingerprint=generate_password_hash(dump(payload)) if isinstance(payload,dict) and any(k in payload for k in ('password','initial_password')) else digest(payload)
    db.execute('INSERT INTO V1_REQUESTS VALUES(?,?,?,?,?)',(scope,request_id,fingerprint,dump(response),status))
    return response,status


def address_hub(address):
    address=re.sub(r'^\s*(?:\(\s*\d{5}\s*\)|\d{5}(?=\s))\s*','',address)
    region=address.split()[0] if address.split() else ''
    groups={3:('서울','서울시','서울특별시','인천','인천시','인천광역시','경기','경기도','강원','강원도','강원특별자치도','제주','제주도','제주특별자치도'),
            5:('충북','충청북도','충남','충청남도','전북','전라북도','전북특별자치도','전남','전라남도','대전','대전시','대전광역시','세종','세종시','세종특별자치시','광주','광주시','광주광역시'),
            4:('경북','경상북도','경남','경상남도','부산','부산시','부산광역시','대구','대구시','대구광역시','울산','울산시','울산광역시')}
    # Bare '광주시' is ambiguous: require the province or 광주광역시.
    require(region!='광주시','시·도를 포함한 주소가 필요합니다.',422,'INVALID_ADDRESS')
    for hub,aliases in groups.items():
        if region in aliases:return hub
    raise Fault(422,'INVALID_ADDRESS','배송 주소의 시·도 권역을 확인하세요.')


def stock_need(value):
    needs={'BODY':1,value['target_head_id']:1}
    for slot in ('target_arm_l_id','target_arm_r_id'):
        key='ARM_'+value[slot][-1]
        needs[key]=needs.get(key,0)+1
    return needs


def public_order(row):
    return {**{key:row[key] for key in ('order_id','target_station','progress','outcome','updated_at')},'spec':json.loads(row['spec'])}


def admin_order(row):
    return {**{key:row[key] for key in ('order_id','company','phone','address','target_station','dispatch_station',
             'factory_status','approval_seq','cycle_id','pallet_id','created_at','updated_at')},'spec':json.loads(row['spec'])}


def find_order(db,order_id):
    row=db.execute('SELECT * FROM V1_ORDERS WHERE order_id=?',(identifier(order_id,True),)).fetchone()
    require(row is not None,'주문이 없습니다.',404,'NOT_FOUND')
    return row


def history(db,cycle,kind,message,command_id=None,inspection_id=None,extra=None):
    db.execute('INSERT INTO V1_HISTORY VALUES(?,?,?,?,?,?,?,?,?,?)',
        (uid(),cycle['order_id'],cycle['cycle_id'],command_id,inspection_id,now(),cycle['stage'],kind,message,dump(extra or {})))


def page(rows,key,sort_key,limit,cursor):
    # Caller supplies already sorted rows and a query-specific cursor domain.
    start=0
    if cursor:
        try:marker=json.loads(base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4)))
        except Exception:raise Fault(400,'BAD_REQUEST','잘못된 페이지 커서입니다.')
        require(marker.get('key')==sort_key,'다른 조회의 커서입니다.')
        matches=[i for i,row in enumerate(rows) if row[key]==marker.get('id')]
        require(bool(matches),'조회 기준이 달라졌습니다. 첫 페이지부터 조회하세요.')
        start=matches[0]+1
    selected=rows[start:start+limit]
    next_cursor=None
    if start+limit<len(rows):next_cursor=base64.urlsafe_b64encode(dump({'key':sort_key,'id':selected[-1][key],'sort_value':selected[-1].get('created_at',selected[-1].get('recorded_at',selected[-1][key]))}).encode()).decode().rstrip('=')
    return selected,next_cursor


def query_page():
    try:limit=int(request.args.get('limit','50'))
    except ValueError:raise Fault(400,'BAD_REQUEST','limit는 정수입니다.')
    require(1<=limit<=100,'limit 범위는 1~100입니다.')
    cursor=request.args.get('cursor')
    require(cursor is None or 1<=len(cursor)<=256,'커서 길이 오류')
    return limit,cursor
