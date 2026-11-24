# Pi·UNO 실물 연결 · v0.1.0

이 패키지는 실제 USB 시리얼로 UNO의 컨베이어·액추에이터·IR1/IR2를 제어합니다. 장비 없는 데모 프로그램인 `raspi_agent/agent.py`, 예전 ZIP과 함께 실행하지 마세요. 카메라 4개는 모두 메인 PC에 연결합니다. Pi에는 OpenCV가 필요하지 않습니다.

## 준비된 기능과 아직 필요한 것

- 실물 UNO 펌웨어: `firmware/MobotLineV1/MobotLineV1.ino`
- Pi 연결 모듈: `python -m raspi_line`
- HTTP: `/device/v1/commands`, `/device/v1/commands/{command_id}`, `/device/v1/status`, `/device/v1/events`
- 완료 이벤트: 저장 후 `/api/v1/device-events`로 같은 이벤트 ID를 재전송합니다. 명령 접수 202는 완료가 아닙니다.
- 배출은 액추에이터 전진→정지→복귀 후 완료하며 벨트는 계속 정차합니다. 메인 서버가 QR·DB 배정을 저장하고 MOVE_TO_ASSEMBLY를 보내야 벨트가 출발합니다.
- IR1 정차 후에는 로봇팔의 실제 조립·대기 자세 완료까지 기다립니다. 별도 MOVE_TO_PICKUP 명령으로 IR2까지 이동합니다. 임의 조립 대기 타이머가 없습니다.
- PAUSE는 출력을 멈추고 단계·남은 시간을 보존합니다. RESUME은 완료된 PAUSE의 UUID를 대조합니다.
- 같은 명령을 다시 받아도 C 전문을 재전송하지 않습니다. Q 조회와 저장된 결과를 사용합니다. Pi 재시작·UNO 재부팅·시리얼 단절·결과 미확인은 UNKNOWN/FAULT로 보류합니다. DB를 삭제해서 이 상태를 해제하지 마세요. 회차를 잃은 실물의 복구는 현장 대조와 승인된 복구 절차가 필요하며, 임의로 완료 처리하는 기능은 없습니다.

사용자가 확인한 배선: STEP=5, DIR=6, 액추에이터 ENA=9·IN1=7·IN2=8, IR1=4·IR2=2. 구동 시간은 아직 미정이므로 배포한 펌웨어와 Pi 설정은 운전 잠금 상태입니다.

## 1. Pi에 설치

ZIP의 최상위 파일·폴더들을 `~/mobot-device`에 풉니다. 기존 폴더에 덮어쓸 때 **기존 config와 journal DB는 보존**하세요. 새 패키지에는 인증키나 운영 DB가 없습니다.

```bash
cd ~/mobot-device
python3 -m venv .venv
.venv/bin/python -m pip install -r raspi_line/requirements.txt
.venv/bin/python -m raspi_line --init-config --config raspi_line/config.json
ls -l /dev/serial/by-id/
```

설정 파일은 한 번만 생성합니다. 두 번째 생성은 기존 설정을 덮어쓰지 않고 거절합니다. 생성된 제어 키와 이벤트 키는 서로 다르며 외부에 공유하지 않습니다. 파일을 열어 실제 USB 식별 경로를 `serial_port`에 넣으세요. `/dev/ttyACM0`보다 `/dev/serial/by-id/...`를 권장합니다. USB 권한이 없으면 Pi 사용자를 dialout 그룹에 추가하고 다시 로그인합니다.

## 2. UNO 업로드와 실측 프로필

Arduino IDE에서 MobotLineV1.ino를 열면 같은 폴더의 hardware_profile.h를 함께 사용합니다. 보드는 Arduino Uno, 포트는 실제 UNO 포트를 선택합니다. 기존 RobotWorkSimulation.ino가 아닌 새 펌웨어를 업로드합니다. 업로드 중 Pi 서비스와 시리얼 모니터가 포트를 동시에 열면 안 됩니다.

`hardware_profile.h`에서 다음 값을 실측·검토한 뒤 설정합니다. 현재 0은 권장 운전값이 아니라 미설정 표시입니다.

| 값 | 의미 |
|---|---|
| PUSH_MS / RETRACT_MS | 액추에이터 전진 / 복귀 시간 |
| REVERSAL_MS | 전진 정지 후 역방향 전환 대기 |
| IR1_DELAY_MS / IR2_DELAY_MS | 해당 IR 검출 후 정차 지연. 즉시 정차는 0 |
| TRAVEL_LIMIT_MS | IR 도착을 확인하지 못하면 정지할 최대 이동 시간 |
| HEARTBEAT_LIMIT_MS | Pi의 H 전문이 끊기면 정지·고장 유지할 시간 |
| SENSOR_FILTER_MS | IR 안정 상태 확인 시간 |
| STEP_HALF_PERIOD_US | STEP 반주기. 기존 코드의 80us는 실측 승인값으로 자동 채택하지 않음 |

값과 정방향·센서 극성을 확인한 다음 TIMING_PROFILE_APPROVED를 true로 바꾸고 다시 업로드합니다. WIRING_VERIFIED는 확인받은 배선으로 true입니다. 배선이 달라지면 먼저 수정하세요. 배출 완료의 근거는 TIMER로 기록하며 실제 배출 센서가 있다고 표시하지 않습니다. 기본 코드는 스테퍼 DIR=HIGH, IR 검출=LOW를 기존 코드에서 이어받았습니다.

Pi의 config.json에도 실측 후 `hardware_profile_approved=true`와 다음 초 단위 감시값을 설정합니다.

- heartbeat_interval_seconds: Pi→UNO 생존 신호 간격
- serial_response_timeout_seconds: UNO 회신이 끊겼다고 판단할 시간
- controller_timeout_seconds: 메인 서버가 Pi 상태를 조회하지 않을 때 운전을 보류할 시간

heartbeat 간격은 UNO의 HEARTBEAT_LIMIT_MS보다 짧고, serial_response_timeout_seconds보다 짧아야 합니다. 시리얼·메인 서버 단절을 실제로 시험해 값을 확정해야 합니다. 메인 제어권을 잃으면 Pi가 heartbeat를 중단하고 UNO watchdog이 출력을 정지합니다. 정지 출력은 물리 속도·잔류 이동의 실측과 별개입니다.

## 3. 서로 다른 네트워크에서 연결

기존 공개 ngrok 장비 API는 사용하지 않습니다. 내부 VPN 경로의 HTTPS 또는 암호화된 SSH 터널을 사용합니다. 두 방향 통신이 필요합니다.

- 메인 PC → Pi의 `/device/v1` 명령·조회
- Pi → 메인 PC의 `/api/v1/device-events`

직접 내부 HTTPS로 연결할 때 Pi의 `listen_host`를 VPN 주소로, `tls_certificate`·`tls_private_key`를 실제 인증서 파일로 설정합니다. 메인 PC가 인증서의 발급자를 신뢰해야 합니다. VPN 주소 대역이 기본 사설망 목록 밖이면 Pi와 메인 서버의 internal_networks에 해당 VPN 대역을 명시적으로 추가해야 합니다. Pi의 `server_url`도 내부 메인 서버의 HTTPS 주소로 지정합니다. 자체 서명 인증서를 무조건 무시하는 옵션은 제공하지 않습니다.

SSH로 Pi에 접속할 수 있다면 메인 PC PowerShell에서 다음과 같이 두 방향 터널을 열 수도 있습니다. 실제 사용자와 Pi의 접속 가능한 주소로 바꿉니다. 이 주소로 SSH 접속이 가능하도록 VPN 등의 경로가 먼저 있어야 합니다. ngrok HTTP 주소는 SSH 주소가 아닙니다.

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -L 18101:127.0.0.1:8101 -R 18000:127.0.0.1:8000 pi@PI_SSH_ADDRESS
```

이 경우 Pi는 `listen_host=127.0.0.1`, `listen_port=8101`, `server_url=http://127.0.0.1:18000`으로 설정합니다. 메인 PC의 LINE 주소는 `http://127.0.0.1:18101`입니다. 터널은 운전 중 계속 유지해야 합니다.

## 4. 메인 PC 설정

Pi의 config.json을 안전한 경로로 메인 PC에 복사한 뒤 프로젝트 루트에서 실행합니다. 이 파일은 인증키가 있으므로 저장소에 올리지 않습니다.

```powershell
.venv\Scripts\python.exe scripts/configure_live_line.py --pi-config C:\PRIVATE\pi-config.json --line-url http://127.0.0.1:18101 --cameras 0 1 2 3
```

카메라 번호 0·1·2·3은 사용법 예시입니다. 실제 네 장치와 역할을 대조해서 지정하세요. 카메라 설정을 지금 바꾸지 않으려면 `--cameras`와 숫자를 생략합니다. 설정 도구는 LINE 주소와 두 인증키만 연결하고 기존 ARM·AGV 설정을 보존합니다. 기존 파일이 있으면 같은 위치에 백업합니다. 변경 후 메인 서버를 재시작합니다. 실제 장비 주소를 알 수 없어 현재 운영 설정에 가짜 Pi 주소를 넣지는 않았습니다.

카메라 1 QR과 카메라 4 실시간 영상은 기존 메인 PC OpenCV 경로를 사용합니다. 카메라 2 좌표·카메라 3 부품 판정은 승인된 비전 모듈·보정 프로필이 필요합니다. 카메라를 연결한 것만으로 로봇팔 보정·YOLO 학습이 완료되지는 않습니다.

## 5. 실행과 확인

```bash
cd ~/mobot-device
.venv/bin/python -m raspi_line --config raspi_line/config.json
```

초기에는 상태 조회로 LINE의 online·boot_id를 확인합니다. 운전 프로필이 미설정이면 UNKNOWN/오프라인으로 보이는 것이 정상이고 모터를 구동하지 않습니다. 전체 공정은 WPF에서 제작 수락→준비 상태 확인→자동 운전 시작 순서로 실행합니다. LINE 단독 연결만으로 ARM·AGV 준비를 우회하지 않습니다.

자동 시작 서비스 예시는 `mobot-line.service.example`입니다. 실제 사용자·경로를 수정하고 초기 점검 후 설치합니다. 명령 기록 DB는 동일 경로에 유지합니다. 한 DB·시리얼 포트에 두 프로그램이 동시에 제어권을 갖지 못하도록 잠급니다.

## 검증 범위

USB 대역을 통한 명령·완료 증거·중복·순번 조회·정지/해제·재부팅·단절 검사를 수행했습니다. Arduino CLI의 공식 Uno 보드 도구로 잠금 프로필과 별도 임시 운전 분기 빌드를 검사합니다. 임시 빌드 값은 시험 전용이며 패키지에는 포함하지 않습니다. 실제 Pi·UNO·모터·센서·카메라를 연결한 실물 시험은 아직 수행하지 않았습니다.

기준 문서는 GitHub main의 공통 통신 규약·OpenAPI·하드웨어 공정·팀 규약입니다. 문서는 온라인으로 대조했으며 이 패키지에 내려받지 않았습니다.
