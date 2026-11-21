import atexit
import os
from .common import *
from .engine import Engine
from .devices import HttpDevice,SimDevice,create_adapter_api
from .vision import PcVision
from .api import create_api,internal_client


def install(app):
    path=Path(app.config['ORDER_DB'])
    initialize(path)
    config_path=path.parent/'factory-v1.json'
    settings=json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    app.config.update(V1_SETTINGS=settings,V1_MODE=os.environ.get('MOBOT_V1_MODE',settings.get('mode','HARDWARE')),
        V1_INTERNAL_NETWORKS=settings.get('internal_networks',['127.0.0.0/8','::1/128','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']),
        V1_PUBLIC_RATE_LIMIT=settings.get('public_requests_per_minute',30),
        V1_DEVICE_TOKENS=settings.get('device_tokens',{}),V1_CONTROLLER_TOKEN=settings.get('controller_token',''),
        V1_ADAPTER_DEVICE=settings.get('adapter_device','AGV'))
    app.config['V1_BACKGROUND']=os.environ.get('MOBOT_V1_BACKGROUND','1')=='1'
    require(app.config['V1_MODE'] in ('HARDWARE','SIMULATION'),'MOBOT_V1_MODE 설정 오류')
    clients={device:SimDevice(device) for device in DEVICES} if app.config['V1_MODE']=='SIMULATION' else {device:HttpDevice(device,config) for device,config in settings.get('devices',{}).items() if device in DEVICES}
    vision=PcVision(app,app.config['V1_MODE']=='SIMULATION')
    worker=Engine(app,clients,vision)
    app.extensions['factory_v1']=worker
    app.register_blueprint(create_api())
    app.register_blueprint(create_adapter_api())

    @app.before_request
    def retire_legacy():
        if request.path.startswith('/api/') and not request.path.startswith('/api/v1/'):
            if not current_app.config.get('LEGACY_API_ENABLED',False):return Fault(410,'UNSUPPORTED_VERSION','이전 시험 API입니다. /api/v1을 사용하세요.').body,410
            if not internal_client():return Fault(403,'FORBIDDEN','이전 시험 API는 내부망 전용입니다.').body,403

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
    @app.before_request
    def startup():
        with lock:
            if not started[0]:start();started[0]=True
    atexit.register(worker.close)
