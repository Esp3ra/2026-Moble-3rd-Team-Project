# CAM1 QR ROI

메인 PC의 PcVision이 CAM1을 열고 ROI 원본에서 QR을 판독합니다. 원본 판독에 실패하면 같은 영역에 CLAHE 명암 보정을 적용합니다. 기본 카메라 요청은 FHD·MJPEG·30fps입니다. WPF는 수신 영상의 실제 표시 FPS를 보여줍니다.

WPF의 4분할 관제에서 **CAM1 QR 판독 ROI**를 확인합니다. **전체 영상·ROI 열기**를 누르면 같은 CAM1 프레임의 원본과 확대 영상, 서버의 QR 판독 결과가 나란히 표시됩니다. WPF는 영상을 시계 방향으로 90° 회전해 표시하고 선택 영역은 원본 프레임 좌표로 변환합니다. CAM3에는 조립 위치에 정차한 팔레트의 YOLO 검사에 사용할 영상을 배치합니다.

- 배출 완료 후 IR1까지 이송·정차한 다음, 새 프레임과 현재 회차를 확인하고 QR을 읽습니다. 이번 회차의 주문·팔레트 배정을 DB에 저장한 뒤 조립을 요청합니다. 조립·대기 자세 확인과 정차 중 검사에서 합격을 저장한 뒤에만 IR2 이송을 요청합니다.
- ROI는 운영 DB와 같은 폴더의 qr-roi.json에 저장합니다. 수정 시 리비전을 비교하며 회차 진행 중에는 수정을 보류합니다.
- 관리자 화면은 내부망 관리자 세션으로 표준 `GET /api/v1/cameras/CAM1/frame`에서 받은 JPEG를 확대합니다. 서버에서 ROI 확장을 제공하면 `GET /api/v1/qr-roi/status`로 판독 정보를 조회하고, 최상위 관리자 화면에서 `PUT /api/v1/qr-roi/roi`로 판독 영역을 저장할 수 있습니다. 확장이 없는 서버에서는 화면에서 드래그한 영역을 해당 창에서만 확대합니다.

이전 ROI를 가져올 때는 공정 정지 상태에서 이 폴더에서 실행합니다. 기존 qr-roi.json이 있으면 해당 ROI가 우선합니다.

~~~powershell
$mobotState = "$env:LOCALAPPDATA\MOBOT\instance"
& "$env:LOCALAPPDATA\MOBOT\.venv\Scripts\python.exe" -m scripts.configure_qr_roi --db "$mobotState\mobot.sqlite3" --source C:\path\to\roi_config.json
~~~
