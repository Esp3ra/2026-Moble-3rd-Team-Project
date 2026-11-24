# WPF 컨베이어 전체 회차 시험

## 적용 구성

메인 PC Flask(8000)·WPF·CAM1 QR ROI → LAN 또는 ngrok HTTP 폴링 → Pi `wireless_pi` → USB(115200) → UNO `RobotWorkSimulation`이다. 기존 시험 인증키와 주소를 유지한다. 정식 `/device/v1` 공정과 별개인 사용자 승인 시험 기능이며, 주문 배정·재고 변경·실제 로봇팔·YOLO·AGV 운행을 실행하지 않는다.

새 기능은 **Pi 코드 교체와 UNO 펌웨어 재업로드가 모두 필요**하다. 서버와 WPF도 재시작한다. 이전 `READY UNO_SIM_V1`은 수동 1초 이동만 허용하고 전체 회차 시작은 차단한다.

## 설치

1. Pi에서 기존 시험 프로그램을 Ctrl+C로 종료한다. 같은 USB 포트를 다른 프로그램에서 열지 않는다.
2. Arduino IDE에서 ZIP의 `firmware/RobotWorkSimulation/RobotWorkSimulation.ino` 또는 메인 PC의 `pallet_system_uno_sim/firmware/RobotWorkSimulation/RobotWorkSimulation.ino`를 UNO에 업로드한다. 연결 상태는 `READY UNO_SIM_QR_V2`로 식별한다.
3. LAN은 최신 `releases/mobot-lan-test.zip`, ngrok은 최신 `releases/mobot-ngrok-test.zip`을 Pi의 새 폴더에 푼다. ZIP에 해당 모드의 기존 주소·인증키가 포함되어 있으므로 다른 모드의 설정으로 덮어쓰지 않는다. ZIP은 공개 저장소에 올리지 않는다.
4. Pi에서 압축을 푼 폴더로 이동해 실행한다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r wireless_pi/requirements.txt
chmod 600 wireless_pi/config.json
.venv/bin/python -m wireless_pi --config wireless_pi/config.json
```

USB 장치가 여러 개면 `wireless_pi/config.json`의 `serial_port`를 실제 `/dev/ttyUSB0` 등으로 지정한다. Pi 시험 프로그램은 기본 10분 후 종료한다. 최대 1시간이 필요하면 `--seconds 3600`을 추가한다.

5. 메인 PC에서 기존 시험 서버를 종료하고 사용하는 모드에 맞는 시작 파일을 실행한다. LAN 현재 설정은 `Start-MOBOT-LAN-Test.cmd http://192.168.1.51:8000`, ngrok은 `Start-MOBOT-Ngrok-Test.cmd`이다. WPF는 `http://127.0.0.1:8000`에 연결한다. 이전 독립 `pallet_system_uno_sim/app.py`(8080)와 동시에 운전하지 않는다. 해당 독립 웹 제어기는 이번 WPF 연동 대상이 아니다.
6. CAM1 전체 영상과 CAM3 표시 자리의 ROI 영상을 확인하고 필요하면 QR ROI 창에서 영역을 조절한다. 실제 IR1·IR2가 모두 비감지 상태일 때 상단 **컨베이어 작업 시작**을 누른다.

## 진행 표시와 순서

WPF는 1초마다 최신 Pi/UNO 진행 상태를 조회한다. 상단 여섯 칸에서 현재 단계는 파란색, 완료 단계는 녹색으로 표시한다. 상세 줄에는 마지막 시리얼 메시지·시각·인식 팔레트·UNO의 QR 확인 여부를 표시한다.

| 표시 | 실제 동작 및 완료 조건 |
|---|---|
| 배출 | 액추에이터 전진 10초, 방향 전환 정지 0.5초 |
| IR1 정차 | 액추에이터 복귀 10초와 벨트 이송 동시 시작, IR1 감지 후 기존 5350ms 이송 후 정차 |
| QR 확인 | 벨트 정차 유지. 정차 후 촬영한 CAM1 ROI에서 `PALLET-01`~`PALLET-08` 하나를 인식해야 진행 |
| 조립 모의 | 서버의 QR 성공 알림을 UNO가 확인한 시점부터 3초 대기. 실제 로봇팔 동작 없음 |
| IR2 정차 | 벨트 재출발, IR2 감지 후 기존 1750ms 이송 후 정차 |
| 완료 | 벨트 정차와 액추에이터 복귀 타이머 종료 확인. 실제 액추에이터 끝 위치 센서 검증은 아님 |

원본 QR 영상에서 인식 실패 시 CLAHE 명암 보정을 적용하는 기존 판독기를 사용한다. 미인식·잘못된 형식·다중 QR·정차 전 영상·오래된 영상은 다음 단계로 보내지 않는다. 이 시험의 QR 확인은 주문 DB 바인딩과 별개이며, 판독 결과는 시험 기록에만 보관한다.

시리얼은 `a <회차번호>`로 시작하고 `EV <회차번호> <진행메시지>`로 회신한다. QR 대기 중 서버가 현재 회차의 QR을 확인하면 Pi가 `q <같은 회차번호>`를 보낸다. `QR:RECOGNIZED_ROBOT_WAIT`가 모의 조립 시작 증거이며 `CYCLE_DONE:READY_A`가 회차 완료 증거다. 다른 회차의 회신은 현재 회차를 진행시키지 않는다.

## 정지와 재실행

상단 **전체 공정 정지**는 UNO에 `s`를 보내 벨트와 액추에이터 출력을 정지한다. 단절·응답 지연·Pi/UNO 재시작에서는 회차를 자동 재실행하지 않는다. 결과 미확인 상태는 현장을 확인하고 전체 정지 응답을 받은 뒤 새 작업으로 시작한다. 시험 모드의 정지 해제 버튼은 사용하지 않는다.

Pi는 서버와 1.5초 이상 통신하지 못하면 정지를 요청한다. 전체 회차 완료 회신 제한은 약 122초이며 QR을 계속 못 읽어도 무제한 자동 운전하지 않는다. 각 벨트 구간은 기존 30초 이동 제한을 유지한다. 소프트웨어 정지 요청은 물리 비상정지나 끝 위치 확인을 대체하지 않는다.

무선 시험 탭의 수동 액추에이터 전진·복귀는 **1초**를 유지한다. 전체 회차 펌웨어의 기존 **전진·복귀 각 10초**와 구분한다. 원래 배선 STEP=5, DIR=6, ENA=9, IN1=7, IN2=8, IR1=4, IR2=2를 유지했다.

## 검증 범위

실제 `.ino` 소스에 대한 호스트 상태 전이 검사, Arduino Uno 컴파일, 서버·Pi 모의 시리얼/QR 검사 및 WPF 모의 HTTP 검사를 수행한다. 실제 장비에서의 이동 방향·정차 위치·네트워크 도달성·QR 인식은 현장에서 확인해야 한다. 이 문서의 설치만으로 실물 동작이 검증된 것은 아니다.
