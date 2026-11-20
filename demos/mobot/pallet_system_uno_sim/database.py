"""장비 제어 루프와 독립적으로 QR·IR 및 장비 완료 확인 후 DB 작업을 수행합니다."""
import copy
import os
import threading

if __package__:
    from .pallet_repository import lookup, validate_pallet_id
    from .pallet_mapping import assign_next_order
else:
    from pallet_repository import lookup, validate_pallet_id
    from pallet_mapping import assign_next_order


class DatabaseWorker:
    def __init__(self, bench, mode='lookup'):
        self.bench, self.mode = bench, mode
        self.lock = threading.Lock()
        self.handled = None
        self.data = {'status': 'waiting', 'message': 'QR·UNO 알림·IR 정지·장비 완료 응답 대기'}

    def connect(self):
        import mysql.connector
        password = os.getenv('DB_PASSWORD')
        if password is None:
            raise ValueError('DB_PASSWORD 환경 변수를 설정하세요')
        return mysql.connector.connect(
            host=os.getenv('DB_HOST', '127.0.0.1'),
            port=int(os.getenv('DB_PORT', '3306')),
            user=os.getenv('DB_USER', 'YOUR_DB_USER'), password=password,
            database=os.getenv('DB_NAME', 'moble_smart_factory'),
            charset='utf8mb4', autocommit=True, connection_timeout=5,
            read_timeout=5, write_timeout=5)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def retry(self):
        with self.lock:
            if self.data['status'] != 'error':
                raise ValueError('실패한 DB 작업만 재시도할 수 있습니다')
            run_id = self.data.get('run_id')
            self.handled = None
            self.data = {'status': 'waiting', 'run_id': run_id, 'message': 'DB 재시도 대기'}

    @staticmethod
    def eligible(state):
        trial = state['trial']
        return (state['state'] in {'AWAIT_CONFIRM', 'SUCCESS'}
                and all(trial.get(key) for key in
                        ('qr_ok', 'qr_ack', 'ir_triggered', 'first_belt_stopped', 'sim_resumed', 'ir2_triggered', 'belt_stopped', 'machine_done')))

    def blocks_next_run(self, state):
        with self.lock:
            return (self.data['status'] in {'processing', 'error'}
                    or (self.eligible(state)
                        and not (self.data.get('run_id') == state['trial'].get('id')
                                 and self.data['status'] == 'success')))

    def run(self):
        while not self.bench.closed.wait(.2):
            self.process_once()

    def process_once(self):
        state = self.bench.snapshot()
        trial = state['trial']
        run_id = trial.get('id')
        with self.lock:
            if self.data.get('run_id') != run_id:
                self.data = {'status': 'waiting', 'run_id': run_id,
                             'message': 'QR·UNO 알림·IR 정지·장비 완료 응답 대기'}
            if not run_id or run_id == self.handled:
                return
            if not self.eligible(state):
                if state['state'] in {'QR_UNCONFIRMED', 'ERROR', 'STOPPED'}:
                    self.data.update(status='blocked', message='미확인 또는 중단 회차: DB 처리 차단')
                return
            self.handled = run_id
            self.data = {'status': 'processing', 'run_id': run_id, 'message': 'DB 처리 중'}
        connection = None
        try:
            if self.bench.demo:
                result = None
                message = '가상 데모: 실제 DB 조회·변경 생략'
            else:
                pallet_id = validate_pallet_id(trial['pallet_id'])
                connection = self.connect()
                if self.mode == 'auto':
                    assign_next_order(connection, pallet_id)
                result = lookup(connection, pallet_id)
                message = '주문 연결 완료' if self.mode == 'auto' else '활성 주문 조회 완료'
            data = {'status': 'success', 'run_id': run_id, 'message': message, 'result': result}
        except Exception as exc:
            data = {'status': 'error', 'run_id': run_id, 'message': str(exc)}
        finally:
            if connection is not None:
                try: connection.close()
                except Exception: pass
        with self.lock:
            self.data = data
