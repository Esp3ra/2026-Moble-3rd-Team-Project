import atexit
import os
from .common import *
from .engine import Engine
from .devices import HttpDevice,SimDevice,create_adapter_api
from .vision import PcVision
from .api import create_api
from orders import initialize_orders
from server_paths import instance_directory


def install(app):
    path=Path(app.config['ORDER_DB'])
    config_path=path.parent/'factory-v1.json'
    settings=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    app.config.update(V1_SETTINGS=settings,V1_MODE=os.environ.get('MOBOT_V1_MODE',settings.get('mode','HARDWARE')),
        V1_INTERNAL_NETWORKS=settings.get('internal_networks',['127.0.0.0/8','::1/128','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']),
        V1_PUBLIC_RATE_LIMIT=settings.get('public_requests_per_minute',30),
        V1_DEVICE_TOKENS=settings.get('device_tokens',{}),V1_CONTROLLER_TOKEN=settings.get('controller_token',''),
        V1_ADAPTER_DEVICE=settings.get('adapter_device','AGV'))
    app.config['V1_BACKGROUND']=os.environ.get('MOBOT_V1_BACKGROUND','1')=='1'
    mode=app.config['V1_MODE']
    require(mode in ('HARDWARE','SIMULATION'),'MOBOT_V1_MODE 설정 오류')
    scope='SIMULATION' if mode=='SIMULATION' else 'PRODUCTION'
    if mode=='SIMULATION':
        require(os.environ.get('MOBOT_DB') and path!=(instance_directory()/'mobot.sqlite3').resolve(),
                '모의 검증은 명시한 별도 DB만 사용합니다.',409,'CONFLICT')
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            has_meta=db.execute("SELECT 1 FROM sqlite_master WHERE name='V1_META' AND type='table'").fetchone()
            row=db.execute("SELECT value FROM V1_META WHERE key='database_scope'").fetchone() if has_meta else None
            require((row[0] if row else 'PRODUCTION')==scope,'현재 모드와 DB 범위가 다릅니다.',409,'CONFLICT')
    initialize_orders(path)
    initialize(path)
    with closing(connect(path)) as db,db:
        db.execute("INSERT OR IGNORE INTO V1_META VALUES('database_scope',?)",(scope,))
        row=db.execute("SELECT value FROM V1_META WHERE key='database_scope'").fetchone()
        require(row[0]==scope,'현재 모드와 DB 범위가 다릅니다.',409,'CONFLICT')
    clients={device:SimDevice(device) for device in DEVICES} if mode=='SIMULATION' else {
        device:HttpDevice(device,config) for device,config in settings.get('devices',{}).items() if device in DEVICES
    }
    vision=PcVision(app,app.config['V1_MODE']=='SIMULATION')
    worker=Engine(app,clients,vision)
    app.extensions['factory_v1']=worker
    app.register_blueprint(create_api())
    if mode=='SIMULATION':
        app.register_blueprint(create_adapter_api())

    @app.before_request
    def retire_legacy():
        if request.path.startswith('/api/') and not request.path.startswith('/api/v1/'):
            return Fault(410,'UNSUPPORTED_VERSION','/api/v1을 사용하세요.').body,410

    @app.errorhandler(Fault)
    def fault(error):return error.body,error.status

    @app.errorhandler(400)
    def bad_request(error):return Fault(400,'BAD_REQUEST','요청 본문을 확인하세요.').body,400

    @app.errorhandler(413)
    def too_large(error):return Fault(400,'BAD_REQUEST','요청 본문이 너무 큽니다.').body,400

    @app.errorhandler(500)
    def internal_error(error):return Fault(500,'INTERNAL_ERROR','서버 처리 결과를 확인할 수 없습니다. 상태 조회 후 대조하세요.','QUERY_FIRST').body,500

    def start():
        if app.config.get('V1_BACKGROUND',True):worker.start();vision.start()
    # Lazy startup allows tests to install deterministic adapters first.
    lock=__import__('threading').Lock()
    started=[False]
    def startup():
        with lock:
            if not started[0]:start();started[0]=True
    app.extensions['mobot_start']=startup
    app.before_request(startup)
    atexit.register(worker.close)
