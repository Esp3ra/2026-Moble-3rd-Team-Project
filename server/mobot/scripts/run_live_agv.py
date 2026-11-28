"""Run or check the same-PC adapter; never replay a movement on startup."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.request import Request, urlopen

from configure_live_agv import local_address, positive, settings_path


class AdapterReference:
    """Bind the listener before constructing the journal or claiming control."""
    target = None

    def __getattr__(self, name):
        return getattr(self.target, name)


class StartupBlocked(Exception):
    pass


def check_journal_before_control(journal):
    """A recovery journal must not silently become a second live controller."""
    if not journal.exists():
        return
    with closing(sqlite3.connect(journal.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute("SELECT value FROM agv_meta WHERE key='lane'").fetchone()
        lane = json.loads(row[0]) if row else {}
        pending = db.execute("SELECT result FROM agv_commands").fetchall()
    if (lane.get('recovery') or lane.get('phase', 'READY') not in ('READY', 'CLOSED')
            or lane.get('pause_id') or any(json.loads(r[0])['state'] in ('ACCEPTED', 'RUNNING') for r in pending)):
        raise StartupBlocked('Existing AGV journal requires operator reconciliation. '
                             'Control was not claimed; no position or movement was sent.')


def load_configuration(path):
    settings = json.loads(path.read_text(encoding='utf-8-sig'))
    profile = json.loads(path.with_name('agv-adapter.json').read_text(encoding='utf-8-sig'))
    agv = settings['devices']['AGV']
    url = local_address(agv['base_url'])
    if profile['listen_host'] != '127.0.0.1' or profile['listen_port'] != url.port:
        raise ValueError('Adapter URL and local listener do not match.')
    events = profile['events_url']
    local_address(events.removesuffix('/api/v1/device-events'))
    if not events.endswith('/api/v1/device-events'):
        raise ValueError('Use the device-events POST endpoint on the local business server.')
    for name in ('command_timeout_seconds', 'device_request_timeout_seconds', 'heartbeat_seconds', 'poll_seconds'):
        positive(profile[name])
    controller, event = agv['controller_token'], settings['device_tokens']['AGV']
    if len(controller) < 32 or len(event) < 32 or controller == event:
        raise ValueError('Distinct controller and event credentials are required.')
    return agv, event, profile


def read_device_key(path):
    source = Path(path).read_text(encoding='utf-8-sig')
    match = re.search(r'^\s*#define\s+AGV_DEVICE_KEY\s+"([A-Za-z0-9_-]{32,96})"', source, re.MULTILINE)
    if not match:
        raise ValueError('Private AGV_DEVICE_KEY definition not found.')
    return match[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=settings_path())
    parser.add_argument('--check-only', action='store_true', help='Adapter GET only; does not claim control')
    parser.add_argument('--confirmed-position', type=int, choices=range(1, 8),
                        help='Operator-confirmed physical point; registers position without movement')
    args = parser.parse_args()
    if args.check_only and args.confirmed_position is not None:
        parser.error('A read-only check cannot register a position.')
    adapter = server = None
    try:
        agv, event, profile = load_configuration(args.settings.resolve())
        if args.check_only:
            request = Request(agv['base_url'] + '/device/v1/status',
                              headers={'Authorization': 'Bearer ' + agv['controller_token']})
            with urlopen(request, timeout=positive(agv['request_timeout_seconds'])) as response:
                status = json.load(response)
            if status['device_id'] != 'AGV':
                raise ValueError('Unexpected adapter identity.')
            print(json.dumps({k: status[k] for k in ('device_id', 'online', 'state', 'mode')}, ensure_ascii=False))
            return
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'AGV'))
        from agv_client import AGVClient
        from device_adapter import Adapter, serve
        key = read_device_key(profile['device_key_file'])
        if key in (agv['controller_token'], event):
            raise ValueError('ESP32 key must be distinct from controller and event credentials.')
        journal = Path(profile['journal_path']).resolve()
        journal.parent.mkdir(parents=True, exist_ok=True)
        check_journal_before_control(journal)
        client = AGVClient(profile['device_url'], key, heartbeat_seconds=profile['heartbeat_seconds'],
                           request_timeout=profile['device_request_timeout_seconds'])
        reference = AdapterReference()
        server = serve(reference, agv['controller_token'], (profile['listen_host'], profile['listen_port']))
        adapter = Adapter(client, str(journal), profile['command_timeout_seconds'])
        reference.target = adapter
        if args.confirmed_position is not None:
            raw = client.status()
            if raw['state'] in ('moving', 'reverse_testing', 'paused') or raw['fork_moving'] or raw['control_connected']:
                raise ValueError('Position registration requires an idle, unclaimed device.')
            client.connect()
            client.set_position(args.confirmed_position)  # Once only; never resend on an ambiguous response.
            deadline = time.monotonic() + 3
            while True:
                raw = client.status()
                if raw['position'] == args.confirmed_position and raw['state'] == 'ready':
                    print(f'Operator-confirmed position registered: {args.confirmed_position}', flush=True)
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('Position registration was not observed; reconcile before retrying.')
                time.sleep(.1)
        adapter.start(profile['events_url'], event, profile['poll_seconds'])
        print('AGV adapter listening on the configured loopback port; no movement issued.', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except StartupBlocked as error:
        parser.exit(1, str(error) + '\n')
    except Exception as error:
        # HTTP/OS errors can contain URLs or authentication material. Keep the
        # console safe; report the exception type rather than its raw contents.
        print(f'AGV adapter failed ({type(error).__name__}); check configuration and device state.', file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if server is not None:
            server.server_close()
        if adapter is not None:
            adapter.close()


if __name__ == '__main__':
    main()
