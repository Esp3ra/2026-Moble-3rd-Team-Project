# CAM1 QR ROI 통합

메인 Flask(8000)의 `PcVision`이 CAM1을 한 번만 열고 `factory_v1/qr_roi.py`가 ROI 원본 판독 → 실패 시 CLAHE 명암 보정으로 판독한다. 카메라 기본 설정은 FHD 1920×1080, MJPEG, 30fps 요청이다. 실제 수신·QR 판독 FPS를 표시한다. 영상 획득, 화면 JPEG, QR 판독은 분리돼 있다.

WPF의 **QR ROI** 탭 → **QR ROI 화면 열기**, 또는 **무선 시험** 탭의 **실시간 QR ROI**를 누른다. 별도의 WPF 창에서 전체 영상 / 원본 ROI 영상 / QR 정보를 확인한다. 전체 영상에서 드래그하면 ROI를 저장한다. 좌표는 레터박스 여백을 제외하고 영상에 대한 0~1 비율로 변환한다. 실행 중인 회차에서는 변경을 거절한다.

WPF 카메라 탭은 **CAM 01 = 전체 영상, CAM 03 자리 = CAM 01의 ROI 영상**으로 표시한다. CAM 03의 실제 검사 카메라와 서버 검사 경로는 변경하지 않는다. WPF의 모든 실시간 영상은 시계방향 90° 회전해 표시한다. 전용 ROI 창에서도 같은 회전을 적용하고, 선택 좌표는 원본 좌표 `(x,y)=(표시 y,1-표시 x)`로 되돌려 저장한다. 서버의 판독 프레임·ROI·DB 기록은 원본 방향을 유지하므로 Pi 재배포나 서버 재시작은 필요하지 않다. WPF를 재실행하면 표시 변경이 적용된다.

서버와 WPF를 다시 시작해야 적용된다. 독립 `QR_ROI_Web` 앱은 종료한다. 통합된 WPF는 5001 서버를 사용하지 않으며 카메라 점유 충돌을 피하기 위해 별도 앱을 함께 실행하지 않는다. Pi 코드와 UNO 펌웨어·ngrok 폴링 및 1초 액추에이터 제어는 변경하지 않았다.

## 데이터와 공정

- 실시간 판독은 상태 조회만 제공한다. 주문 배정이나 모터 명령을 생성하지 않는다.
- 공정의 `read_feed_qr`는 같은 ROI/CLAHE 판독기로 현재 프레임을 읽고 기존 `FEED_QR_READ` DTO를 만든다. 배출 후 회차 검사·QR 재시도·DB 배정 저장·이송 명령 순서는 유지한다. 화면에 남은 과거 결과로 공정을 진행하지 않는다.
- ROI는 실행 DB 폴더의 `qr-roi.json`에 저장한다. 변경 시 리비전을 비교하고, 변경 이전 영역에서 처리 중이던 결과는 폐기한다. 촬영 끊김·판독 결과 만료 시 QR 표시를 지운다. 원본 영역 밖의 QR은 읽지 않는다.
- 기존 `scan_test_qr`와 `/api/v1/wireless-test/qr-read`는 구형 클라이언트 호환용 전달부만 남겼고 별도 판독 알고리즘은 제거했다. 구형 메서드 원문은 `legacy/qr_before_roi/old_qr_methods.txt`에 보관하며 실행 경로에서는 가져오지 않는다. `pallet_system_uno_sim`의 별도 레거시 시험 앱은 이번 통합에서 실행하지 않는다.

## 관리자 전용 확장 API

기존 내부망·관리자 세션 인증을 사용한다. 외부 ngrok으로 WPF용 API를 공개하지 않는다.

- `GET /api/v1/qr-roi/status`: 판독 상태, 원문, 팔레트 ID, 원본/CLAHE 방식, ROI·리비전, 촬영 ID·UTC 시각, 해상도·FPS.
- `GET /api/v1/qr-roi/frame`: 원본 ROI JPEG. `X-Captured-At`, `X-Frame-Id`, `Cache-Control: no-store`.
- `PUT /api/v1/qr-roi/roi`: `{ "roi": [x,y,w,h], "expected_revision": 0 }`. 잘못된 값 400, 리비전 충돌·실행 중 회차 409. DB 주문·팔레트·명령에는 쓰지 않는다.

기존 전체 영상은 `/api/v1/cameras/CAM1/frame`에서 조회한다. 이 확장은 공통 OpenAPI 자체를 변경한 것이 아니다. 2026-11-23 확인한 main 규약 v0.2.0의 새 CAM2 장착 좌표 갱신은 이번 CAM1 통합과 별도이며, 현재 코드가 v0.2.0 전체를 구현했다고 보고하지 않는다.

## 독립 앱의 ROI 가져오기

처음 한 번, 회차가 없는 상태에서 다음과 같이 가져올 수 있다. 이미 통합 서버의 `qr-roi.json`이 있으면 그 설정을 유지한다. 장비 키·카메라 번호·주문 DB를 바꾸지 않고 CAM1 프로필과 ROI 파일만 준비한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.configure_qr_roi --db instance/transport-test/mobot.sqlite3 --source QR_ROI_Web/roi_config.json
```

`Start-MOBOT-Ngrok-Test.cmd`는 기존처럼 사용한다. QR 통합 자체는 Pi·UNO 변경을 요구하지 않는다. 시험 시간을 1초로 바꾼 경우에는 서버·Pi의 최신 시험 코드를 사용한다.
