# ngrok 무선 단독 제어 시험

이 코드는 사용자가 요청한 임시 시험용입니다. 공통 규약의 실물 운전과 분리하며, 주문·QR 배정·조립·검사·AGV 배송을 실행하지 않습니다. Pi가 ngrok HTTPS로 먼저 접속하므로 Pi의 외부 포트를 열 필요가 없습니다. 메인 PC의 ngrok http 8000은 현재 실행 중입니다.

## Pi에서 실행

ZIP을 새 폴더 `~/mobot-ngrok-test`에 풉니다. 폴더 안에 wireless_pi와 firmware가 있어야 합니다. 기존 raspi_agent·raspi_line 등 같은 USB를 여는 프로그램은 Ctrl+C로 종료하세요.

```bash
cd ~/mobot-ngrok-test
python3 -m venv .venv
.venv/bin/python -m pip install -r wireless_pi/requirements.txt
chmod 600 wireless_pi/config.json
.venv/bin/python -m wireless_pi --config wireless_pi/config.json
```

config.json에는 현재 ngrok 주소와 시험 전용 인증키가 들어 있습니다. 주소는 `https://YOUR_TUNNEL.ngrok-free.dev`입니다. 이 ZIP과 config.json은 본인 장비에만 전달하세요. 운영 DB·관리자 비밀번호는 포함하지 않습니다.

기본 실행 시간은 10분이며 종료할 때 정지 출력을 요청합니다. Ctrl+C로 먼저 끝낼 수 있습니다. 계속 시험하려면 같은 명령을 다시 실행합니다. USB가 아직 없어도 Pi의 무선 접속 상태가 WPF에 표시됩니다. 아두이노 없이 통신만 시험하려면 마지막 명령 뒤에 `--network-only`를 붙입니다. 이 경우 Pi 통신 확인만 가능하며 모터 동작은 불가능합니다.

## WPF에서 확인·제어

관리자 로그인 → **무선 시험** 탭을 엽니다. Pi 연결됨·UNO 상태·최근 시리얼 회신이 표시됩니다.

- Pi 통신 확인: Pi의 HTTPS 왕복을 확인합니다. UNO 미연결이면 그 사실도 결과에 표시합니다.
- 컨베이어 0.5초: 아래 전용 시험 펌웨어에서만 지원합니다.
- 액추에이터 전진 1초 / 복귀 1초: 서버와 Pi는 항상 1000ms 명령만 전송합니다.
- 정지: Pi를 통해 UNO에 정지 출력을 요청합니다. 상단 전체 공정 정지 버튼도 이 시험이 활성화된 동안 같은 정지 요청을 보냅니다.

버튼을 누를 때만 실제 동작을 보냅니다. 같은 요청 번호의 동작은 중복 실행하지 않습니다. 유효시간이 지난 명령이나 결과 미확인 동작을 자동 재시도하지 않습니다. 최근 명령의 PENDING/SENT는 접수·전달 상태이며, COMPLETED는 UNO의 회신을 받은 상태입니다. 타이머 종료는 실제 위치 센서 검증을 뜻하지 않습니다.

## QR 독립 판독

메인 PC에 연결한 CAM 01에 팔레트 QR을 보여주고 WPF **무선 시험 → CAM 01 · QR 판독**을 누릅니다. `PALLET-01~08` 인식 결과, 미인식·잘못된 QR·다중 QR·영상 없음을 표시합니다. Pi나 UNO가 없어도 서버와 CAM 01만 준비되면 가능합니다. 주문·회차·QR 배정·모터 명령은 생성하지 않습니다. 영상은 카메라 탭에서 확인합니다. 자동 주기 판독 대신 버튼을 누를 때 한 번 판독합니다.

## 1초 시험 버전 적용

WPF 상단에서 배출 → IR1 → QR 성공 → 3초 모의 조립 → IR2 전체 회차를 실행하려면 동봉된 **[CONVEYOR_SIMULATION.md](../CONVEYOR_SIMULATION.md)**를 따른다. 이 기능에는 새 `RobotWorkSimulation` 펌웨어(`UNO_SIM_QR_V2`)와 최신 Pi 코드가 모두 필요하다. 아래의 재업로드 불필요 안내는 기존 수동 1초 시험에만 해당한다.

서버와 WPF를 재시작하고 Pi의 기존 시험 프로그램을 종료한 뒤 최신 `wireless_pi` 코드를 교체합니다. 서버 주소·인증키·시리얼 포트 설정은 유지합니다. 실행 명령은 같습니다.

기존 RobotWorkSimulation의 j/k 1초 펌웨어와 MobotWirelessBench의 1초·10초 지원 버전을 모두 인식합니다. 10초 지원 펌웨어에도 1000ms만 보내므로 해당 UNO를 다시 업로드할 필요가 없습니다. 새로 업로드할 시험 펌웨어는 최대 1000ms로 제한했습니다. 컨베이어 단독 0.5초는 MobotWirelessBench에서만 지원합니다.

1초 구동 중 정지·통신 감시는 유지하며, 단절 시 더 일찍 중단될 수 있습니다. 타이머 종료는 실제 끝 위치 센서 확인이 아닙니다.

## UNO 펌웨어를 모르는 경우

Pi가 부팅 메시지로 자동 식별합니다.

| 감지 | 가능한 시험 |
|---|---|
| READY UNO_SIM_QR_V2 | 전체 회차·QR 성공 대기·진행 메시지·수동 j/k 1초·정지 지원. 전체 회차 액추에이터 전진·복귀는 각 10초 |
| READY UNO_SIM_V1 또는 TIMING 응답 | 액추에이터 j/k 1초·정지·응답 확인 지원. 컨베이어 단독은 불가 |
| READY MOBOT_WIRELESS_TEST | 액추에이터 1초·컨베이어 0.5초·정지 지원 |
| READY MOBOT_WIRELESS_TEST_10S | 호환 버전. 현재 서버·Pi는 1초 명령만 전송 |
| READY V1 또는 그 밖의 메시지 | 지원 여부 미확인. 모터 버튼 차단. Pi 인터넷 통신 확인은 가능 |

모든 단독 동작을 시험하려면 **firmware/MobotWirelessBench/MobotWirelessBench.ino**를 Arduino IDE에서 열어 Uno에 업로드한 다음 Pi에 USB로 연결합니다. 업로드할 때 Pi 시험 프로그램은 종료하세요. 기존 MobotLineV1의 공정용 펌웨어와 별도 파일입니다.

배선은 기존에 확인한 STEP=5, DIR=6, ENA=9, IN1=7, IN2=8, IR1=4, IR2=2를 사용합니다. 전용 시험 코드의 컨베이어는 IR1/IR2가 감지되면 0.5초 전이라도 멈춥니다. 액추에이터 전진↔복귀 사이에는 0.5초 정지 간격을 요구합니다. STEP 반주기는 기존 시험 코드의 80us입니다. 이 값들은 본 시험의 제한값이며 생산 공정의 실측 프로필로 자동 적용하지 않습니다.

## USB 포트 선택

config.json의 serial_port=AUTO는 USB 시리얼 장치가 한 개일 때만 자동 선택합니다. 여러 개라면 다음으로 확인하고 실제 경로를 serial_port에 넣습니다.

```bash
.venv/bin/python -m serial.tools.list_ports
ls -l /dev/serial/by-id/
```

USB 권한 오류면 Pi 사용자의 dialout 권한과 재로그인을 확인합니다. 같은 포트를 다른 프로그램과 함께 열지 않습니다.

## 메인 PC 설정과 기록

현재 실행 DB 옆의 wireless-test.json으로 활성화합니다. 장비 보고만 `/wireless-test/v1/poll`에서 시험 전용 인증키로 받습니다. WPF 조작은 내부 관리자 세션으로만 허용합니다. 기존 `/api/v1` 주문·공정 및 `/device/v1`의 외부 접근 정책은 유지합니다.

무선 시험 활성화 중에는 주문 자동 운전을 차단합니다. 시험 기록은 별도 wireless-test.sqlite3, Pi 기록은 wireless-test-journal.sqlite3에 남습니다. 끄려면 메인 PC의 해당 wireless-test.json에서 enabled를 false로 바꾸고 서버를 재시작합니다. 이후 규약 기반 운전으로 돌아갈 때는 Pi 시험 프로그램을 종료하고 원래 펌웨어·raspi_line을 사용합니다.

소프트웨어 전달·중복·단절·펌웨어 식별 검사를 수행하고 전용 Uno 펌웨어를 컴파일합니다. 실제 사용자 장비의 이동·방향·센서 동작은 현장에서 버튼을 눌러 확인해야 합니다. 설치만으로 실물 검증이 완료된 것으로 표시하지 않습니다.

## 내부망 HTTP 시험

내부망용 ZIP은 `mobot-lan-test.zip`이며 자세한 절차는 동봉된 `LAN_TEST.md`를 따릅니다. 메인 PC의 LAN 시험 서버(8000)에 접속하며 ngrok이 필요 없습니다. `transport=LAN_HTTP_TEST`와 숫자 사설 IP를 사용합니다. 명령·1초 시험 제어·실행 모듈은 동일하지만 Pi 코드와 해당 ZIP의 설정 파일은 업데이트해야 합니다. 이전 HTTPS 설정으로 덮어쓰지 마세요.
