"""Read authenticated ESP32 diagnostics without claiming or renewing control."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward the device credential to another URL.


def read_diagnostics(base_url, key, timeout=2):
    address = urlsplit(base_url)
    if (address.scheme not in ('http', 'https') or not address.hostname or address.username
            or address.password or address.query or address.fragment or address.path not in ('', '/')):
        raise ValueError('Provide the ESP32 base URL without credentials or path.')
    request = Request(base_url.rstrip('/') + '/diagnostics', method='GET', headers={
        'Authorization': 'Bearer ' + key, 'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get('events'), list):
        raise ValueError('Unexpected diagnostics response')
    # Keep only documented fields: do not persist arbitrary device payloads.
    result = {k: data[k] for k in ('boot_id', 'boot_number', 'reset_reason', 'uptime_ms',
                                   'wifi_status', 'last_rssi_dbm') if k in data}
    result['events'] = [{k: event[k] for k in ('sequence', 'boot_number', 'uptime_ms', 'kind',
                                              'reason', 'rssi_dbm') if k in event}
                        for event in data['events'] if isinstance(event, dict)]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--key-file', type=Path,
                        default=Path(__file__).parent / 'AgvRouteTest/agv_secrets.h')
    parser.add_argument('--samples', type=int, default=60)
    parser.add_argument('--interval', type=float, default=1)
    parser.add_argument('--timeout', type=float, default=2)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.samples < 1 or any(not math.isfinite(x) or x <= 0 for x in (args.interval, args.timeout)):
        parser.error('Positive finite sample counts and timings are required.')
    key = os.environ.get('AGV_DEVICE_KEY')
    if not key:
        match = re.search(r'^\s*#define\s+AGV_DEVICE_KEY\s+"([A-Za-z0-9_-]{32,96})"',
                          args.key_file.read_text(encoding='utf-8-sig'), re.MULTILINE)
        if not match:
            parser.exit(1, 'Configure the private device key; do not put it on the command line.\n')
        key = match[1]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = args.output or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MOBOT/diagnostics' / ('wifi-' + stamp + '.jsonl')
    output.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with output.open('x', encoding='utf-8') as log:
        for number in range(args.samples):
            started = time.monotonic()
            row = {'utc': datetime.now(timezone.utc).isoformat(), 'sample': number + 1}
            try:
                row['diagnostics'] = read_diagnostics(args.url, key, args.timeout)
                row['ok'] = True
            except Exception as error:
                failures += 1
                row.update(ok=False, error_type=type(error).__name__)
                if isinstance(error, HTTPError): row['http_status'] = error.code
            row['duration_ms'] = round((time.monotonic() - started) * 1000, 2)
            log.write(json.dumps(row, ensure_ascii=False) + '\n'); log.flush()
            if number + 1 < args.samples:
                time.sleep(max(0, args.interval - (time.monotonic() - started)))
    print(f'Diagnostics only: {args.samples} samples, {failures} errors. Log: {output}')


if __name__ == '__main__':
    main()
