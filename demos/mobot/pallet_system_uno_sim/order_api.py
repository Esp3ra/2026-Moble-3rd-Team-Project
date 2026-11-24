"""POST /orders 주문 접수 API. 기본 주소: http://127.0.0.1:8000"""
import argparse
import getpass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys

import mysql.connector
from order_service import receive_order, OrderConflict


def make_handler(connection_factory, token=None):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200, {'status': 'ok'}) if self.path == '/health' else self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if self.path != '/orders':
                self.reply(404, {'error': 'Not found'})
                return
            if token and not hmac.compare_digest(
                self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()
            ):
                self.reply(401, {'error': '인증 토큰이 필요합니다.'})
                return
            connection = None
            try:
                if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    self.reply(415, {'error': 'Content-Type: application/json이 필요합니다.'})
                    return
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('Content-Length 방식으로 요청하세요.')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('요청 본문은 1~65536 바이트여야 합니다.')
                data = json.loads(self.rfile.read(length).decode('utf-8'))
                # 연결 실패보다 잘못된 주문정보를 먼저 안내합니다.
                from order_service import validate_order
                validate_order(data)
                connection = connection_factory()
                result = receive_order(connection, data)
                self.reply(201 if result['created'] else 200, result)
            except OrderConflict as error:
                self.reply(409, {'error': str(error)})
            except (ValueError, UnicodeError) as error:
                self.reply(400, {'error': str(error)})
            except mysql.connector.Error:
                self.reply(503, {'error': 'DB 처리 실패. 연결·권한을 확인하고 같은 job_id로 재시도하세요.'})
            finally:
                if connection is not None:
                    connection.close()
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', default='127.0.0.1')
    parser.add_argument('--http-port', type=int, default=8000)
    args = parser.parse_args()
    token = os.getenv('ORDER_API_TOKEN')
    if args.listen not in ('127.0.0.1', 'localhost') and not token:
        parser.error('외부 접속을 허용하려면 ORDER_API_TOKEN 환경 변수를 설정하세요.')
    config = dict(host=os.getenv('DB_HOST', '127.0.0.1'),
                  port=int(os.getenv('DB_PORT', '3306')), user=os.getenv('DB_USER', 'YOUR_DB_USER'),
                  database=os.getenv('DB_NAME', 'moble_smart_factory'),
                  password=os.getenv('DB_PASSWORD'), charset='utf8mb4',
                  autocommit=True, connection_timeout=5)
    if config['password'] is None:
        config['password'] = getpass.getpass('DB 비밀번호: ')
    def connect():
        return mysql.connector.connect(**config)
    try:
        connection = connect()
        connection.close()
        server = ThreadingHTTPServer((args.listen, args.http_port), make_handler(connect, token))
        print(f'주문 API 실행: http://{args.listen}:{args.http_port}/orders (종료: Ctrl+C)', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (mysql.connector.Error, OSError) as error:
        print(f'API 시작 실패: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
