# MOBOT 업무 서버와 구매 웹

메인 PC에서 Flask·SQLite와 카메라를 함께 실행합니다. WPF와 구매 웹은 /api/v1을 사용합니다. 서버가 Pi·로봇팔·AGV 연결 모듈의 /device/v1에 명령을 보내고, 각 모듈이 /api/v1/device-events로 완료를 보고합니다. WPF에서 수락한 주문은 수락 순으로 한 번에 한 회차씩 처리합니다.

- 관리자 앱은 저장소의 WPF/RobotFactory.sln에 있습니다.
- 현장 Pi 부팅 서비스와 USB 연결은 [LINE 배포 안내](../../devices/line_pi/README.md)를 따릅니다.
- UNO 펌웨어는 [RobotWorkSimulation.ino](../../firmware/RobotWorkSimulation/RobotWorkSimulation.ino)에 있습니다.
- 네 칸의 WPF 관제는 CAM1 QR ROI, CAM2 장착 위치 보정, CAM3 검사 영상, CAM4 전체 관제입니다. [QR ROI 설정](QR_ROI_INTEGRATION.md)

## 메인 PC 실행

이 폴더에서 프로젝트 밖에 가상환경을 준비합니다.

~~~powershell
python -m venv "$env:LOCALAPPDATA\MOBOT\.venv"
& "$env:LOCALAPPDATA\MOBOT\.venv\Scripts\python.exe" -m pip install -r requirements.txt
..\..\WPF\Start-MOBOT.cmd
~~~

구매 웹과 WPF 기본 주소는 http://127.0.0.1:8000입니다. 실제 DB와 카메라·장비 설정은 %LOCALAPPDATA%\MOBOT\instance에 둡니다. 서버는 같은 폴더의 mobot.sqlite3, factory-v1.json, qr-roi.json을 사용합니다. 기존 DB를 별도 위치에 둘 경우 MOBOT_DB에 파일 경로를 지정할 수 있습니다. factory-v1.example.json에는 예제값만 포함합니다.

장비마다 `devices.LINE`, `devices.ARM`, `devices.AGV`에 연결 모듈의 주소와 인증값을 설정합니다. SSH 전달이 필요한 장비는 Windows SSH 설정에 등록한 별칭을 해당 장비의 `ssh_tunnel_host`에 지정합니다. Start-MOBOT은 설정된 터널을 함께 준비합니다. 각 Pi의 장비 서비스는 배포 시 기동을 준비합니다.

현장 LINE·ARM·AGV, 카메라 4대와 승인된 비전 프로필이 준비되면 WPF에서 운전을 시작합니다. IR1 정차 후 CAM1 QR 배정을 저장하고 조립을 요청합니다. 조립 완료·대기 복귀 이후 정차 상태에서 검사하고, 합격 저장 뒤에만 IR2 이송을 시작합니다. IR2 정차 뒤 AGV가 인수·하역·복귀합니다.

물리적 검사 위치를 확정한 뒤 해당 카메라를 논리 CAM3에 배정하고 `inspection_camera`를 `cam3`으로 지정합니다. 실물 재작업 명령 형식이 확정될 때까지 결함이 난 회차는 정차 상태로 표시합니다. 별도 SQLite를 사용하는 모의 모드는 재작업 한 번과 새 검사 시도를 검증할 수 있습니다.

업무 서버에서 만든 LINE 배포 ZIP은 기본적으로 %LOCALAPPDATA%\MOBOT\releases에 저장합니다.

~~~powershell
& "$env:LOCALAPPDATA\MOBOT\.venv\Scripts\python.exe" -B scripts/package_line.py
~~~

## 검증

scripts/check_openapi.py는 GitHub 공통 계약을 메모리로 읽어 API 요청과 응답을 비교합니다. tests/의 서버 회귀 검증은 임시 DB와 모의 장치를 사용합니다. 팀 공통 기준은 [하드웨어 공정](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/HARDWARE_WORKFLOW.md)과 [프로젝트 트리](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/PROJECT_TREE.md)에 있습니다.
