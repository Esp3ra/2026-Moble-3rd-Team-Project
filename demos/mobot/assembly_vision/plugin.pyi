"""메인 PC의 PcVision이 호출하는 플러그인의 키워드 인자 명세.

구현 모듈 assembly_vision.calibration이 공개할 두 함수의 반환 형식은
main/docs/통신규약/openapi.yaml을 따른다.
"""

from typing import Literal

from numpy.typing import NDArray


def locate_assembly(
    *,
    cycle_id: str,
    spec: dict[str, str],
    calibration_id: str,
    camera_id: Literal["cam2"],
    frame: NDArray,
    frame_id: str,
    captured_at: str,
) -> dict[str, object]: ...


def inspect_product(
    *,
    cycle_id: str,
    inspection_id: str,
    camera_id: Literal["cam3"],
    frame: NDArray,
    frame_id: str,
    captured_at: str,
) -> dict[str, object]: ...
