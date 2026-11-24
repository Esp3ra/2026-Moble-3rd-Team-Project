"""Package only live LINE sources and example settings, never operational secrets."""
import argparse
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build(target):
    files=['auth.py','orders.py']
    files+=['factory_v1/'+p for p in ('__init__.py','common.py','contracts.py','devices.py','api.py','account_policy.py')]
    files+=['raspi_line/'+p for p in ('__init__.py','__main__.py','adapter.py','requirements.txt','config.example.json','README.md','mobot-line.service.example')]
    files+=['firmware/MobotLineV1/'+p for p in ('MobotLineV1.ino','hardware_profile.h')]
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in files:archive.write(ROOT/file,file)
    print(f'Created {target} ({target.stat().st_size} bytes; {len(files)} source/example files).')
    return target


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'releases/mobot-line-v1.zip')
    build(parser.parse_args().output)


if __name__=='__main__':main()
