"""Package only live LINE sources and example settings, never operational secrets."""
import argparse
import zipfile
import sys
from pathlib import Path

SERVER=Path(__file__).resolve().parents[1]
REPO=SERVER.parents[1]
sys.path.insert(0,str(SERVER))
from server_paths import instance_directory


def build(target):
    server_files=['auth.py','orders.py']
    server_files+=['factory_v1/'+p for p in ('__init__.py','common.py','contracts.py','devices.py','api.py','account_policy.py')]
    adapter_files=['devices/line_pi/'+p for p in ('__init__.py','__main__.py','adapter.py','serial_link.py','requirements.txt','config.example.json','README.md','install-service.sh')]
    firmware_files=['firmware/RobotWorkSimulation/'+p for p in ('RobotWorkSimulation.ino','hardware_profile.h')]
    files=[(SERVER/file,file) for file in server_files]+[(REPO/file,file) for file in adapter_files+firmware_files]
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('devices/', b'')  # Namespace package when importing directly from the ZIP.
        for source,member in files:archive.write(source,member)
    print(f'Created {target} ({target.stat().st_size} bytes; {len(files)} source/example files).')
    return target


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=instance_directory().parent/'releases/mobot-line-v1.zip')
    build(parser.parse_args().output)


if __name__=='__main__':main()
