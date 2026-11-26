"""Import confirmed standalone ROI and FHD profile without touching order data or keys."""
import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from factory_v1.qr_roi import validate_roi


def configure(database,source):
    database=Path(database).resolve();source=Path(source).resolve()
    if not database.is_file():raise ValueError('설정할 DB가 없습니다.')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('SELECT count(*) FROM V1_CYCLES WHERE closed=0').fetchone()[0]:raise ValueError('실행 중인 회차를 먼저 종료하세요. 카메라 설정을 변경하지 않았습니다.')
    roi=validate_roi(json.loads(source.read_text(encoding='utf-8-sig'))['roi'])
    folder=database.parent;settings_path=folder/'factory-v1.json';roi_path=folder/'qr-roi.json'
    settings=json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    backup=folder/'qr-integration-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f');backup.mkdir(parents=True)
    if settings_path.exists():shutil.copy2(settings_path,backup/settings_path.name)
    profile=settings.setdefault('camera_profiles',{}).setdefault('cam1',{})
    profile.update(width=1920,height=1080,fps=30,fourcc='MJPG')
    settings_path.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not roi_path.exists():roi_path.write_text(json.dumps(dict(roi=roi,revision=0),indent=2)+'\n',encoding='utf-8')
    print(f'QR ROI + FHD/30fps configured: {folder}. Device keys, camera index and DB records unchanged.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,required=True);parser.add_argument('--source',type=Path,required=True);args=parser.parse_args()
    configure(args.db,args.source)


if __name__=='__main__':main()
