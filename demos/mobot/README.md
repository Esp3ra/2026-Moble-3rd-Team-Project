# MOBOT · 통신 규약 v0.1.0 전환본

Flask·SQLite·구매 웹·WPF는 `/api/v1`을 사용합니다. 장비 연결 모듈과는 `/device/v1` 명령·상태·이벤트로 통신합니다. 이전 `/api/orders`, `/api/equipment`, `/api/devices` 시험 API는 기본적으로 410을 반환합니다. 아래 이전 안내 문서와 기존 Pi 배포 ZIP은 v0.1.0 연결용이 아닙니다.

기준은 GitHub main의 [AGENTS](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/AGENTS.md), [공통 규약](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/통신규약/공통_통신규약과_DTO.md), [OpenAPI](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/통신규약/openapi.yaml), [하드웨어 공정](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/HARDWARE_WORKFLOW.md), [팀 규약](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/프로젝트_전체규약_팀답변_공유본.md)입니다. 이 문서는 구현·실행 안내이며 원격 규약의 복사본이 아닙니다.

실물 Pi·UNO 연결은 [설치 안내](raspi_line/README.md)를 따릅니다. 새 펌웨어는 `firmware/MobotLineV1`이며, 구형 `raspi_agent`의 ngrok 폴링 방식과 구분합니다.

조립·검사를 제외한 실물 운송 시험은 `Start-MOBOT-Transport-Test.cmd`로 실행합니다. [시험 모드 안내](TRANSPORT_TEST.md)를 확인하세요.

사용자 요청에 따른 임시 공개 ngrok 단독 제어 시험은 [설치 안내](wireless_pi/README.md)를 사용합니다. 이 별도 시험이 활성화되면 주문 자동 운전은 차단하며, 기존 규약 기반 장비 API의 외부 접근 정책은 바꾸지 않습니다.

## 실행

내부망 TCP/IP(HTTP) 시험은 `Start-MOBOT-LAN-Test.cmd`와 [LAN 설치 안내](LAN_TEST.md)를 사용합니다. 별도 5000 수신 서버 대신 메인 Flask(8000)와 기존 WPF·QR ROI·Pi/UNO 시험을 함께 사용합니다. Pi에는 `releases/mobot-lan-test.zip`의 코드와 LAN 설정을 넣습니다.

CAM1의 실시간 QR은 [QR ROI 통합 안내](QR_ROI_INTEGRATION.md)를 따릅니다. WPF의 **QR ROI** 탭에서 전체 영상·ROI·정보 창을 엽니다. 메인 서버(8000)가 직접 처리하므로 독립 QR_ROI_Web 서버(5001)는 함께 실행하지 않습니다. 구형 CAM1 판독 알고리즘은 실행 경로에서 제거했습니다.

### Alpha 파일 배치와 로컬 실행본

2026-11-23 확인한 Alpha `35b67c6`은 파일명을 예약한 개발 뼈대이며, 서버·장비 소스 상당수가 빈 파일입니다. 빈 파일로 이 실행본을 덮어쓰지 않습니다. 최신 배치 기준은 GitHub main의 [프로젝트 파일 트리](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/PROJECT_TREE.md)를 확인합니다.

| 이 로컬 실행본 | 팀 저장소 배치 |
| --- | --- |
| 루트 `app.py`, `factory_v1/`, `templates/`, `static/` | `demos/mobot/` 아래의 같은 파일·폴더 |
| `WPF/` | 저장소 루트 `WPF/` |
| `raspi_line/` | 현재 규약 기반 Pi 배포 패키지. 팀의 `devices/line_pi/`와 통합할 때 진입점·시리얼 처리를 대조 |
| `wireless_pi/`, `wireless_control.py` | 기존 ngrok 단독 시험용 확장. 정식 장비 모듈과 구분 |
| `firmware/MobotLineV1/`, `firmware/MobotWirelessBench/` | 규약 기반 펌웨어와 단독 시험 펌웨어. 팀의 `firmware/RobotWorkSimulation/` 예약 경로로 임의 교체하지 않음 |

현재 서버·DB·실행 스크립트의 기준 위치를 유지합니다. Pi용 LINE ZIP은 `python scripts/package_line.py`로 생성하며, 독립 실행에 필요한 공유 API 모듈도 함께 포함합니다. ngrok 시험 ZIP과 설정·통신 명령은 바뀌지 않습니다.

Windows Python 환경에서 `.venv\Scripts\python.exe -m pip install -r requirements.txt`로 설치합니다. `Start-MOBOT.cmd`는 실물 모드로 시작합니다. 구매 화면은 http://127.0.0.1:8000/order, 관리자는 `../../WPF/RobotFactory.sln`을 Release로 빌드해 실행합니다. 기존 계정과 비밀번호는 유지합니다.

`Start-MOBOT-Simulation.cmd`는 장비 없는 모의 실행입니다. 실물 모드 서버를 먼저 종료해야 같은 포트를 사용할 수 있습니다. 모의 실행도 지정한 DB에 기록하므로 아래처럼 별도 DB 사용을 권장합니다.

```powershell
$env:MOBOT_DB="$env:TEMP\mobot-simulation.sqlite3"
.\Start-MOBOT-Simulation.cmd
```

별도 DB에는 계정이 자동 생성되지 않습니다. 실행 DB의 계정을 보존한 별도 시험용 DB를 준비하거나, 시험용 계정을 먼저 등록해야 합니다. 모의 영상과 완료 이벤트는 실물 검증 결과가 아닙니다.

## 주문·QR·재고

2026-11-24 사용자 지정 HUB: **3 서울 / 4 대전 / 5 천안**. 서울·인천·경기·강원·제주는 3번, 경상·부산·대구·울산·대전·세종은 4번, 충청·전라·광주는 5번이다. 새 주문 주소 분류에 적용하며 기존 주문의 저장 목적지는 소급 변경하지 않는다. GitHub 공통 문서의 이전 권역표와 다른 사용자 지정 변경이며, 공통 문서는 이번 작업에서 변경하지 않았다.


외부 주문번호는 UUID v4입니다. QR에는 PALLET-01~08만 기록하고 현재 주문과의 배정은 DB에 저장합니다. 주문 접수 시 머리·몸통과 공용 팔 재고를 함께 차감합니다. L1/R1처럼 같은 종류의 양팔은 ARM_1에서 2개를 차감합니다. 제작 수락 전 소비자 취소 또는 배출 전 관리자 취소만 재고를 한 번 복구합니다.

주문 수락 순서대로 한 회차씩 진행합니다. 배출 후 QR 판독·DB 저장이 끝나야 벨트가 출발합니다. 조립 대기 자세, 인수, 암실 도착, 동일 프레임 검사, 배송, 하역, 2번·85° 복귀의 완료 증거를 확인합니다. 오류 제품은 6/7로 분기합니다. 최종 복귀 후에도 팔레트는 AWAIT_MANUAL_RETURN이며 작업자 회수·재적재 확인 없이 READY로 바꾸지 않습니다.

## 실물 연결 준비

`factory-v1.example.json`을 참고해 **DB가 있는 폴더**의 `factory-v1.json`에 장비별 내부 HTTPS 주소·컨트롤러 키·이벤트 송신 키를 설정합니다. 네 카메라는 모두 메인 PC에 연결합니다. 카메라 4는 메모리의 실시간 프레임만 제공하고 DB 영상 보관을 하지 않습니다. 검사 사진은 카메라 3의 검사 결과와 함께 저장합니다.

`vision_module`은 Python 모듈로 `locate_assembly(cycle_id, spec, calibration_id, camera_id, frame, frame_id, captured_at)`과 `inspect_product(cycle_id, inspection_id, camera_id, frame, frame_id, captured_at)`를 제공해야 합니다. 실제 호출 인자는 `factory_v1/vision.py`를 확인하세요. 승인 보정 프로필·작업 범위와 비전 판정 프로필이 없으면 자동 시작을 허용하지 않습니다. 좌표와 YOLO 판정 기준은 임의로 만들지 않았습니다.

과거 UNO a/s 명령·Pi ngrok 폴링·AGV 시험 HTTP는 새 완료 이벤트와 바로 호환되지 않습니다. 실물 LINE 연결 모듈과 UNO v1 펌웨어를 raspi_line 및 firmware/MobotLineV1에 추가했습니다. 실측 프로필 적용과 ARM·AGV 연결 모듈의 실물 연동은 별도로 필요합니다. 서버는 접수 202를 완료로 취급하지 않으며 전송 응답 유실 때 이동을 자동 재전송하지 않습니다. 장비 재부팅·결과 미확인은 WPF 현장 확인 절차로 보류합니다.

구매 API만 외부 공개합니다. 관리자·장비 API는 내부망 전용이므로 기존 공개 ngrok 주소로 Pi를 연결하는 방식은 사용하지 않습니다. 다른 네트워크의 장비는 VPN 등으로 신뢰할 수 있는 내부 경로를 마련하고 HTTPS·장비별 자격 증명을 설정해야 합니다. 실제 인증서·장비 주소·제한시간은 배포 설정입니다. 세션은 시간 만료 없이 로그아웃·권한 변경 때 철회됩니다.

수동 동작·장비 설정·결산의 미확정 정책은 503으로 차단합니다. OpenAPI 계정 이름은 생성 요청 128자, 응답 100자로 서로 달라 현재 서버는 100자 제한을 적용합니다. 공유 규약 수정은 별도 합의가 필요합니다.

## 검증

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -B -m unittest discover -s tests
.venv\Scripts\python.exe -B scripts/check_openapi.py
dotnet build ../../WPF/RobotFactory.sln -c Release
```

OpenAPI 검사는 지정한 GitHub 커밋의 문서를 메모리로만 읽어 실제 응답과 대조합니다. `.github/workflows/factory-v1.yml`은 이 검증의 CI 설정입니다. 실물 30회 시험 완료를 의미하지 않습니다. 테스트는 임시 DB를 사용합니다. 실행 DB와 instance의 비밀키·백업·빌드 결과물을 저장소에 올리지 마세요.

카메라별 캡처 형식은 factory-v1.json의 camera_profiles에 설정할 수 있습니다. 현재 연결된 Global Shutter Camera의 CAM1은 640×480, MJPG, 90fps로 실측 후 설정했습니다. 지원하지 않는 장치에 같은 수치를 그대로 적용하지 마세요. WPF 표시 속도는 모니터 주사율과 PC 처리 성능의 영향을 받으며 CAM01 제목에 실제 갱신 fps를 표시합니다.

## GitHub 공유본 실행 위치

최신 WPF는 저장소 루트 `WPF/`에 있습니다. 서버·Pi·LINE/무선 시험·QR ROI 소스는 이 폴더를 기준으로 실행합니다. 최초 서버 설치는 이 폴더에 가상환경을 만들고 requirements.txt를 설치합니다. 별도 WPF·펌웨어 컴파일은 저장소 CI에 구성했습니다.

실제 주문 DB·계정·토큰·장치 설정·로그·배포 ZIP·빌드 산출물은 공유 대상에서 제외했습니다. Wi-Fi·기존 MySQL·터널 연결 정보는 예시 값으로 치환했습니다. 실제 ROI 설정은 제외했으며 첫 실행 후 사용 장치에서 ROI를 설정하세요. 서버 시작 시 DB 초기화·변환이 수행되므로 기존 운영 DB 적용은 담당자 검토 후 진행합니다.

## 이번 공유본의 규약 대조 범위

main 2161153의 OpenAPI v0.2.0에 대해 기존 공통 API 응답 56건을 대조했습니다. 계정 표시 이름의 요청·응답 128자 결정은 ACCOUNT_NAME_CONTRACT.md의 명시적 변경을 검사에 반영합니다. CAM2 장착 좌표 갱신 확장 기능은 이번 검사에서 실행하지 않으며 전체 v0.2.0 실물 호환 완료를 뜻하지 않습니다. HUB 3 서울·4 대전·5 천안 및 LAN/무선 시험용 확장도 팀 공통 규약과 차이를 검토해야 합니다.
