# AgvRouteTest · 중앙 서버 · WPF 데모

2026-11-19. 사용자 선택: DB 목적지 체계를 AGV 지도에 맞춰 변경하고, 실제 ESP32 연결 전에 장비 없는 데모를 연결한다.

## 연결한 흐름

웹 주문 → SQLite 주문·목적지 저장 → WPF 선택 주문 UNO 데모 → QR·주문 배정 → UNO 배출 확인 → **선택 주문 배송 시작** → AGV 배송 7단계 → 2번·지게 85° 복귀 → AGV 작업 SUCCESS.

AGV는 기존 추가 카메라 2 자리에 표시한다. 실제 카메라는 아직 연결하지 않았다. UNO 배출 확인이 끝난 선택 주문만 배송할 수 있다. 다른 주문을 임의로 자동 선택하거나 자동 배차하지 않는다.

| 목적지 번호 | 현재 HUB 명칭 | 기존 데모의 주소 자동 판정 |
|---|---|---|
| 1 | 컨테이너 팔레트 인수 위치 | 배송 목적지 아님 |
| 2 | 대기·복귀 위치 | 배송 목적지 아님 |
| 3 | 서울 HUB | 서울·서울시·서울특별시 |
| 4 | 대전 HUB | 평택·평택시, 경기/경기도 평택시 |
| 5 | 천안 HUB | 수원·수원시, 경기/경기도 수원시 |
| 6 | 오류1 구역 | 관리자 선택 |
| 7 | 오류2 구역 | 관리자 선택 |

앞쪽 우편번호는 무시한다. 다른 지역이나 불명확한 주소는 `target_station=NULL`이며 관리자가 목적지를 선택해야 한다. WPF는 선택 주문의 목적지를 기본값으로 표시하고, AGV 배송 시작 시 선택값을 주문 DB에도 저장한다. 지도·이동 중 경유 번호 1/2와 배송 목적지 3~7을 구분한다.

## AGV 단계와 완료 조건

`AgvRouteTest.ino` 상단 전달 내용과 실제 `/status`, `/go`, `/servo` 구현을 기준으로 서버가 순서를 관리한다. 펌웨어의 배선·경로표·센서·모터·서보 코드 및 Wi-Fi 설정은 수정하지 않았다.

| 단계 | 펌웨어에 대응하는 동작 | 다음 단계로 넘어가는 조건 |
|---|---|---|
| 1 | `/go?point=1` | state=arrived, position=1 |
| 2 | `/servo?angle=110` | servo_angle=110, fork_moving=false |
| 3 | `/go?point=2` | arrived, position=2 |
| 4 | `/go?point=<3~7>` | arrived, position=선택 목적지 |
| 5 | `/servo?angle=60` | servo_angle=60, fork_moving=false |
| 6 | `/go?point=2` | arrived, position=2 |
| 7 | `/servo?angle=85` | position=2, servo_angle=85, fork_moving=false, ready 또는 arrived |

이동 도중 지게 각도를 바꾸지 않으며, 2번 경유 때는 110°, 하역 후 복귀 중에는 60°를 유지한다. 하역은 `angle=60` 기준이다. 펌웨어의 `position=lower`는 85°여서 하역 명령으로 사용하지 않는다.

중앙 데모는 `DemoVehicle`이 이 상태 변화를 모사한다. `/go`는 약 0.8초, `/servo`는 약 0.5초로 단축되며, 서버는 150ms 간격으로 완료를 확인한다. 실제 HTTP 202를 완료로 간주하지 않는 단계 구조다. 현재 실행에서는 ESP32 HTTP 요청을 보내지 않는다. `servo_angle` 역시 실제 각도 센서 측정값이 아니라 펌웨어 기준 출력 각도에 대응하는 데모 값이다.

## DB 변경과 보존

- `ORDERS.target_station`: 기존 CHECK 1/2/3을 3/4/5/6/7로 변경한다.
- 기존 테이블은 시작 시 자동 변환한다. 과거 번호와 새 번호의 의미가 다르므로 기존 주소에서 목적지를 다시 계산한다. 기존 데모의 주소 분류에 따라 서울 주소는 3, 평택 주소는 4, 수원 주소는 5, 나머지는 미배정이다. 주문·결제 필드·요청 UUID·계정·팔레트·매핑·외래키는 보존한다. 변환은 한 번만 적용되며 외래키 검사에 실패하면 롤백한다.
- `AGV_TASKS`: task_id, 중복 방지 request_id, UNO equipment_run_id, job_id, pallet_id, mapping_id, 목적지, 운영 계정·세션 소유자 해시, 단계 번호, 상태·오류, 장비 상태 JSON, 생성·갱신·종료 시각.
- `AGV_TASK_EVENTS`: 각 단계 요청·완료 이벤트와 시각.
- 기존 `EQUIPMENT_DEVICE_LEASES`에 장비 키 `AGV-DEMO`를 사용해 프로세스 중복 실행을 막는다. UNO-DEMO와는 별개다.

배송 시작 시 SQLite 트랜잭션 안에서 UNO SUCCESS·DB success·운영자 배출 확인·주문 IN_PROGRESS·활성 QR 매핑·팔레트 MAPPED를 다시 확인한다. 한 UNO 회차당 AGV 작업은 한 개이며 실패 후에도 자동 재배차하지 않는다. 같은 요청 UUID의 재시도는 기존 작업을 반환한다. 장비별 미종료 작업은 한 개만 허용한다.

AGV SUCCESS는 배송·복귀 데모의 성공이며 전체 생산 검사를 완료 처리하지 않는다. 주문 IN_PROGRESS, 팔레트 MAPPED와 활성 QR 배정은 유지한다. 전체 공정 종료·팔레트 회수·배정 해제 시점은 후속 공정 정의에 따라 연결해야 한다.

## WPF와 API

기존 서버 `Start-MOBOT.cmd`와 WPF `WPF/RobotFactory.sln`을 실행한다. 별도의 ESP32 주소나 AGV 프로세스 설정 없이 중앙 서버에 데모가 실행된다. 기존 계정 로그인을 사용한다.

WPF에서 UNO 배출 확인을 완료한 주문을 선택하고, 오른쪽 가운데 AGV 배송 패널에서 목적지 확인 후 **선택 주문 배송 시작**을 누른다. 위치·지게 각도·단계·작업 ID·결과를 확인할 수 있다. **AGV 데모 정지**는 가상 AGV 배송만 중단하며 UNO 데모 정지와 분리돼 있다.

| API | 요청 |
|---|---|
| GET `/api/agv/state` | 장비·최신 작업·최근 이벤트·배송 가능 주문·제어 가능 여부 |
| POST `/api/agv/start` | `{"job_id":"mobot-1","destination":3,"request_id":"새 UUID"}` |
| POST `/api/agv/heartbeat` | `{}` |
| POST `/api/agv/stop` | `{}` |

모두 기존 `Authorization: Bearer <로그인 토큰>`을 사용한다. POST는 JSON 객체를 요구하며 실제 계정 세션만 제어할 수 있다. 소유자는 서버가 토큰 해시로 결정한다. WPF는 1초 간격으로 상태와 시작 세션의 heartbeat를 보낸다. 4초 초과 단절, 장비 오류 상태, 잘못된 서보 각도, 모드 변경, 단계 30초 초과는 배송을 중단한다. 서버 재시작 시 미종료 작업은 INTERRUPTED로 보관하고 자동 재출발하지 않는다.

AGV 상태의 주요 필드:

```json
{
  "mode":"DEMO",
  "device":{"state":"arrived","position":2,"servo_angle":85,"fork_moving":false,"mode":"auto"},
  "task":{"task_id":"작업 UUID","equipment_run_id":"UNO 회차 ID","job_id":"mobot-1","pallet_id":"PALLET-01","destination":3,"state":"SUCCESS","step_index":7},
  "can_stop":false
}
```

## 검증

- 중앙 자동 테스트 **26개 통과**: 기존 UNO/SQLite 12개 + AGV·목적지 변환 14개.
- AGV는 7단계 순서·완료 대기·최종 복귀, 미확인 UNO 배송 차단, 요청 UUID 재시도·동시 배차·중복 배송 차단, heartbeat 단절·오류 상태·정지, 오류 구역 배송, 재시작·프로세스 중복·소유권 상실, 이동 중 명령 재발행 방지를 검증했다.
- 목적지 주소 파싱, 이전 CHECK 제약 변환, 기존 주문·매핑·요청 UUID 유일 제약·외래키 보존, 재시작 시 중복 변환 방지도 검증했다.
- WPF Release 빌드 경고 0, 오류 0.
- 임시 DB에서 실제 HTTP와 WPF 로그인·주문 선택·QR 배정·UNO 확인·AGV 배송·2번/85° 복귀·목적지 DB 반영·중복 배송 차단까지 통과했다. 실제 DB에는 테스트 주문을 넣지 않았다.

실행 명령:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
dotnet build .\WPF\RobotFactory.sln -c Release
```

## 실제 ESP32 연결에 남은 작업

현재 범위는 데모이며 펌웨어 업로드·실차 주행·센서·서보는 검증하지 않았다. 실물 단계에는 ESP32 주소, 실제 2번·85° 초기 배치 확인, `X-AGV-Control: 1` 헤더·URL 쿼리를 사용하는 HTTP 어댑터, 응답 유실·409 후 상태 확인·중복 명령 방지가 필요하다. 펌웨어의 자동 주행에는 중앙 서버 heartbeat 규격이 없으므로 중앙 단절 시 실차 정지 정책도 실제 장비 단계에서 확정해야 한다. 중앙 데모 heartbeat는 실제 ESP32 정지를 보장하는 기능이 아니다.
