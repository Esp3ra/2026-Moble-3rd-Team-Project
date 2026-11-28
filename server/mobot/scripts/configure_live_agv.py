"""Configure the same-PC AGV adapter without changing other device settings."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server_paths import instance_directory


def settings_path():
    return (Path(os.environ['MOBOT_DB']).resolve().parent if os.environ.get('MOBOT_DB')
            else instance_directory()) / 'factory-v1.json'


def local_address(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
            or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('Use http://127.0.0.1:port for this same-PC adapter.')
    return url


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('Timing values must be finite and positive.')
    return number


def save_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path.with_name(path.name + '.before-agv-' + stamp + '.bak').write_bytes(path.read_bytes())
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def configure(path, device_url=None, key_file=None, adapter_url=None, command_timeout=None,
              http_timeout=None, device_timeout=None):
    path = Path(path).resolve()
    settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    profile_path = path.with_name('agv-adapter.json')
    profile = json.loads(profile_path.read_text(encoding='utf-8-sig')) if profile_path.exists() else {}
    agv = dict(settings.get('devices', {}).get('AGV', {}))
    address = local_address(adapter_url or agv.get('base_url', 'http://127.0.0.1:8765'))
    # The business URL and local listening endpoint are separate fields. This
    # launcher supports same-PC HTTP only; it cannot terminate remote TLS.
    profile.update(listen_host='127.0.0.1', listen_port=address.port)
    if device_url is not None:
        target = urlsplit(device_url)
        if (target.scheme not in ('http', 'https') or not target.hostname or target.username
                or target.password or target.query or target.fragment or target.path not in ('', '/')):
            raise ValueError('Provide the private ESP32 base URL without credentials.')
        profile['device_url'] = device_url.rstrip('/')
    if key_file is not None:
        profile['device_key_file'] = str(Path(key_file).resolve())
    if command_timeout is not None:
        profile['command_timeout_seconds'] = positive(command_timeout)
    if device_timeout is not None:
        profile['device_request_timeout_seconds'] = positive(device_timeout)
    if not all(profile.get(key) for key in ('device_url', 'device_key_file', 'command_timeout_seconds')):
        raise ValueError('First setup requires device URL, private key file and a separate completion timeout.')
    positive(profile['command_timeout_seconds'])
    profile.setdefault('device_request_timeout_seconds', 1.0)
    profile.setdefault('heartbeat_seconds', 0.3)
    profile.setdefault('poll_seconds', 1.0)
    profile.setdefault('events_url', 'http://127.0.0.1:8000/api/v1/device-events')
    profile.setdefault('journal_path', str(instance_directory().parent / 'agv_journal.sqlite3'))
    agv.update(base_url=f'http://127.0.0.1:{address.port}')
    agv.setdefault('controller_token', secrets.token_urlsafe(32))
    if http_timeout is not None:
        agv['request_timeout_seconds'] = positive(http_timeout)
    else:
        agv.setdefault('request_timeout_seconds', 2.0)
    positive(agv['request_timeout_seconds'])
    token = settings.get('device_tokens', {}).get('AGV') or secrets.token_urlsafe(32)
    if (not isinstance(agv['controller_token'], str) or len(agv['controller_token']) < 32
            or not isinstance(token, str) or len(token) < 32 or token == agv['controller_token']):
        raise ValueError('Controller/event credentials must be distinct and at least 32 characters.')
    if settings.get('mode', 'HARDWARE') != 'HARDWARE':
        raise ValueError('Do not convert a simulation configuration into live hardware settings.')
    settings['mode'] = 'HARDWARE'
    settings.setdefault('devices', {})['AGV'] = agv
    settings.setdefault('device_tokens', {})['AGV'] = token
    save_private(profile_path, profile)
    save_private(path, settings)
    return profile_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=settings_path())
    parser.add_argument('--agv-url', help='Same-PC adapter URL, distinct from the ESP32 URL')
    parser.add_argument('--device-url')
    parser.add_argument('--device-key-file', type=Path, help='Private agv_secrets.h; key never printed')
    parser.add_argument('--command-timeout-seconds', type=float)
    parser.add_argument('--timeout-seconds', type=float, help='Business-server HTTP timeout only')
    parser.add_argument('--device-timeout-seconds', type=float, help='ESP32 HTTP timeout only')
    args = parser.parse_args()
    try:
        profile = configure(args.settings, args.device_url, args.device_key_file, args.agv_url,
                            args.command_timeout_seconds, args.timeout_seconds, args.device_timeout_seconds)
    except (ValueError, OSError):
        parser.exit(1, 'AGV configuration failed. Check local URLs, private files and separate positive timing values.\n')
    print(f'AGV settings saved; private runtime profile: {profile}')


if __name__ == '__main__':
    main()
