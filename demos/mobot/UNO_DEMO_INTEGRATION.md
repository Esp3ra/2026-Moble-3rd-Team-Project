# UNO 데모 · SQLite · WPF 연결

후속 변경: AGV 배송 데모와 목적지 3~7 체계를 추가했다. 현재 AGV 연결과 DB 목적지 변경은 [AGV 통합 안내](AGV_DEMO_INTEGRATION.md)를 따른다. 아래 문서는 UNO 연결 단계의 기록이다.

2026-11-19. 사용자가 확정한 범위: 기존 SQLite 사용, WPF에서 실행한 주문을 QR 인식 시 배정, 실물 장비 없이 데모 먼저 연결.

## 연결 구조

```text
웹 주문 → 중앙 Flask :8000 → SQLite ORDERS
WPF 로그인 → 중앙 인증 세션 → /api/equipment/*
                              ↓
                   EquipmentDemo (단일 가상 장비)
                   ├─ 기존 Bench 상태·ACK·heartbeat 검증
                   ├─ demo_step 가상 UNO 이벤트
                   ├─ 기존 팔레트 QR PNG → 합성 영상 → OpenCV 판독
                   └─ SQLite 주문·팔레트 배정 및 회차 기록
```

중앙 서버 프로세스에서 데모를 실행한다. 장비 모듈의 별도 Flask :8080, MySQL, `order_api.py` :8000은 실행하지 않는다. 주문 API는 기존 중앙 `/api/orders` 하나를 사용한다. 모듈의 MySQL 자동 배정 코드는 호출하지 않으며, SQLite 트랜잭션으로 선택 주문만 배정한다.

실물 연결로 확장할 때는 USB·카메라가 연결된 장비 PC에 제어 프로세스를 하나 두고 중앙 서버가 인증된 API로 요청해야 한다. 현재 코드에는 그 원격 실물 연결을 구현하지 않았다. UNO·카메라·모터·로봇팔·AGV는 실제 연결하지 않았다.

## 실행과 화면

프로젝트 루트에서:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\Start-MOBOT.cmd
```

다른 PowerShell에서:

```powershell
dotnet run --project .\WPF\RobotFactory.Admin\RobotFactory.Admin.csproj -c Release
```

1. WPF에서 기존 관리자 계정으로 로그인한다. 기본 서버는 `http://127.0.0.1:8000`이다.
2. 생산 대기 주문을 선택한다.
3. 왼쪽 하단의 데모 배출 팔레트를 고르고 시작 위치 확인을 체크한다. 팔레트 선택은 가상으로 배출할 QR 선택이며, 이때 DB 배정은 하지 않는다.
4. **선택 주문 데모 시작**을 누른다. 영상에 실제 팔레트 QR 이미지가 나타나면 OpenCV가 읽고 해당 주문에 배정한다.
5. 장비 동작과 DB 배정이 정상 완료되면 **배출 확인 · 회차 완료**가 활성화된다. 운영자가 누르면 장비 회차 `SUCCESS`를 기록한다.
6. DB 실패 시 **DB 재시도**를 사용한다. 장비 시작 명령을 다시 보내지 않는다. **데모 전체 정지**는 가상 회차만 중단한다.

CAM 01은 640×480 합성 JPEG를 약 5fps로 조회한다. 기존 추가 카메라 1 자리는 시간 설정·회차 추적 영역으로 사용한다. 추가 카메라 2는 미연결 자리로 유지한다. 하단 이벤트와 알림은 장비 API 값을 표시한다. 부품 검사는 아직 연결하지 않아 수신 대기로 표시한다.

시간은 IR1 지연 5500ms, 작업 가정 대기 3000ms, IR2 지연 2000ms가 기본이다. 500ms 단위이고 IR1/IR2는 0~25000ms, 작업 대기는 0~30000ms이다. 데모는 액추에이터 전진·복귀 구간을 단축하므로 기본 한 회차는 약 13초다. 이 시간은 실제 하드웨어의 전진 10초·대기 0.5초·복귀 10초와 다르다. 시간 설정의 revision 및 응답 값 일치 검증은 기존 Bench를 사용한다.

## 환경과 저장 위치

| 설정 | 기본값 / 용도 |
|---|---|
| `MOBOT_DB` | `instance/mobot.sqlite3`, 중앙 SQLite |
| `MOBOT_HOST` | `127.0.0.1` |
| `MOBOT_PORT` | `8000` |
| `MOBOT_API_URL` | WPF 최초 서버 주소, 기본 `http://127.0.0.1:8000` |
| `MOBOT_ADMIN_TOKEN` | 기존 외부 조회 호환용; 장비 제어는 실제 계정 세션 필요 |
| Flask `UNO_DEMO_ROOT` | `instance/uno-demo`, 회차 이벤트·이미지 기록 경로 |
| Flask `UNO_DEMO_QRS` | `pallet_qrs`, 기존 `PALLET-01.png`~`PALLET-08.png` 사용 |

마지막 두 값은 환경 변수가 아닌 Flask 설정이다. 테스트에서 별도 임시 경로로 덮어쓴다. MySQL `DB_HOST` 등은 이번 중앙 데모에 사용하지 않는다. 추가 실행 의존성은 OpenCV와 NumPy이며 루트 `requirements.txt`에 포함했다.

## API

조회와 제어 모두 `Authorization: Bearer <로그인 세션 토큰>`을 사용한다. POST는 JSON 객체를 요구한다. 클라이언트가 임의 `owner`를 보내지 않으며 서버가 인증된 세션 토큰의 해시를 제어 소유자로 사용한다. 로그인은 기존 `POST /api/auth/login`이다.

| 메서드 | 경로 | 내용 |
|---|---|---|
| GET | `/api/equipment/state` | 현재 장비, 회차, DB 결과, 버튼 가능 여부, 센서, 시간, 이벤트 |
| GET | `/api/equipment/frame.jpg` | 인증된 현재 JPEG, 캐시 금지 |
| POST | `/api/equipment/start` | 선택 주문·가상 팔레트·위치 확인·중복 방지 UUID |
| POST | `/api/equipment/heartbeat` | 시작한 세션에서 1초 간격 `{}` |
| POST | `/api/equipment/stop` | 로그인 관리자 누구나 데모 정지 `{}` |
| POST | `/api/equipment/confirm-ejection` | 시작한 세션에서 배출 확인 `{}` |
| POST | `/api/equipment/database-retry` | 시작한 세션에서 실패 DB 처리만 재시도 `{}` |
| POST | `/api/equipment/timings` | 세 시간 설정 적용 |

시작 요청:

```json
{
  "job_id": "mobot-1",
  "pallet_id": "PALLET-01",
  "position_ready": true,
  "request_id": "181a86da-7c7d-4be9-aaf4-5f637fe05a03"
}
```

시간 설정 요청:

```json
{"values":{"ir1_delay_ms":5500,"robot_wait_ms":3000,"ir2_delay_ms":2000}}
```

QR 배정 직후 응답의 주요 필드 예시(실제 응답에는 추가 필드가 있다):

```json
{
  "mode": "DEMO",
  "state": "RETURNING",
  "can_confirm": false,
  "trial": {"qr_ok": true, "qr_ack": true, "machine_done": false},
  "run": {
    "run_id": "20261119_155231_abcc4106",
    "job_id": "mobot-1",
    "expected_pallet_id": "PALLET-01",
    "pallet_id": "PALLET-01",
    "mapping_id": 1,
    "database_status": "mapped",
    "finished_at": null
  }
}
```

인증 실패는 401, 제어 권한 부족은 403, 상태·내용 충돌은 409, DB·파일 처리 오류는 503이다. 같은 UUID·세션·주문·팔레트의 시작 재요청은 기존 회차를 반환한다. 다른 내용의 같은 UUID는 거절한다. WPF는 응답 유실 후 시작 재시도에 같은 UUID를 유지한다.

## DB와 상태 대응

| 시점 | 장비 | 주문 | 팔레트·배정 | 회차 DB |
|---|---|---|---|---|
| 웹 주문 접수 | 대기 | `PENDING` | 기존 READY 8개 유지 | 없음 |
| WPF 선택 주문 실행 | `STARTING` → `PUSHING` | `PENDING` | 아직 배정 없음 | `run_id`·`job_id`·예정 QR·운영자 저장 |
| QR 단일 인식 + QR ACK | 배출·이송 진행 | `IN_PROGRESS` | `MAPPED`, 활성 매핑 생성 | 실제 QR·`mapping_id`, DB `mapped` |
| IR1 정차·가정 작업·IR2 최종 정차 | `WAIT_IR` → `ROBOT_WAIT` → `SECOND_BELT` → `WAIT_IR2` | 유지 | 유지 | 단계별 플래그 기록 |
| 장비 완료 및 QR·ACK 확인 | `AWAIT_CONFIRM` | 유지 | 유지 | DB 성공·장비 플래그를 함께 검사 |
| 운영자 배출 확인 | `SUCCESS` | `IN_PROGRESS` 유지 | 활성 매핑 유지 | DB `success`, 확인·종료 시각 저장 |
| QR 누락 / heartbeat 단절 / 정지 | `QR_UNCONFIRMED` / `ERROR` / `STOPPED` | 이미 배정됐으면 유지 | 이미 배정됐으면 유지 | 실패 상태·플래그·종료 시각 보관 |
| 서버 재시작 후 이전 미종료 회차 | 새 장비 대기 | 유지 | 유지 | 이전 회차 `INTERRUPTED`, 자동 재시작 없음 |

QR 읽기 시 배정은 사용자가 지정한 방식이다. 제공 모듈의 기본 동작(종료 후 가장 오래된 주문 자동 선택)과 시점·대상이 다르다. 기존 `DatabaseWorker.eligible()`은 최종 배출 확인 조건 검사에 재사용한다. QR·QR ACK·IR1·첫 정지·재구동·IR2·최종 정지·장비 완료 및 활성 DB 매핑을 모두 확인한다.

`SUCCESS`는 이 장비 회차의 성공이다. 전체 조립·검사·배송의 주문 완료를 뜻하지 않는다. 주문 `COMPLETED`, 매핑 해제, 팔레트 READY 복귀, 순환 횟수 증가는 아직 별도 공정 흐름이 정해지지 않아 수행하지 않는다. 기존 SQLite에 없는 `body_detected`도 임의 추가하거나 감지 성공으로 기록하지 않았다. 부품 ID·목적지도 기존 중앙 DB 값을 유지한다.

추가 테이블:

- `EQUIPMENT_RUNS`: 회차/요청 UUID, 선택 주문, 예정·실제 팔레트, 매핑 ID, 계정·세션 소유자 해시, 상태, DB 결과·오류, 장비 플래그 JSON, 시작·갱신·확인·종료 시각.
- `EQUIPMENT_DEVICE_LEASES`: 장비별 중앙 프로세스 소유권과 갱신 시각. 5초 이내 활성 프로세스가 있으면 다른 프로세스의 연결을 거절한다. 소유권을 잃으면 중단한다.

SQLite `BEGIN IMMEDIATE`와 미종료 회차의 장비별 유일 인덱스로 중복 운전을 막는다. QR 배정 트랜잭션 안에서 주문·팔레트·활성 매핑을 다시 확인한다. 운전 중인 대기 주문의 드롭도 차단한다. 재시작 시 미종료 회차를 보관하고 자동 운전하지 않는다. QR 배정 이후의 실패는 추적 가능하도록 배정을 남긴다.

화면 heartbeat가 4초 이상 끊기면 기존 제어기가 데모를 중단한다. `ir_clear`, `ir2_clear`가 true일 때 화면에는 **비어 있음**으로 표시한다.

## 수정 파일

- 중앙 `equipment.py`: SQLite 어댑터, 데모 작업, 인증 API, QR·회차 추적, 재시도·확인.
- `app.py`, `requirements.txt`: 데모 API 등록과 OpenCV·NumPy 의존성.
- `smart_factory_sqlite.sql`, `orders.py`: 회차·소유권 테이블과 운전 중 주문 취소 차단.
- `pallet_system_uno_sim/controller.py`, `app.py`: 실제 펌웨어와 동일한 시작 ACK `ACTUATOR:PUSH_10000MS`; 재사용에 필요한 패키지 import 지원.
- `pallet_system_uno_sim/database.py`, `camera_pipeline.py`: 중앙에서 모듈 재사용을 위한 import 지원.
- `pallet_system_uno_sim/tests/*`: ACK 수정 대응, 실제 펌웨어 ACK 회귀 테스트.
- WPF `Services/EquipmentApiClient.cs`: 장비 API·JPEG DTO와 클라이언트.
- WPF `ViewModels/EquipmentViewModel.cs`, `ScreenModels.cs`: 1초 상태·heartbeat, 200ms 영상, 선택 주문 시작, 시간 설정, 확인·DB 재시도, 알림.
- WPF `Views/DashboardView.xaml` 및 코드: 영상·데모 제어·회차 정보 배치.
- `tests/test_equipment_demo.py`: 중앙 SQLite 연결 테스트.

## 검증 결과

- 기존 장비 모듈: **65개 통과**. 펌웨어에서 실제 ACK 문자열을 읽는 회귀 테스트 포함. 가상 USB 단절·복구, 카메라 복구, 수동 조작, 시간 설정, QR·주문 검증 포함.
- 중앙 데모: **12개 통과**. 선택한 주문의 QR 시점 배정, QR 미인식, 배출 확인 차단·중복 확인, DB 실패 재시도(추가 시작 없음), 인증·소유자·취소 차단, heartbeat 단절, 서버 재시작·프로세스 중복 방지·소유권 상실, 동시 시작, 활성 매핑 재검사, JPEG·시간 검증.
- WPF Release 빌드: 경고 0, 오류 0.
- 실제 HTTP+WPF 검증 앱: 로그인 → 주문 접수·선택 → 팔레트 8개 → QR 판독·완료 전 DB 배정 → WPF heartbeat만으로 회차 유지 → JPEG 표시 → 배출 확인 → DB 성공, 주문 IN_PROGRESS·팔레트 MAPPED 유지까지 통과. 임시 SQLite·임시 계정으로 실행해 실제 주문에 시험 데이터를 넣지 않았다.

테스트 명령(루트):

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
dotnet build ..\..\WPF\RobotFactory.sln -c Release
```

장비 모듈의 기존 테스트는 해당 모듈 폴더를 작업 디렉터리로 실행해야 한다. 별도 테스트 환경에 `pallet_system_uno_sim/requirements.txt` 의존성을 설치한 후 `python -B -m unittest discover -s tests`로 실행한다. 두 폴더 모두 `app.py`가 있으므로 루트에서 장비 테스트를 그대로 실행하면 import 대상이 달라진다.

## 실물 연결 전 확인할 항목

실제 UNO·센서·모터·USB 탈착·실제 카메라·MySQL·AGV를 이번 통합 실행에서 검증하지 않았다. 펌웨어는 수정하거나 업로드하지 않았다. 실물 단계에서는 장비 PC 주소·USB 포트·카메라 인덱스, 원격 제어 인증·연결 단절 처리, 실제 시작·종료 응답을 확인해야 한다. AGV 목적지 1/2/3과 지도 3/4/5 대응, AGV 통신 규격, 실제 로봇팔 요청·완료 응답, 본체 감지 주체, 전체 공정 완료·팔레트 해제 시점은 사용자·담당자 확인 후 연결한다.
