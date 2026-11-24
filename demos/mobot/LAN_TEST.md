# 내부망 TCP/IP(HTTP) 시험

WPF 상단 **컨베이어 작업 시작**과 QR 성공 대기형 전체 회차는 [CONVEYOR_SIMULATION.md](CONVEYOR_SIMULATION.md)를 따른다. 최신 Pi ZIP과 `RobotWorkSimulation` UNO 펌웨어 재업로드가 모두 필요하다. 아래 1초 제한·재업로드 불필요 설명은 기존 단독 수동 시험 기준이다. 전체 회차의 액추에이터 전진·복귀는 기존 펌웨어대로 각 10초다.

메인 PC의 Flask·WPF·QR ROI와 Pi·UNO 시험 제어를 같은 내부망에서 연결한다. 기존 `테스트 서버/Server`의 별도 HTTPServer(5000)와 가짜 공정 이벤트는 사용하지 않는다. 메인 Flask(8000)에 Pi가 먼저 접속해 명령을 받아 UNO로 전달한다. 원시 TCP 소켓이나 정식 `/device/v1` 운전으로 전환한 것은 아니다.

## 메인 PC

기존 Flask 서버를 Ctrl+C로 종료하고 실행한다. 확인 시점 메인 PC의 **Wi-Fi IP는 192.168.1.51**, Pi는 192.168.1.41이며 둘 다 192.168.1.0/24이다. PC의 유선 주소 192.168.0.154 대신 Wi-Fi 주소를 사용한다.  원본 시험 코드의 192.168.0.175와 다르다. IP가 바뀌면 실제 주소로 수정한다.

```powershell
cd C:\Users\moble\Desktop\mobot
.\Start-MOBOT-LAN-Test.cmd http://192.168.1.51:8000
```

기본 인자 없이 실행하면 내부 IP가 하나일 때 자동 선택한다. 여러 주소면 선택을 요구하고 종료하므로 위처럼 명시한다. WPF는 기존처럼 `http://127.0.0.1:8000`에 연결한다. ngrok은 필요 없다. QR ROI·시험 DB·액추에이터 최대 1초·컨베이어 최대 0.5초·정지 및 중복 방지는 유지한다. LAN 시험 중 자동 주문 공정은 차단한다.

확인 시점 이더넷 프로필은 **Public**이므로 Private 전용 방화벽 규칙은 적용되지 않는다. 접속이 막히면 **관리자 PowerShell**에서 아래 규칙을 추가한다. 네트워크 프로필을 바꾸지 않고, 현재 PC 사설 주소와 같은 192.168.1.x 대역의 8000 포트로만 제한한다. IP/서브넷이 바뀌면 규칙도 실제 값으로 수정한다. 공유기 포트포워딩은 하지 않는다. 이번 작업에서는 방화벽 설정 자체는 변경하지 않았다.

```powershell
New-NetFirewallRule -DisplayName "MOBOT WiFi TEST 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -LocalAddress 192.168.1.51 -RemoteAddress 192.168.1.0/24 -Profile Any
```

## 라즈베리파이

생성된 `releases/mobot-lan-test.zip`을 Pi의 새 폴더 `~/mobot-lan-test`에 푼다. 기존 ngrok 프로그램·시리얼 모니터 등 같은 USB 포트를 사용하는 프로그램은 Ctrl+C로 종료한다. ZIP의 `wireless_pi/config.json`에 실제 내부 주소와 기존 시험 인증키가 들어 있다. ngrok용 예전 config.json으로 덮어쓰지 않는다.

```bash
cd ~/mobot-lan-test
python3 -m venv .venv
.venv/bin/python -m pip install -r wireless_pi/requirements.txt
chmod 600 wireless_pi/config.json

# 인증·HTTP 왕복만 확인: USB에 접근하거나 동작 명령을 생성하지 않음
.venv/bin/python test_lan_connection.py --config wireless_pi/config.json

# USB 없이 연결 상태만 WPF에 표시 (10분 후 종료)
.venv/bin/python -m wireless_pi --config wireless_pi/config.json --network-only

# 위 시험을 Ctrl+C로 종료한 뒤 실제 UNO 연결
.venv/bin/python -m wireless_pi --config wireless_pi/config.json
```

UNO 포트가 여러 개면 `wireless_pi/config.json`의 `serial_port`를 `/dev/ttyUSB0`처럼 지정한다. 하나면 `AUTO`를 유지할 수 있다. 기존 j/k 1초 펌웨어와 MobotWirelessBench 1초·10초 버전을 지원한다. 10초 버전에도 1000ms만 전송하므로 UNO 재업로드는 필요 없다. 서버·WPF를 재시작하고 Pi 코드는 최신 ZIP으로 교체한다. 컨베이어 단독 0.5초는 MobotWirelessBench에서만 지원한다.

`test_lan_connection.py --server http://192.168.1.51:8000 --config wireless_pi/config.json`처럼 기존 `--server` 인자도 사용할 수 있다. 인증된 진단 성공은 실물 동작 성공을 뜻하지 않는다. 실제 모터는 WPF의 **무선 시험** 탭에서 버튼을 눌렀을 때만 움직인다.

## 연결과 규약의 구분

- 메인 PC는 유선, Pi는 Wi-Fi여도 서로 접근 가능한 LAN이면 된다. 서로 다른 네트워크라면 경로/VPN 없이 내부 IP로 연결되지 않는다.
- `LAN_HTTP_TEST`는 숫자 사설 IP 또는 루프백 HTTP 주소만 허용한다. 사용자 요청의 임시 내부망 시험 예외이며, HTTPS를 요구하는 정식 규약 준수 완료로 보고하지 않는다. 인증 토큰은 유지되지만 HTTP는 암호화되지 않는다.
- 공통 `/api/v1/device-events`와 DTO 검사를 완화하지 않는다. 테스트의 `body.event_type=TEST_PING`은 공통 이벤트가 아니다. 진단은 `/wireless-test/v1/probe`, 실제 시험 명령/결과는 기존 `/wireless-test/v1/poll`로 분리한다.
- 진단 probe는 공정·장비 회차를 바꾸지 않아 실제 Pi가 실행 중이어도 부팅 ID를 덮어쓰거나 명령을 소비하지 않는다.
- ngrok으로 돌아가려면 Pi LAN 클라이언트와 서버를 종료한 뒤 `Start-MOBOT-Ngrok-Test.cmd`를 실행한다. 해당 ZIP과 기존 HTTPS 설정을 사용한다. 두 모드의 서버를 같은 8000 포트에서 동시에 실행하지 않는다.
- 실제 IP/인증키가 들어 있는 ZIP·config.json·DB는 공개 저장소에 올리지 않는다.
