"""Automatic assembly slot calibration and ONNX observations for a fixed camera."""

import ast
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

import cv2
import numpy as np


PART_CODES = {
    "lime_dual_camera_head": "H1",
    "yellow_infrared_monoeye_head": "H2",
    "left_blue_gripper_arm": "L1",
    "right_blue_gripper_arm": "R1",
    "left_orange_welding_arm": "L2",
    "right_orange_welding_arm": "R2",
    "left_red_fastening_arm": "L3",
    "right_red_fastening_arm": "R3",
}
SLOT_NAMES = {"head": "H", "left": "L", "right": "R"}
INPUT_SIZE = 640
CANDIDATE_THRESHOLD = 0.25
SEEN_THRESHOLD = 0.60
ANCHOR_THRESHOLD = 0.84
ANCHOR_SCORE_MARGIN = 0.025
EMPTY_DISTANCE_LIMIT = 12.0
ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self):
        return np.array([(self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2])

    def contains(self, center):
        return self.x1 <= center[0] < self.x2 and self.y1 <= center[1] < self.y2

    def clip(self, width, height):
        return Box(max(0, self.x1), max(0, self.y1), min(width, self.x2), min(height, self.y2))

    def move(self, dx, dy):
        return Box(self.x1 + dx, self.y1 + dy, self.x2 + dx, self.y2 + dy)

    def crop(self, frame):
        return frame[self.y1:self.y2, self.x1:self.x2]


@dataclass(frozen=True)
class Detection:
    code: str
    score: float
    box: Box


class OnnxDetector:
    def __init__(self, path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        model = path.read_bytes()
        self.version = "sha256:" + sha256(model).hexdigest()
        self.session = ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (len(inputs) != 1 or inputs[0].shape != [1, 3, INPUT_SIZE, INPUT_SIZE]
                or len(outputs) != 1 or outputs[0].shape[:2] != [1, 4 + len(PART_CODES)]
                or inputs[0].type != "tensor(float)" or outputs[0].type != "tensor(float)"):
            raise ValueError("640×640 FP32 입력과 부품 8개 클래스를 사용하는 best.onnx가 필요합니다.")
        labels = ast.literal_eval(self.session.get_modelmeta().custom_metadata_map.get("names", "{}"))
        if (not isinstance(labels, dict) or set(labels) != set(range(len(PART_CODES)))
                or set(labels.values()) != set(PART_CODES)):
            raise ValueError("best.onnx의 클래스 이름과 순서를 확인하세요.")
        self.codes = [PART_CODES[labels[index]] for index in range(len(PART_CODES))]
        self.input_name = inputs[0].name

    def detect(self, frame):
        height, width = frame.shape[:2]
        scale = INPUT_SIZE / max(width, height)
        resized = cv2.resize(frame, (round(width * scale), round(height * scale)))
        resized_height, resized_width = resized.shape[:2]
        x, y = (INPUT_SIZE - resized_width) // 2, (INPUT_SIZE - resized_height) // 2
        letterbox = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.uint8)
        letterbox[y:y + resized_height, x:x + resized_width] = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        tensor = np.ascontiguousarray(letterbox.transpose(2, 0, 1)[None], dtype=np.float32) / 255
        rows = self.session.run(None, {self.input_name: tensor})[0][0].T
        if not np.isfinite(rows).all():
            raise ValueError("ONNX 검출 결과에 유효하지 않은 값이 있습니다.")
        labels = rows[:, 4:].argmax(axis=1)
        scores = rows[np.arange(len(rows)), labels + 4]
        accepted = (scores >= CANDIDATE_THRESHOLD) & (rows[:, 2:4] > 0).all(axis=1)
        labels, scores, boxes = labels[accepted], scores[accepted], rows[accepted, :4].copy()
        boxes[:, :2] -= boxes[:, 2:] / 2
        boxes[:, :2] -= [x, y]
        boxes /= scale
        indexes = cv2.dnn.NMSBoxesBatched(
            boxes.tolist(), scores.tolist(), labels.tolist(), CANDIDATE_THRESHOLD, 0.45
        ) if len(boxes) else []
        detections = []
        for index in np.asarray(indexes, dtype=int).reshape(-1):
            bx, by, bw, bh = boxes[index]
            box = Box(round(bx), round(by), round(bx + bw), round(by + bh)).clip(width, height)
            if box.x1 < box.x2 and box.y1 < box.y2:
                detections.append(Detection(self.codes[labels[index]], min(1.0, float(scores[index])), box))
        return detections


def read_frame(path):
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"검사 기준 사진을 열 수 없습니다. {path.name}")
    return image


def signature(frame, box):
    return cv2.resize(box.crop(frame), (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)


def distance(first, second):
    return float(np.abs(first - second).mean())


def observation(state, detection=None):
    return {
        "observation": state,
        "observed_id": detection.code if detection else None,
        "confidence": round(detection.score, 4) if detection else None,
    }


class AutomaticInspector:
    @classmethod
    def from_directory(cls, folder, rotation=0):
        empty_path = folder / "empty.jpg"
        return cls(OnnxDetector(folder / "best.onnx"), read_frame(folder / "reference.jpg"),
                   read_frame(empty_path) if empty_path.is_file() else None, rotation)

    def __init__(self, detector, reference, empty=None, rotation=0):
        if type(rotation) is not int or rotation not in ROTATIONS:
            raise ValueError("검사 영상의 시계 방향 회전은 0, 90, 180, 270으로 지정하세요.")
        self.rotation = ROTATIONS[rotation]
        self.source_shape = reference.shape
        reference = self._orient(reference)
        empty = self._orient(empty) if empty is not None else None
        self.detector = detector
        self.shape = reference.shape
        height, width = self.shape[:2]
        detections = [hit for hit in self.detector.detect(reference) if hit.score >= SEEN_THRESHOLD]
        parts = {}
        self.regions = {}
        for slot, prefix in SLOT_NAMES.items():
            matches = [hit.box for hit in detections if hit.code.startswith(prefix)]
            if len(matches) != 1:
                raise ValueError(f"정상 기준 사진에서 {slot} 부품 1개를 확인할 수 있어야 합니다.")
            part = parts[slot] = matches[0]
            pad_x, pad_y = round((part.x2 - part.x1) * .12), round((part.y2 - part.y1) * .12)
            self.regions[slot] = Box(
                part.x1 - pad_x, part.y1 - pad_y, part.x2 + pad_x, part.y2 + pad_y
            ).clip(width, height)
        if any(sum(box.contains(part.center) for box in self.regions.values()) != 1 for part in parts.values()):
            raise ValueError("정상 기준 사진에서 세 부위의 위치가 겹칩니다.")

        self.anchor = self._body_anchor(parts).clip(width, height)
        self.template = cv2.cvtColor(self.anchor.crop(reference), cv2.COLOR_BGR2GRAY)
        if min(self.template.shape) < 24 or self.template.std() < 8:
            raise ValueError("정상 기준 사진에 몸통 아래쪽과 다리가 보여야 합니다.")
        self.present = {slot: signature(reference, box) for slot, box in self.regions.items()}
        self.empty = {}
        if empty is not None:
            empty_regions = self._registered_regions(empty)
            if empty_regions is None:
                raise ValueError("빈 몸통은 정상 기준 사진과 같은 카메라에서 촬영해야 합니다.")
            empty_detections = self.detector.detect(empty)
            for slot, box in empty_regions.items():
                if box != box.clip(width, height):
                    continue
                patch = signature(empty, box)
                if (not any(box.contains(hit.box.center) for hit in empty_detections)
                        and distance(patch, self.present[slot]) > 2 * EMPTY_DISTANCE_LIMIT):
                    self.empty[slot] = patch
        fingerprint = sha256(repr(self.shape).encode() + reference.tobytes())
        if empty is not None:
            fingerprint.update(empty.tobytes())
        self.calibration_id = "auto-roi:" + fingerprint.hexdigest()

    def _orient(self, frame):
        return frame if self.rotation is None else cv2.rotate(frame, self.rotation)

    def accepts_frame(self, frame):
        # Equal source dimensions imply equal dimensions after the same rotation.
        return frame is not None and frame.shape == self.source_shape and frame.dtype == np.uint8

    @staticmethod
    def _body_anchor(parts):
        left, right = parts["left"].center, parts["right"].center
        pivot = (left + right) / 2
        down = pivot - parts["head"].center
        arm_span = np.linalg.norm(left - right)
        length = np.linalg.norm(down)
        if min(length, arm_span) < 30 or abs((left - right) @ down) > .6 * arm_span * length:
            raise ValueError("정상 기준 사진에서 머리와 양팔의 배치를 확인하세요.")
        down /= length
        across = np.array([-down[1], down[0]])
        arm_corners = [
            (x, y) for arm in (parts["left"], parts["right"])
            for x in (arm.x1, arm.x2) for y in (arm.y1, arm.y2)
        ]
        start = max((np.asarray(point) - pivot) @ down for point in arm_corners) + .02 * arm_span
        corners = np.array([
            pivot + advance * down + span * across
            for advance in (start, start + .62 * arm_span) for span in (-.35 * arm_span, .35 * arm_span)
        ])
        return Box(*np.rint([*corners.min(axis=0), *corners.max(axis=0)]).astype(int))

    def _registered_regions(self, frame):
        if frame.shape != self.shape or frame.dtype != np.uint8:
            return None
        height, width = frame.shape[:2]
        radius = round(max(height, width) * .09)
        anchor = self.anchor
        search = Box(anchor.x1 - radius, anchor.y1 - radius, anchor.x2 + radius, anchor.y2 + radius).clip(width, height)
        gray = cv2.cvtColor(search.crop(frame), cv2.COLOR_BGR2GRAY)
        scores = cv2.matchTemplate(gray, self.template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if not np.isfinite(score) or score < ANCHOR_THRESHOLD:
            return None
        local_maxima = cv2.compare(scores, cv2.dilate(scores, np.ones((3, 3), np.uint8)), cv2.CMP_EQ)
        nearby = max(2, round(min(self.template.shape) * .05))
        # Neighboring peaks describe the same position within the template's pixel tolerance.
        local_maxima[max(0, y - nearby):y + nearby + 1, max(0, x - nearby):x + nearby + 1] = 0
        _, competing_score, _, _ = cv2.minMaxLoc(scores, mask=local_maxima)
        if competing_score >= score - ANCHOR_SCORE_MARGIN:
            return None
        dx, dy = search.x1 + x - anchor.x1, search.y1 + y - anchor.y1
        return {slot: box.move(dx, dy) for slot, box in self.regions.items()}

    def inspect_product(self, *, cycle_id, inspection_id, frame, frame_id, captured_at, qr_read):
        if self.accepts_frame(frame):
            frame = self._orient(frame)
            aligned = self._registered_regions(frame)
        else:
            aligned = None
        observations = {slot: observation("UNKNOWN") for slot in SLOT_NAMES}
        if aligned is not None:
            height, width = frame.shape[:2]
            matches = {slot: [] for slot in SLOT_NAMES}
            ambiguous = set()
            for hit in self.detector.detect(frame):
                slots = [slot for slot, box in aligned.items() if box.contains(hit.box.center)]
                if len(slots) == 1:
                    matches[slots[0]].append(hit)
                else:
                    ambiguous.update(slots)
            for slot, hits in matches.items():
                if slot in ambiguous:
                    continue
                box = aligned[slot]
                strong = [hit for hit in hits if hit.score >= SEEN_THRESHOLD]
                if len(strong) == 1:
                    observations[slot] = observation("SEEN", strong[0])
                elif not hits and slot in self.empty and box == box.clip(width, height):
                    patch = signature(frame, box)
                    delta = distance(patch, self.empty[slot])
                    if delta <= EMPTY_DISTANCE_LIMIT and 2 * delta < distance(patch, self.present[slot]):
                        observations[slot] = observation("MISSING")
        return {
            "cycle_id": cycle_id, "inspection_id": inspection_id, "frame_id": frame_id,
            "captured_at": captured_at, "image_ref": uuid4().hex, "qr_read": qr_read,
            "model_version": self.detector.version, "calibration_id": self.calibration_id,
            **observations,
        }
