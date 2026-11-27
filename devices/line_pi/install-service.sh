#!/usr/bin/env bash
# Run once as the deployment user, with the line stopped.
set -euo pipefail

if (( EUID == 0 )); then
    echo 'Run as the Pi account that owns the LINE installation.' >&2
    exit 1
fi

line_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
cd -- "$line_dir"
.venv/bin/python -c 'import json; from pathlib import Path; from devices.line_pi.__main__ import validate_config; validate_config(json.loads(Path("devices/line_pi/config.json").read_text(encoding="utf-8-sig")))'

# WorkingDirectory is a raw path, while ExecStart tokenizes its arguments.
if [[ "$line_dir" == *$'\n'* || "$line_dir" == *$'\r'* || "$line_dir" == *[[:blank:]] ]]; then
    echo 'The LINE installation path cannot contain line breaks or end in whitespace.' >&2
    exit 1
fi
working_dir=${line_dir//%/%%}
exec_line_dir=${working_dir//\\/\\\\}
exec_line_dir=${exec_line_dir//\"/\\\"}
unit=$(mktemp)
trap 'rm -f -- "$unit"' EXIT
cat > "$unit" <<EOF
[Unit]
Description=MOBOT live LINE v1 bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=$(id -un)
SupplementaryGroups=dialout
WorkingDirectory=$working_dir
ExecStart=":$exec_line_dir/.venv/bin/python" -m devices.line_pi --config devices/line_pi/config.json
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$unit" /etc/systemd/system/mobot-line.service
sudo systemctl daemon-reload
sudo systemctl enable --now mobot-line.service
sudo systemctl is-enabled --quiet mobot-line.service
sudo systemctl is-active --quiet mobot-line.service
echo 'mobot-line.service is active and enabled at boot.'
