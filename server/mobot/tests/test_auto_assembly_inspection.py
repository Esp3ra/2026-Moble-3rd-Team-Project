import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import cv2
import numpy as np

from assembly_vision.inspection import AutomaticInspector, Box, Detection
from factory_v1 import contracts
from factory_v1.common import now
from factory_v1.vision import PcVision


PARTS = (
    Detection("H1", .9, Box(164, 61, 236, 130)),
    Detection("L1", .9, Box(245, 133, 303, 218)),
    Detection("R3", .9, Box(97, 133, 155, 218)),
)
SPEC = dict(target_head_id="H1", target_arm_l_id="L1", target_arm_r_id="R3")


def draw_scene(parts=PARTS):
    frame = np.full((360, 400, 3), 52, dtype=np.uint8)
    cv2.rectangle(frame, (155, 129), (245, 246), (220, 220, 220), -1)
    cv2.rectangle(frame, (159, 246), (179, 286), (225, 225, 225), -1)
    cv2.rectangle(frame, (214, 246), (236, 292), (235, 235, 235), -1)
    for item in parts:
        item.box.crop(frame)[:] = (90, 225, 120)
    return frame


class InspectorTests(unittest.TestCase):
    def check(self, inspector, frame):
        result = inspector.inspect_product(
            cycle_id=str(uuid4()), inspection_id=str(uuid4()), frame=frame,
            frame_id="frame", captured_at=now(), qr_read="PALLET-01",
        )
        contracts.vision(result)
        return result

    def test_regions_follow_the_torso_and_wrong_class_remains_in_its_observed_slot(self):
        detector = Mock(version="test")
        detector.detect.return_value = PARTS
        reference = draw_scene()
        inspector = AutomaticInspector(detector, reference)
        frame = cv2.warpAffine(reference, np.float32([[1, 0, 27], [0, 1, 5]]), (400, 360))
        detector.detect.return_value = [
            Detection(("R1" if item.code == "L1" else item.code), item.score, item.box.move(27, 5))
            for item in PARTS
        ]
        value = self.check(inspector, frame)
        self.assertEqual(value["left"]["observed_id"], "R1")
        self.assertEqual(contracts.inspect_result(value, SPEC, "PALLET-01", 3)[0], "MISMATCH")

    def test_missing_requires_matching_empty_appearance(self):
        detector = Mock(version="test")
        detector.detect.side_effect = [PARTS, (), PARTS[:1] + PARTS[2:], PARTS[:1] + PARTS[2:]]
        reference, empty = draw_scene(), draw_scene(())
        inspector = AutomaticInspector(detector, reference, empty)
        missing = self.check(inspector, draw_scene(PARTS[:1] + PARTS[2:]))
        self.assertEqual(missing["left"]["observation"], "MISSING")
        obscured = reference.copy()
        PARTS[1].box.crop(obscured)[:] = (170, 170, 170)
        self.assertEqual(self.check(inspector, obscured)["left"]["observation"], "UNKNOWN")
        unaligned = self.check(inspector, np.zeros_like(reference))
        self.assertTrue(all(unaligned[slot]["observation"] == "UNKNOWN" for slot in ("head", "left", "right")))

    def test_empty_detection_without_reference_is_held(self):
        detector = Mock(version="test")
        detector.detect.side_effect = [PARTS, PARTS[:1] + PARTS[2:]]
        inspector = AutomaticInspector(detector, draw_scene())
        result = self.check(inspector, draw_scene(PARTS[:1] + PARTS[2:]))
        self.assertEqual(contracts.inspect_result(result, SPEC, "PALLET-01", 3)[0], "INDETERMINATE")

    def test_close_score_peaks_belong_to_the_same_torso_position(self):
        detector = Mock(version="test")
        detector.detect.return_value = PARTS
        frame = draw_scene()
        inspector = AutomaticInspector(detector, frame)
        search_radius = round(max(frame.shape[:2]) * .09)
        scores = np.zeros((2 * search_radius + 1, 2 * search_radius + 1), dtype=np.float32)
        scores[search_radius, search_radius] = 1.0
        scores[search_radius, search_radius + 2] = 0.995
        scores[search_radius, search_radius + 20] = 0.90
        with patch("assembly_vision.inspection.cv2.matchTemplate", return_value=scores):
            result = self.check(inspector, frame)
        self.assertEqual(contracts.inspect_result(result, SPEC, "PALLET-01", 3)[0], "PASS")

    def test_cam1_uses_raw_frame_for_both_qr_and_automatic_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            app = SimpleNamespace(logger=Mock(), config={
                "ORDER_DB": str(Path(folder) / "orders.sqlite3"),
                "V1_SETTINGS": dict(
                    inspection_camera="cam1", auto_inspection=True, vision_profile_approved=True, inspection_rotation=90,
                ),
            })
            detector = Mock(version="test")
            detector.detect.return_value = PARTS
            upright = draw_scene()
            frame = cv2.rotate(upright, cv2.ROTATE_90_COUNTERCLOCKWISE)
            inspector = AutomaticInspector(detector, frame, rotation=90)
            with patch.object(AutomaticInspector, "from_directory", return_value=inspector) as load:
                vision = PcVision(app)
            load.assert_called_once_with(Path(folder) / "assembly-vision", rotation=90)
            sample = (frame, "cam1-frame", now())
            vision.frames["cam1"] = (time.monotonic(), *sample)
            self.assertTrue(vision.inspection_ready())
            with patch.object(vision.qr, "read_now", return_value={"pallet_id": "PALLET-01"}) as read:
                result = vision.inspect_product(str(uuid4()), str(uuid4()), SPEC, "PALLET-01", sample)
            self.assertIs(read.call_args.args[0][0], frame)
            np.testing.assert_array_equal(detector.detect.call_args.args[0], upright)
            self.assertEqual((result["frame_id"], result["captured_at"]), sample[1:])
            self.assertEqual(contracts.inspect_result(result, SPEC, "PALLET-01", 3)[0], "PASS")
            self.assertTrue(vision.inspection_image(result["image_ref"]))
