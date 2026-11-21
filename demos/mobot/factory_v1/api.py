from flask import Blueprint,Response,jsonify
from werkzeug.security import generate_password_hash
from .common import *


def client_address():
    address=request.remote_addr or ''
    try:
        if ipaddress.ip_address(address).is_loopback and request.headers.get('X-Forwarded-For'):
            address=request.headers['X-Forwarded-For'].split(',')[-1].strip()
        return str(ipaddress.ip_address(address))
    except ValueError:return 'invalid'


def internal_client():
    try:return any(ipaddress.ip_address(client_address()) in ipaddress.ip_network(cidr) for cidr in current_app.config['V1_INTERNAL_NETWORKS'])
    except ValueError:return False


def throttle(category):
    limit=current_app.config['V1_PUBLIC_RATE_LIMIT']
    key=digest([category,client_address()])
    window=int(time.time()//60)
    with transaction() as db:
        db.execute('INSERT INTO V1_RATE_LIMIT VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET hits=CASE WHEN window=excluded.window THEN hits+1 ELSE 1 END,window=excluded.window',(key,window))
        hits=db.execute('SELECT hits FROM V1_RATE_LIMIT WHERE key=?',(key,)).fetchone()[0]
    require(hits<=limit,'요청이 너무 많습니다. 잠시 후 다시 시도하세요.',429,'RATE_LIMITED')


def body(required,optional=()):return object_fields(request.get_json(silent=True),required,optional)
def engine():return current_app.extensions['factory_v1']


def restore_stock(db,row):
    if row['stock_restored']:return
    for key,units in stock_need(json.loads(row['spec'])).items():
        db.execute('UPDATE V1_STOCK SET available_units=available_units+? WHERE stock_key=?',(units,key))
    db.execute('UPDATE V1_ORDERS SET stock_restored=1 WHERE order_id=?',(row['order_id'],))


def create_api():
    api=Blueprint('v1',__name__,url_prefix='/api/v1')

    @api.before_request
    def scope():
        public={'v1.catalog','v1.submit_order','v1.lookup','v1.public_cancel'}
        if request.endpoint not in public:
            require(internal_client(),'관리자·장비 API는 허용된 내부망에서만 접속할 수 있습니다.',403,'FORBIDDEN')
        uuid_fields={'order_id','request_id','cycle_id','command_id','event_id','boot_id','inspection_id','previous_read_id','pause_command_id','trace_id','record_id'}
        def normalize(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in uuid_fields and item is not None:value[key]=identifier(item)
                    else:normalize(item)
            elif isinstance(value,list):
                for item in value:normalize(item)
        if request.is_json:normalize(request.get_json(silent=True))
        if request.view_args:
            for key,value in request.view_args.items():
                if key in uuid_fields:request.view_args[key]=identifier(value)

    @api.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Mobot-Contract']='0.1.0'
        response.headers['X-Mobot-Environment']=current_app.config['V1_MODE']
        return response

    @api.errorhandler(Fault)
    def fault(error):return error.body,error.status

    @api.errorhandler(sqlite3.Error)
    def unavailable(error):return Fault(503,'DEVICE_UNAVAILABLE','DB 처리 실패 · 같은 요청 ID로 결과를 조회하세요.','QUERY_FIRST').body,503

    @api.get('/catalog')
    def catalog():
        with transaction() as db:stock=dict(db.execute('SELECT stock_key,available_units FROM V1_STOCK'))
        return dict(heads=[dict(id=k,available_units=stock[k]) for k in ('H1','H2')],
                    arms=[dict(left_code='L'+str(i),right_code='R'+str(i),shared_units=stock['ARM_'+str(i)]) for i in (1,2,3)],body_available=stock['BODY']>0)

    @api.post('/orders')
    def submit_order():
        throttle('submit')
        data=body(('request_id','company','phone','address','spec','payment_method'))
        data={**data, 'request_id':identifier(data['request_id']), 'company':text(data['company'],100),
              'phone':text(data['phone'],40),'address':text(data['address'],300),'spec':spec(data['spec'])}
        require(data['payment_method'] in ('card','transfer'),'모의 결제 수단 오류')
        hub=address_hub(data['address'])
        with transaction() as db:
            previous=cached(db,'public:submit',data['request_id'],data)
            if previous:return previous[0],200
            for key,units in stock_need(data['spec']).items():
                available=db.execute('SELECT available_units FROM V1_STOCK WHERE stock_key=?',(key,)).fetchone()[0]
                require(available>=units,'선택 부품의 공용 재고가 부족합니다.',409,'OUT_OF_STOCK')
            for key,units in stock_need(data['spec']).items():db.execute('UPDATE V1_STOCK SET available_units=available_units-? WHERE stock_key=?',(units,key))
            order_id=uid();number=db.execute('SELECT COALESCE(MAX(internal_number),0)+1 FROM V1_ORDERS').fetchone()[0]
            db.execute('INSERT INTO V1_ORDERS(order_id,internal_number,company,phone,address,spec,payment_method,target_station,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (order_id,number,data['company'],data['phone'],data['address'],dump(data['spec']),data['payment_method'],hub,now(),now()))
            result=public_order(find_order(db,order_id))
            remember(db,'public:submit',data['request_id'],data,result,201)
        return result,201

    @api.post('/orders/lookup')
    def lookup():
        throttle('lookup');data=body(('order_id',))
        with transaction() as db:return public_order(find_order(db,data['order_id']))

    @api.post('/orders/cancel')
    def public_cancel():
        throttle('cancel');data=body(('order_id','request_id'))
        with transaction() as db:
            row=find_order(db,data['order_id'])
            prior=cached(db,'public:cancel',data['request_id'],data)
            if prior:return prior
            require(row['factory_status'] in ('PENDING','CANCELLED'),'제작 수락 전에만 고객 취소가 가능합니다.',409,'BAD_TRANSITION')
            restore_stock(db,row)
            db.execute("UPDATE V1_ORDERS SET factory_status='CANCELLED',outcome='CANCELLED',updated_at=? WHERE order_id=?",(now(),row['order_id']))
            result=public_order(find_order(db,row['order_id']))
            return remember(db,'public:cancel',data['request_id'],data,result)

    @api.post('/auth/login')
    def login():
        throttle('login');data=body(('login_id','password'))
        login_id=text(data['login_id'],128);password=data['password']
        require(isinstance(password,str) and 1<=len(password)<=1024,'비밀번호 문자열을 확인하세요.')
        with transaction() as db:
            row=db.execute(ACCOUNT_SELECT+" WHERE a.login_id=? AND r.role_code IN('ADMIN','OPERATOR')",(login_id,)).fetchone()
            valid=check_password_hash(row['password_hash'] if row else DUMMY_HASH,password)
            require(row is not None and valid,'아이디 또는 비밀번호를 확인하세요.',401,'UNAUTHORIZED')
            token=secrets.token_urlsafe(40)
            db.execute('INSERT INTO V1_SESSIONS VALUES(?,?,?,?)',(token_hash(token),row['account_id'],row['role_id'],now()))
            return dict(token=token,account=account(row))

    @api.get('/auth/me')
    @admin()
    def me():return g.account

    @api.post('/auth/logout')
    @admin()
    def logout():
        with transaction() as db:db.execute('DELETE FROM V1_SESSIONS WHERE token_hash=?',(token_hash(bearer_token()),))
        return Response(status=204)

    @api.get('/orders')
    @admin()
    def list_orders():
        limit,cursor=query_page()
        with transaction() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM V1_ORDERS ORDER BY created_at DESC,order_id DESC')]
            rows,next_cursor=page(rows,'order_id','orders',limit,cursor)
            return dict(orders=[admin_order(row) for row in rows],next_cursor=next_cursor)

    @api.get('/orders/<order_id>')
    @admin()
    def get_order(order_id):
        with transaction() as db:return admin_order(find_order(db,order_id))

    @api.post('/orders/<order_id>/accept')
    @admin()
    def approve(order_id):
        data=body(('request_id',));identifier(data['request_id'])
        payload=dict(order_id=identifier(order_id,True),**data);scope='accept:'+g.account['account_id']
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],payload)
            if prior:return prior
            row=find_order(db,order_id)
            require(row['factory_status'] in ('PENDING','QUEUED'),'수락 가능한 대기 주문이 아닙니다.',409,'BAD_TRANSITION')
            if row['factory_status']=='PENDING':
                seq=db.execute('SELECT COALESCE(MAX(approval_seq),0)+1 FROM V1_ORDERS').fetchone()[0]
                db.execute("UPDATE V1_ORDERS SET factory_status='QUEUED',approval_seq=?,updated_at=? WHERE order_id=?",(seq,now(),order_id))
            result=admin_order(find_order(db,order_id))
            return remember(db,scope,data['request_id'],payload,result)

    @api.post('/orders/<order_id>/cancel')
    @admin()
    def cancel(order_id):
        data=body(('request_id',));payload=dict(order_id=identifier(order_id,True),**data);scope='cancel:'+g.account['account_id']
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],payload)
            if prior:return prior
            row=find_order(db,order_id)
            require(row['factory_status'] in ('PENDING','QUEUED','CANCELLED') and row['cycle_id'] is None,'배출 지시 전 대기 주문만 취소할 수 있습니다.',409,'BAD_TRANSITION','QUERY_FIRST')
            restore_stock(db,row)
            db.execute("UPDATE V1_ORDERS SET factory_status='CANCELLED',outcome='CANCELLED',updated_at=? WHERE order_id=?",(now(),order_id))
            return remember(db,scope,data['request_id'],payload,admin_order(find_order(db,order_id)))

    @api.get('/pallets')
    @admin()
    def pallets():
        limit,cursor=query_page()
        with transaction() as db:rows=[dict(r) for r in db.execute('SELECT * FROM V1_PALLETS ORDER BY pallet_id')]
        rows,next_cursor=page(rows,'pallet_id','pallets',limit,cursor)
        return dict(pallets=rows,next_cursor=next_cursor)

    @api.get('/plant')
    @admin()
    def plant():return engine().plant()

    @api.post('/plant/actions')
    @admin()
    def plant_action():return engine().action(g.account['account_id'],request.get_json(silent=True))

    @api.get('/plant/actions/<request_id>')
    @admin()
    def plant_ticket(request_id):
        with transaction() as db:return engine().ticket(db,request_id)

    @api.post('/cycles/<cycle_id>/qr-retry')
    @admin()
    def qr_retry(cycle_id):return engine().retry_qr(g.account['account_id'],cycle_id,request.get_json(silent=True))

    @api.get('/cycles/<cycle_id>/qr-retry/<request_id>')
    @admin()
    def qr_ticket(cycle_id,request_id):
        with transaction() as db:return engine().qr_ticket(db,cycle_id,request_id)

    @api.get('/orders/<order_id>/history')
    @admin()
    def order_history(order_id):
        limit,cursor=query_page()
        with transaction() as db:
            find_order(db,order_id)
            rows=[dict(r) for r in db.execute('SELECT * FROM V1_HISTORY WHERE order_id=? ORDER BY recorded_at DESC,record_id DESC',(order_id,))]
        rows,next_cursor=page(rows,'record_id','history:'+order_id,limit,cursor)
        return dict(events=[{**{k:v for k,v in row.items() if k!='extra'},**json.loads(row['extra'])} for row in rows],next_cursor=next_cursor)

    @api.get('/inspections/<inspection_id>')
    @admin()
    def inspection(inspection_id):
        with transaction() as db:row=db.execute('SELECT * FROM V1_INSPECTIONS WHERE inspection_id=?',(identifier(inspection_id),)).fetchone()
        require(row and row['body'],'검사 결과가 없습니다.',404,'NOT_FOUND')
        result=json.loads(row['body'])
        if row['archive_id']:result['archive_id']=row['archive_id']
        return result

    @api.get('/inspections/<inspection_id>/image')
    @admin()
    def inspection_image(inspection_id):
        with transaction() as db:row=db.execute('SELECT image FROM V1_INSPECTIONS WHERE inspection_id=?',(identifier(inspection_id),)).fetchone()
        require(row and row['image'],'보관된 검사 사진이 없습니다.',404,'NOT_FOUND')
        with transaction() as db:captured=json.loads(db.execute('SELECT body FROM V1_INSPECTIONS WHERE inspection_id=?',(inspection_id,)).fetchone()[0])['captured_at']
        return Response(row[0],mimetype='image/jpeg',headers={'X-Captured-At':captured})

    @api.get('/cameras/<camera_id>/frame')
    @admin()
    def camera(camera_id):
        require(camera_id in ('CAM1','CAM2','CAM3','CAM4'),'카메라가 없습니다.',404,'NOT_FOUND')
        image,captured=engine().vision.jpeg(camera_id.lower())
        require(image is not None,'카메라 영상을 수신하지 못했습니다.',503,'DEVICE_UNAVAILABLE','QUERY_FIRST')
        return Response(image,mimetype='image/jpeg',headers={'X-Captured-At':captured})

    @api.post('/device-events')
    def device_event():
        from . import contracts
        data=request.get_json(silent=True)
        object_fields(data,('event_id','device_id','boot_id','event_seq','cycle_id','command_id','occurred_at','kind','data'))
        incoming=token_hash(bearer_token())
        source=next((name for name,key in current_app.config['V1_DEVICE_TOKENS'].items() if secrets.compare_digest(incoming,token_hash(key))),None)
        require(source is not None,'장비 인증 실패',401,'UNAUTHORIZED')
        require(source==data['device_id'],'인증 장비와 이벤트 장비가 다릅니다.',403,'FORBIDDEN')
        try:return engine().record(data)
        except Fault as error:
            if error.status==409:engine().hold(error.body['message'],source,data.get('command_id'))
            raise

    @api.get('/stock')
    @admin()
    def stock():
        limit,cursor=query_page()
        with transaction() as db:rows=[dict(r) for r in db.execute('SELECT * FROM V1_STOCK ORDER BY stock_key')]
        rows,next_cursor=page(rows,'stock_key','stock',limit,cursor)
        return dict(items=rows,next_cursor=next_cursor)

    @api.post('/stock/restocks')
    @admin(True)
    def restock():
        data=body(('request_id','stock_key','added_units'))
        require(data['stock_key'] in ('H1','H2','ARM_1','ARM_2','ARM_3','BODY') and type(data['added_units']) is int and 1<=data['added_units']<=2147483647,'재입고 수량·부품 오류')
        scope='restock:'+g.account['account_id']
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],data)
            if prior:return prior
            db.execute('UPDATE V1_STOCK SET available_units=available_units+? WHERE stock_key=?',(data['added_units'],data['stock_key']))
            result=dict(db.execute('SELECT * FROM V1_STOCK WHERE stock_key=?',(data['stock_key'],)).fetchone())
            db.execute('INSERT INTO V1_AUDIT VALUES(?,?,?,?,?)',(uid(),g.account['account_id'],'RESTOCK',dump(data),now()))
            return remember(db,scope,data['request_id'],data,result)

    @api.get('/accounts')
    @admin(True)
    def accounts():
        limit,cursor=query_page()
        with transaction() as db:rows=[account(r) for r in db.execute(ACCOUNT_SELECT+' ORDER BY a.account_id')]
        rows,next_cursor=page(rows,'account_id','accounts',limit,cursor)
        return dict(accounts=rows,next_cursor=next_cursor)

    @api.post('/accounts')
    @admin(True)
    def create_account():
        data=body(('request_id','login_id','display_name','role','initial_password'))
        identifier(data['request_id']);text(data['login_id'],128);text(data['display_name'],100);text(data['initial_password'],1024)
        require(data['role'] in ('ADMIN','SUPER_ADMIN'),'권한 코드 오류')
        scope='account:create:'+g.account['account_id']
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],data)
            if prior:return prior[0],200
            require(not db.execute('SELECT 1 FROM ACCOUNTS WHERE login_id=?',(data['login_id'],)).fetchone(),'이미 있는 아이디입니다.',409,'CONFLICT')
            role='ADMIN' if data['role']=='SUPER_ADMIN' else 'OPERATOR'
            role_id=db.execute('SELECT role_id FROM ROLES WHERE role_code=?',(role,)).fetchone()[0]
            account_id=db.execute('INSERT INTO ACCOUNTS(role_id,login_id,name,password_hash) VALUES(?,?,?,?)',(role_id,data['login_id'],data['display_name'],generate_password_hash(data['initial_password']))).lastrowid
            result=account(db.execute(ACCOUNT_SELECT+' WHERE a.account_id=?',(account_id,)).fetchone())
            return remember(db,scope,data['request_id'],data,result,201)

    @api.patch('/accounts/<account_id>/role')
    @admin(True)
    def update_role(account_id):
        data=body(('request_id','role'));require(data['role'] in ('ADMIN','SUPER_ADMIN'),'권한 코드 오류')
        scope='account:role:'+g.account['account_id'];payload=dict(account_id=account_id,**data)
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],payload)
            if prior:return prior
            row=db.execute(ACCOUNT_SELECT+' WHERE a.account_id=?',(account_id,)).fetchone()
            require(row is not None,'계정이 없습니다.',404,'NOT_FOUND')
            if row['role_code']=='ADMIN' and data['role']=='ADMIN':
                require(db.execute("SELECT count(*) FROM ACCOUNTS a JOIN ROLES r ON a.role_id=r.role_id WHERE r.role_code='ADMIN'").fetchone()[0]>1,'마지막 최상위 관리자는 변경할 수 없습니다.',409,'CONFLICT')
            role_id=db.execute('SELECT role_id FROM ROLES WHERE role_code=?',('ADMIN' if data['role']=='SUPER_ADMIN' else 'OPERATOR',)).fetchone()[0]
            db.execute('UPDATE ACCOUNTS SET role_id=? WHERE account_id=?',(role_id,account_id))
            db.execute('DELETE FROM V1_SESSIONS WHERE account_id=?',(account_id,))
            db.execute('DELETE FROM ADMIN_SESSIONS WHERE account_id=?',(account_id,))
            result=account(db.execute(ACCOUNT_SELECT+' WHERE a.account_id=?',(account_id,)).fetchone())
            return remember(db,scope,data['request_id'],payload,result)

    @api.post('/commands/<command_id>/reconcile')
    @admin()
    def reconcile(command_id):
        data=body(('request_id','observation','reason'),('evidence_ref',))
        identifier(command_id);text(data['reason'],300)
        if 'evidence_ref' in data:text(data['evidence_ref'],256)
        require(data['observation'] in ('COMPLETED','NOT_EXECUTED','UNRESOLVED'),'관측 코드 오류')
        payload=dict(command_id=command_id,**data);scope='reconcile:'+g.account['account_id']
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],payload)
            if prior:return prior
            row=db.execute('SELECT * FROM V1_COMMANDS WHERE command_id=?',(command_id,)).fetchone()
            require(row is not None,'명령이 없습니다.',404,'NOT_FOUND')
            plant=db.execute('SELECT * FROM V1_PLANT').fetchone()
            require(plant['state']=='RECOVERY_REQUIRED' and row['state'] in ('UNKNOWN','FAILED','STOPPED','COMPLETED','RUNNING'),'현장 대조가 필요한 명령이 아닙니다.',409,'BAD_TRANSITION')
            devices=[json.loads(r[0]) for r in db.execute("SELECT status FROM V1_DEVICES WHERE device_id IN('LINE','ARM','AGV')")]
            available=len(devices)==3 and all(d['online'] and d['state'] in ('IDLE','PAUSED') and d['active_command_id'] is None for d in devices)
            other_unknown=db.execute("SELECT 1 FROM V1_COMMANDS WHERE state IN('UNKNOWN','FAILED') AND command_id!=?",(command_id,)).fetchone()
            resolved=False
            if available:
                if data['observation']=='COMPLETED' and row['state']=='COMPLETED' and row['result_event_id']:
                    # Only an already received, typed device event may advance the stage.
                    resolved=True
                elif data['observation']=='NOT_EXECUTED' and row['state'] in ('UNKNOWN','FAILED'):
                    db.execute("UPDATE V1_COMMANDS SET state='STOPPED',updated_at=? WHERE command_id=?",(now(),command_id))
                    if row['action'] not in ('PAUSE','RESUME'):engine().queue(db,row['device_id'],row['cycle_id'],row['action'],json.loads(row['params']))
                    resolved=True
            if resolved and not other_unknown:
                ticket=uid()
                pause_ids={device:engine().queue(db,device,row['cycle_id'],'PAUSE',ticket=ticket) for device in DEVICES}
                db.execute("INSERT INTO V1_TICKETS VALUES(?,'PLANT','ACCEPTED',?,NULL)",(ticket,row['cycle_id']))
                db.execute("UPDATE V1_PLANT SET state='PAUSING',pause_ids=?,action_request=?,updated_at=? WHERE id=1",(dump(pause_ids),ticket,now()))
            result=dict(command_id=command_id,resolution=data['observation'],requires_review=not resolved or bool(other_unknown),recorded_at=now())
            db.execute('INSERT INTO V1_AUDIT VALUES(?,?,?,?,?)',(uid(),g.account['account_id'],'RECONCILE',dump(payload),now()))
            return remember(db,scope,data['request_id'],payload,result)

    @api.post('/manual-actions')
    @admin()
    def manual():
        data=body(('request_id','device_id','procedure_id','reason'))
        identifier(data['request_id']);text(data['reason'],200);text(data['procedure_id'],64)
        require(data['device_id'] in DEVICES,'장비 ID 오류')
        # No physical procedure or ownership handover has yet been approved by the team.
        raise Fault(503,'DEVICE_UNAVAILABLE','승인된 수동 절차·제어권 전환 프로필을 먼저 등록하세요.','OPERATOR')

    @api.get('/manual-actions/<command_id>')
    @admin()
    def manual_result(command_id):
        identifier(command_id);raise Fault(404,'NOT_FOUND','수동 조작 요청이 없습니다.')

    @api.get('/settings/<device_id>')
    @admin(True)
    def setting(device_id):
        require(device_id in DEVICES,'장비 ID 오류');raise Fault(404,'NOT_FOUND','승인·적용된 설비 프로필이 없습니다.')

    @api.put('/settings/<device_id>')
    @admin(True)
    def change_setting(device_id):
        data=body(('request_id','device_id','validated_profile_id','expected_revision'))
        identifier(data['request_id']);text(data['validated_profile_id'],128)
        require(device_id in DEVICES and device_id==data['device_id'] and type(data['expected_revision']) is int and data['expected_revision']>=1,'설비·리비전 오류')
        raise Fault(503,'DEVICE_UNAVAILABLE','실측·승인된 설비 설정 프로필이 필요합니다.','OPERATOR')

    @api.post('/settlements/query')
    @admin(True)
    def settlement():
        data=body(('from_utc','to_utc'));utc(data['from_utc']);utc(data['to_utc'])
        require(data['from_utc']<data['to_utc'],'조회 기간 오류',422,'CONFLICT')
        raise Fault(503,'DEVICE_UNAVAILABLE','결산 산식·가격·기간 정책 승인 후 활성화합니다.','OPERATOR')

    @api.post('/archive/<record_id>/delete')
    @admin(True)
    def delete_archive(record_id):
        data=body(('request_id','reason'));identifier(record_id);text(data['reason'],300)
        payload=dict(record_id=record_id,**data);scope='delete:'+g.account['account_id']
        with transaction() as db:
            prior=cached(db,scope,data['request_id'],payload)
            if prior:return prior
            record=db.execute('SELECT cycle_id FROM V1_INSPECTIONS WHERE archive_id=? UNION ALL SELECT cycle_id FROM V1_HISTORY WHERE record_id=?',(record_id,record_id)).fetchone()
            require(record is not None,'보관자료가 없습니다.',404,'NOT_FOUND')
            require(not db.execute('SELECT 1 FROM V1_CYCLES WHERE cycle_id=? AND closed=0',(record[0],)).fetchone(),'실행 중 회차의 증거는 삭제할 수 없습니다.',409,'CONFLICT')
            db.execute('UPDATE V1_INSPECTIONS SET image=NULL,archive_id=NULL WHERE archive_id=?',(record_id,))
            db.execute('DELETE FROM V1_HISTORY WHERE record_id=?',(record_id,))
            db.execute('INSERT INTO V1_AUDIT VALUES(?,?,?,?,?)',(uid(),g.account['account_id'],'ARCHIVE_DELETE',dump(payload),now()))
            return remember(db,scope,data['request_id'],payload,dict(request_id=data['request_id'],record_id=record_id,deleted_at=now()))

    return api
