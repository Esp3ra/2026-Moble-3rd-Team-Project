"""Optional independent GET polling for a device with a short controller lease."""
import threading
import math
from .common import require
from .devices import HttpDevice
from . import contracts


class DeviceKeepalives:
    def __init__(self, engine):
        self.engine=engine
        self.closed=threading.Event()
        self.threads=[]
        self.targets=[]
        for device,config in engine.app.config['V1_SETTINGS'].get('devices',{}).items():
            interval=config.get('controller_keepalive_seconds')
            if interval is None:continue
            require(device=='LINE' and type(interval) in (int,float) and math.isfinite(interval) and 0<interval<=.25,
                    '독립 LINE 제어 유지 주기는 0초 초과·0.25초 이하여야 합니다.')
            client=HttpDevice(device,{**config,'request_timeout_seconds':.4})
            self.targets.append((device,interval,client))

    def start(self):
        for device,interval,client in self.targets:
            def poll(device=device,interval=interval,client=client):
                with self.engine.app.app_context():
                    while not self.closed.wait(interval):
                        try:
                            # Never renew hardware after this server loses its DB controller lease.
                            self.engine.acquire()
                            contracts.device_status(client.status(),device)
                        except Exception:
                            # GET only; normal worker records device faults. No command retries/reconnects.
                            pass
            thread=threading.Thread(target=poll,name='controller-keepalive-'+device,daemon=True)
            self.threads.append(thread);thread.start()

    def close(self):
        self.closed.set()
        for thread in self.threads:thread.join(timeout=2)
