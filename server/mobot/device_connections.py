"""Launch configured SSH transports. Remote device services manage their own lifetime."""
import re
import socket
import subprocess
from contextlib import contextmanager
from urllib.parse import urlsplit


@contextmanager
def start_transports(settings):
    children = []
    try:
        for device, config in settings.items():
            alias = config.get("ssh_tunnel_host")
            if not alias:
                continue
            url = urlsplit(config.get("base_url", ""))
            if (not isinstance(alias, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", alias)
                    or url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port):
                raise ValueError(f"{device}: SSH 호스트 별칭과 로컬 장비 포트를 확인하세요.")
            try:
                with socket.create_connection(("127.0.0.1", url.port), timeout=.25):
                    print(f"{device}: 기존 로컬 연결 사용", flush=True)
                    continue
            except OSError:
                pass
            try:
                process = subprocess.Popen([
                    "ssh", "-NT", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                    "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=5",
                    "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", alias,
                ])
            except OSError:
                print(f"{device}: SSH 프로그램을 실행하지 못했습니다.", flush=True)
            else:
                children.append(process)
        yield
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
