"""Install approved AGV connectivity into factory-v1.json, preserving other settings."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server_paths import instance_directory


def main():
    p = argparse.ArgumentParser(description='Configure live AGV adapter connection in factory-v1.json')
    p.add_argument('--agv-url', required=True, help='Private HTTPS adapter, or http://127.0.0.1:port over an SSH tunnel')
    p.add_argument('--controller-token', required=True, help='Controller bearer token (at least 32 chars)')
    p.add_argument('--device-token', required=True, help='AGV event bearer token (at least 32 chars, distinct from controller token)')
    p.add_argument('--timeout-seconds', type=float, default=None, help='Optional request_timeout_seconds for AGV HttpDevice')
    p.add_argument('--settings', default=str(instance_directory() / 'factory-v1.json'))
    args = p.parse_args()

    url = urlsplit(args.agv_url)
    if (
        not (url.scheme == 'https' or (url.scheme == 'http' and url.hostname == '127.0.0.1'))
        or not url.hostname
        or 'ngrok' in url.hostname.lower()
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ('', '/')
    ):
        p.error('내부 HTTPS 또는 암호화 터널의 로컬 HTTP 주소(http://127.0.0.1:port)를 입력하세요.')

    if len(args.controller_token) < 32 or len(args.device_token) < 32 or args.controller_token == args.device_token:
        p.error('AGV 제어 키와 이벤트 키를 각각 32자 이상으로 서로 다르게 설정하세요.')

    path = Path(args.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if path.exists():
        backup = path.with_name(
            path.name + '.before-agv-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.bak'
        )
        backup.write_bytes(path.read_bytes())

    settings['mode'] = 'HARDWARE'
    agv_cfg = {'base_url': args.agv_url.rstrip('/'), 'controller_token': args.controller_token}
    if args.timeout_seconds is not None:
        agv_cfg['request_timeout_seconds'] = args.timeout_seconds
    settings.setdefault('devices', {})['AGV'] = agv_cfg
    settings.setdefault('device_tokens', {})['AGV'] = args.device_token

    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)
    print(f'Updated AGV settings in {path}')


if __name__ == '__main__':
    main()
