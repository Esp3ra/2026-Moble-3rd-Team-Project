"""Opt-in LINE/AGV hardware trial; only ARM and assembly vision are simulated."""
import os
from .common import *
from .vision import PcVision

PROFILE = 'LINE_AGV_MIXED_V1'


def enabled(settings, mode, path, production_path):
    configured = settings.get('mixed_trial', {}).get('profile') == PROFILE
    requested = os.environ.get('MOBOT_MIXED_TRIAL') == '1'
    require(configured == requested, '혼합시험 설정과 MOBOT_MIXED_TRIAL=1이 모두 필요합니다.',409,'CONFLICT')
    if configured:
        require(not settings['mixed_trial'].get('retired'),'보존된 이전 시험입니다. 새 시험 프로필을 사용하세요.',409,'CONFLICT')
        require(mode == 'HARDWARE' and os.environ.get('MOBOT_DB') and path.resolve() != production_path.resolve(),
                '혼합시험은 명시한 별도 DB와 HARDWARE 연결을 사용합니다.',409,'CONFLICT')
        require(set(settings.get('devices', {})) == {'LINE','AGV'},
                '혼합시험에는 실물 LINE·AGV만 설정하세요. ARM은 대역입니다.',409,'CONFLICT')
    return configured


class MixedTrialVision:
    """Keep real CAM1 QR binding; label synthetic assembly/inspection evidence."""
    def __init__(self, app):
        self.real = PcVision(app, False)
        self.simulated = PcVision(app, True)
        self.qr = self.real.qr
        self.inspection_camera = 'cam3'
        self.part_order = ['HEAD','LEFT','RIGHT']

    def start(self): self.real.start()
    def close(self): self.real.close(); self.simulated.close()
    def ready(self): return self.real.ready()
    def assembly_ready(self): return True
    def inspection_ready(self): return True
    def _source(self, camera): return self.simulated if camera in ('cam2','cam3') else self.real
    def frame_data(self, camera): return self._source(camera).frame_data(camera)
    def frame(self, camera): return self._source(camera).frame(camera)
    def jpeg(self, camera): return self._source(camera).jpeg(camera)
    def process_frame(self, camera, after): return self._source(camera).process_frame(camera, after)
    def read_feed_qr(self, *args): return self.real.read_feed_qr(*args)
    def locate_assembly(self, *args): return self.simulated.locate_assembly(*args)
    def validate_poses(self, poses): return self.simulated.validate_poses(poses)
    def inspect_product(self, *args):
        result = self.simulated.inspect_product(*args)
        if result:
            result['model_version'] = 'MIXED_TRIAL_SIMULATED'
            result['calibration_id'] = 'MIXED_TRIAL_SIMULATED'
        return result
    def inspection_image(self, image): return self.simulated.inspection_image(image)
    def release_image(self, image): return self.simulated.release_image(image)
