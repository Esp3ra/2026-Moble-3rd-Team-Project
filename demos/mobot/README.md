# MOBOT · 통신 규약 v0.1.0 전환본

Flask·SQLite·구매 웹·WPF는 `/api/v1`을 사용합니다. 장비 연결 모듈과는 `/device/v1` 명령·상태·이벤트로 통신합니다. 이전 `/api/orders`, `/api/equipment`, `/api/devices` 시험 API는 기본적으로 410을 반환합니다. 아래 이전 안내 문서와 기존 Pi 배포 ZIP은 v0.1.0 연결용이 아닙니다.

기준은 GitHub main의 [AGENTS](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/AGENTS.md), [공통 규약](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/통신규약/공통_통신규약과_DTO.md), [OpenAPI](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/통신규약/openapi.yaml), [하드웨어 공정](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/HARDWARE_WORKFLOW.md), [팀 규약](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/프로젝트_전체규약_팀답변_공유본.md)입니다. 이 문서는 구현·실행 안내이며 원격 규약의 복사본이 아닙니다.

## 실행

Windows Python 환경에서 `.venv\Scripts\python.exe -m pip install -r requirements.txt`로 설치합니다. `Start-MOBOT.cmd`는 실물 모드로 시작합니다. 구매 화면은 http://127.0.0.1:8000/order, 관리자는 `../../WPF/RobotFactory.sln`을 Release로 빌드해 실행합니다. 기존 계정과 비밀번호는 유지합니다.

`Start-MOBOT-Simulation.cmd`는 장비 없는 모의 실행입니다. 실물 모드 서버를 먼저 종료해야 같은 포트를 사용할 수 있습니다. 모의 실행도 지정한 DB에 기록하므로 아래처럼 별도 DB 사용을 권장합니다.

```powershell
$env:MOBOT_DB="$env:TEMP\mobot-simulation.sqlite3"
.\Start-MOBOT-Simulation.cmd
```

별도 DB에는 계정이 자동 생성되지 않습니다. 실행 DB의 계정을 보존한 별도 시험용 DB를 준비하거나, 시험용 계정을 먼저 등록해야 합니다. 모의 영상과 완료 이벤트는 실물 검증 결과가 아닙니다.

## 주문·QR·재고

외부 주문번호는 UUID v4입니다. QR에는 PALLET-01~08만 기록하고 현재 주문과의 배정은 DB에 저장합니다. 주문 접수 시 머리·몸통과 공용 팔 재고를 함께 차감합니다. L1/R1처럼 같은 종류의 양팔은 ARM_1에서 2개를 차감합니다. 제작 수락 전 소비자 취소 또는 배출 전 관리자 취소만 재고를 한 번 복구합니다.

주문 수락 순서대로 한 회차씩 진행합니다. 배출 후 QR 판독·DB 저장이 끝나야 벨트가 출발합니다. 조립 대기 자세, 인수, 암실 도착, 동일 프레임 검사, 배송, 하역, 2번·85° 복귀의 완료 증거를 확인합니다. 오류 제품은 6/7로 분기합니다. 최종 복귀 후에도 팔레트는 AWAIT_MANUAL_RETURN이며 작업자 회수·재적재 확인 없이 READY로 바꾸지 않습니다.

## 실물 연결 준비

`factory-v1.example.json`을 참고해 **DB가 있는 폴더**의 `factory-v1.json`에 장비별 내부 HTTPS 주소·컨트롤러 키·이벤트 송신 키를 설정합니다. 네 카메라는 모두 메인 PC에 연결합니다. 카메라 4는 메모리의 실시간 프레임만 제공하고 DB 영상 보관을 하지 않습니다. 검사 사진은 카메라 3의 검사 결과와 함께 저장합니다.

`vision_module`은 Python 모듈로 `locate_assembly(cycle_id, spec, calibration_id, camera_id, frame, frame_id, captured_at)`과 `inspect_product(cycle_id, inspection_id, camera_id, frame, frame_id, captured_at)`를 제공해야 합니다. 실제 호출 인자는 `factory_v1/vision.py`를 확인하세요. 승인 보정 프로필·작업 범위와 비전 판정 프로필이 없으면 자동 시작을 허용하지 않습니다. 좌표와 YOLO 판정 기준은 임의로 만들지 않았습니다.

현재 원본 UNO의 a/s 명령, 과거 Pi ngrok 폴링, 기존 AGV 시험 HTTP는 새 완료 이벤트와 바로 호환되지 않습니다. 담당 장비 연결 모듈과 UNO v1 펌웨어 구현·실측이 필요합니다. 서버는 접수 202를 완료로 취급하지 않으며 전송 응답 유실 때 이동을 자동 재전송하지 않습니다. 장비 재부팅·결과 미확인은 WPF 현장 확인 절차로 보류합니다.

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

## 저장소 공유 경로와 제외 자료

최신 WPF는 저장소 루트의 `WPF/`에, 서버·웹·규약 v1 구현·Pi 연결 자료는 이 폴더에 있습니다. `WPF-CameraPreview/`와 기존 UNO·AGV 자료는 이전 작업 기록입니다. 통합 실행은 본문의 v0.1.0 안내를 따릅니다.

실제 DB·계정·비밀키·개인 환경 설정·로그·빌드 산출물은 제외했습니다. AGV Wi-Fi 값은 공유용 자리표시자이며, 이전 MySQL 모듈의 기본 주소·사용자도 로컬 주소·자리표시자로 치환했습니다. 새 DB의 계정은 담당자가 등록해야 합니다.
