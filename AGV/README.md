# AGV 적용 메모 · 2026-11-26

Alpha #31의 ESP32 직접 배송 구현과 11월 26일 팀 공정의 연동 기준이다. 정차 중 검사 합격을 DB에 저장한 뒤 벨트를 재출발하고 IR2 정차 완료 후 인수를 지시한다. 현장 목표인 무선 모듈 장착 UNO와 ESP32 인터페이스의 대응은 미정이다.

## 운행과 서버 연결

부팅 시 프로그램의 기본 출발 위치는 **2번 대기 위치**이며 모터 정지·지게 85°로 준비한다.
자동 주행이나 제어 세션 연결을 시작하지 않는다. 운영자는 전원 켜기·리셋·업로드 전에
실제 AGV도 2번에 배치한다. 이는 위치 센서로 2번을 검출한 결과가 아니다.
다른 위치에서 부팅했다면 실물을 확인해 그 위치를 명시적으로 등록한 뒤 사용한다.
라인 이탈·통신 장애 등으로 위치가 불명확해지면 기존 차단·현장 확인 조건을 유지하며
2번으로 자동 복원하거나 복귀 주행하지 않는다.

`2번·85° → 1번 전진 → 110° 인수 → 2번 후진 → 8번 좌회전 → 9번까지 직진 → 목적지3~7 → 60° 하역 → 2번 후진 → 85° 대기`

기존 펌웨어의 목적지 표시명은 **3번 서울 HUB·4번 대전 HUB·5번 천안 HUB·6번 오류1·7번 오류2**다. 개정 공정에서 업무 서버는 같은 회차의 합격품에 대해서만 3~5번 HUB로 배송을 지시한다. 6·7번은 기존 위치·이력에 남는다. 대전·세종 신규 주문의 4번 배정 등 주소 권역은 최신 main 규약을 따른다.

[통합 이슈 #25](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/issues/25)에서 같은 회차의 검사 합격, IR2 정차, 인수·배송·하역 완료와 실물 연동을 추적한다. 검사 시작은 조립 완료·대기 복귀이며 검사 카메라와 촬영 위치는 미정이다.

- 실제 8~9번 사이 검사 가로 테이프도 제거한다. 복귀 시에는 이 선을 건너뛰는 처리 없이 실제 8번 분기를 인식한다.
- 업무 명령은 `PICK_UP → DELIVER → UNLOAD → RETURN_STANDBY`다. `DELIVER`가 인수 위치1번에서 2번까지 후진한 뒤 지정 목적지로 전진한다. 각 구간 완료를 확인한 뒤 다음 구간을 보낸다.
- `GO_TO_INSPECTION`은 422로 거절한다. ESP32 `/inspection`은 인증 후 410으로 거절한다. `INSPECTION_ARRIVED`를 가짜로 생성하지 않는다.
- 기존 DTO의 `DELIVER.params.inspection_id`와 `UNLOAD.params.inspection_id`는 유지한다. 업무 서버는 실제로 결정한 목적지와 그 근거 식별자를 제공해야 한다. 서버는 검사 시도마다 별도 ID를 발급하고 배송·하역에는 유효한 합격 시도의 ID를 전달한다. 검사 결과가 없다고 가짜 검사 ID/결과를 만들지 않는다.
- 이전 버전의 진행 중 journal을 자동으로 새 공정에 전환하지 않는다. 기존 회차 상태와 실제 위치를 대조한 뒤 복구한다. `inspecting`은 이전 장치와의 상태 대조용으로만 인정하고 후속 이동은 차단한다.
- 서보 GPIO23: 85° 대기, 110° 인수/운반, 60° 하역. 별도 적재 피드백 센서는 없다.

## 라인 이탈과 정지

- 진행 방향 센서 전부가 **연속 10000ms 미검출**이면 모터 출력을0으로 하고 `line_lost`로 전환한다. 전진은 앞6개, 후진은 뒤3개(진행 방향 좌35·중앙34·우36)를 사용한다. 후방 중앙 하나만 미검출이라고 정지하지 않는다.
- 회전·교차로 단계 전환으로 이 시간을 초기화하지 않는다. 한 센서라도 다시 검출하면 초기화한다. 정상 pause 시간은 제외한다. 후진 테스트에도 적용한다.
- 통신 소실·이상 교차로·구간/진입 시간초과 등 다른 정지 조건은 유지한다. 따라서 모든 정지가 무조건10초 뒤에 발생한다는 뜻은 아니다. 10초 기준은 전체 미검출에만 적용된다.
- `_fault`는 실행 명령을 `UNKNOWN` 및 `DEVICE_FAILED`/`RECONCILE_REQUIRED`로 기록하며 신규 운전을 차단한다. 작업이 없는 단계 사이에도 활성 회차의 취소·이탈·이전 검사 상태를 감지해 복구를 요구한다.
- `/stop`은 검사 대기 등 모든 상태에서 취소한다. `/pause`·`/resume`만 타이머·단계를 보존한다. 위치불명은 자동 재출발하지 않는다.
- 10초는 이번 사용자 지정 펌웨어 값이다. 현재 공통 통신규약에 이탈 전용 시간이나 `LINE_LOST` 전용 이벤트/오류 코드는 없다. 일반 장애·조회·현장 복구 계약을 사용하며 공통 enum을 임의 추가하지 않는다.

## 적용 및 검증

### Wi-Fi 단절 진단 펌웨어

`AgvRouteTest/WifiDiagnostics.h`는 최근 24건의 `BOOT`, `GOT_IP`,
`WIFI_DISCONNECTED`, `LOST_IP`, `HEARTBEAT_EXPIRED`를 기록한다. 단절 이유는
ESP32 원시 코드이며 의미를 추측해 바꾸지 않는다. `uptime_ms`는 해당 부팅 안의 시각이다.
이전 부팅의 이벤트는 `boot_number`로 구분하고, 현재 부팅의 `reset_reason`을 함께 확인한다.
일부 warm reset에서는 RTC 기록을 유지하지만 전원 제거·브라운아웃에서는 초기화한다.
이 기록만으로 전원 잡음이나 단절의 최초 원인을 확정하지 않는다.

RSSI는 연결 중 1초마다 갱신한 마지막 값으로, 단절 순간 실측값과 구분한다.
아직 측정하지 않은 RSSI는 HTTP에서 null, 시리얼에서 -32768이다.
Wi-Fi 이벤트 콜백은 짧은 기록만 저장하고 시리얼은 메인 루프에서 최대 250ms당
한 줄의 `AGV_DIAG`로 출력한다. 키·비밀번호·SSID·BSSID는 기록하지 않는다.

진단 펌웨어 업로드 후 인증된 `GET /diagnostics`로 조회할 수 있다. 기존 `/status`와
공통 `/device/v1` DTO는 유지한다. 조회는 제어 세션을 얻거나 하트비트를 갱신하지 않는다.
다음 도구는 이동·지게·위치 등록 명령을 보내지 않으며 로그를 비공개 MOBOT 진단 폴더에 저장한다.

```powershell
& "$env:LOCALAPPDATA\MOBOT\.venv\Scripts\python.exe" -B AGV/watch_diagnostics.py `
  --url http://192.168.1.61 --samples 60
```

비공개 키는 `agv_secrets.h` 또는 `AGV_DEVICE_KEY` 환경 변수에서 읽는다.
펌웨어의 Wi-Fi 설정도 무시되는 `agv_secrets.h`에 `AGV_WIFI_SSID`, `AGV_WIFI_PASSWORD`로 둔다.
`agv_secrets.example.h`를 복사해 실제 값을 넣고, 비공개 헤더는 커밋하지 않는다.
새 `.ino`와 두 헤더를 함께 빌드·업로드해야 하며, Python 변경만으로 실차에 진단 기능이
추가되지는 않는다. 업로드는 보드를 재부팅하며 기본 위치를 2번으로 설정한다.
실물을 2번에 정차시키고 단일 제어 주체를 확인한 뒤 수행한다.
기존 journal 복구 이력은 별도 대조 대상이며 기본 위치 변경으로 지우지 않는다.
이벤트 구현은 [Espressif Wi-Fi API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html)를 따른다.

### 메인 PC 서버·WPF 연결

같은 PC의 업무 서버(`http://127.0.0.1:8000`)와 AGV 어댑터
(`http://127.0.0.1:8765`)를 별도로 실행한다. WPF는 업무 서버에만 연결한다.
어댑터 완료 이벤트의 목적지는 `/api/v1/device-events`이며 `/device/v1/events`가 아니다.
구매 웹용 ngrok 주소를 장비 통신에 사용하지 않는다.

최초 설정은 저장소 루트에서 실행한다. 아래 `<...>` 값은 실제 배포 프로필로 지정한다.

```powershell
& "$env:LOCALAPPDATA\MOBOT\.venv\Scripts\python.exe" -B server/mobot/scripts/configure_live_agv.py `
  --device-url '<ESP32 내부 기본 URL>' `
  --device-key-file '<비공개 agv_secrets.h 절대 경로>' `
  --command-timeout-seconds <실측 동작 완료 제한>
```

설정 도구는 업무 서버와 동일하게 `MOBOT_DB` 옆의 `factory-v1.json`을 사용한다.
지정하지 않으면 `%LOCALAPPDATA%\MOBOT\instance`를 사용한다. 기존 장비·카메라·토큰을
보존하고 변경 전 백업을 만든다. 실행 프로필 `agv-adapter.json`도 같은 비공개 폴더에 둔다.
키 원문은 프로필에 복사하지 않으며 지정한 비공개 헤더에서 실행 시 읽는다.

- `devices.AGV.request_timeout_seconds`: 업무 서버 → 어댑터 HTTP 응답 제한.
- `command_timeout_seconds`: 인수·배송·하역·복귀 각 명령의 전체 완료 제한.
- `device_request_timeout_seconds`: 어댑터 → ESP32 HTTP 응답 제한.
- `heartbeat_seconds` 0.3초·`poll_seconds` 1초는 비교 시험용 초기값이며 최종 실측값이 아니다.
  펌웨어의 2초 제어권 만료는 변경하지 않는다.

Python 클라이언트는 HTTP 연결을 재사용하고 같은 클라이언트의 요청을 직렬 처리한다.
대기 중인 하트비트는 다음 일반 요청보다 먼저 보낸다. 하트비트는 단조 시각 기준으로
주기를 계산하고 지연된 주기를 몰아서 보내지 않는다. 실패한 요청의 연결은 폐기하며
같은 명령을 자동 재전송하거나 만료된 제어권을 자동으로 다시 얻지 않는다.
HTTPS는 기본 인증서 검증을 유지한다. 이 변경의 대역 시험 통과는 실차 안정화 완료를 뜻하지 않는다.

ESP32 HTTP 서버는 수락한 연결마다 `TCP_NODELAY`를 설정한다. 기본 헤더와 추가
헤더를 작은 쓰기로 나눠 보내는 ESP-IDF HTTP 서버와 TCP ACK 대기가 겹쳐 추가
헤더 전송이 지연되는 구간을 줄이기 위한 설정이다. 소켓 옵션 설정 실패는
시리얼에 기록하고 해당 연결을 거절한다. 수신 대기·무선 지연의 모든 원인이
해결됐다는 뜻은 아니며, Wi-Fi 절전 프로필과 시간 제한은 이 변경에서 유지한다.

```powershell
# 별도 터미널에서 유지한다. 시작만으로 이동 명령을 보내지 않는다.
powershell -NoProfile -File server/mobot/scripts/Start-AGV-Adapter.ps1
# 조회 전용: 제어권 획득·하트비트·위치 등록을 하지 않는다.
powershell -NoProfile -File server/mobot/scripts/Start-AGV-Adapter.ps1 -CheckOnly
```

실제 위치를 작업자가 확인한 경우에만 시작 명령에 `-ConfirmedPosition 1`처럼 현재 위치를
명시할 수 있다. 정차·지게 정지·다른 제어권이 없는 상태에서 위치를 한 번 등록하고 실제
상태 반영을 조회한다. 응답 유실로 같은 등록·이동을 자동 재전송하지 않는다.
이 옵션은 이동이나 journal 복구 완료를 뜻하지 않는다. 기존 복구 필요 이력은 보존한다.
실행기는 기존 journal에 복구 필요·진행 중·결과 미확인 기록이 있으면 제어권 획득과
위치 등록 전에 시작을 거절한다. 기존 이력을 삭제하거나 새 journal로 우회하지 않는다.
업무 서버와 실물을 대조하고 기존 로컬 복구 절차를 완료한 뒤 실행한다.
인수 시작에는 실제 2번 대기·지게 85° 및 서버의 같은 회차 IR2 정차 조건이 필요하다.

업무 서버는 `WPF/Start-MOBOT.cmd`, WPF는 빌드한
`WPF/RobotFactory.Admin/bin/Release/net10.0-windows/RobotFactory.Admin.exe`로 실행한다.
기존 관리자 계정으로 로컬 서버에 로그인하면 AGV의 온라인·장비 상태가 표시된다.
전체 자동 운전에는 LINE·ARM·카메라·검사 준비도 필요하다. AGV만 연결됐다는 이유로
합격 검사나 다른 장비 완료를 모의 생성하지 않는다.

추가 검증은 `server/mobot/tests/test_live_agv_config.py`와
`test_agv_http_integration.py`에 있다. 후자는 임시 DB와 장비 대역을 사용하면서 실제
어댑터 HTTP·완료 콜백·서버 DB·WPF 조회 API를 연결한다. 실차 시험과 구분한다.

펌웨어와 연결 모듈을 함께 적용한다. 서버 담당자는 기존 암실 도착 대기를 제거하고 위 호출 순서를 연결해야 한다. `python -m pip install -r AGV/requirements.txt` 후 `python AGV/device_adapter.py --help`로 실행 옵션을 확인한다. 키는 비공개 `agv_secrets.h`/환경 변수, journal은 업무DB와 분리한 private 경로를 사용한다. 제한시간은 실제 전체 이동시간을 기준으로 설정한다.

단위·대역 시험은 실제 GPIO/모터를 구동하지 않는다. 실차에서 검사선 제거, 5개 경로 왕복, 라인 이탈10초·재검출·정지/재개·통신 소실, 서버 공정 변경을 확인해야 한다.
