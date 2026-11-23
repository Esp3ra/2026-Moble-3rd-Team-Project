# MOBOT 프로젝트 파일 트리

팀원이 기능을 맡았을 때 어느 장비의 어떤 파일에서 작업할지 찾는 안내다. 첫 통합은 서버·웹의 `demos/mobot/`, 관리자 앱의 `WPF/`, UNO의 `firmware/`, AGV의 `AGV/` 경로를 그대로 사용한다. 두 Raspberry Pi에 배포할 새 제어 코드는 `devices/` 아래에서 장비별로 나눈다.

## 최종 개발 트리

2026-11-23 기준이다. 공통 문서는 `main`, 제품 코드와 예약 폴더는 `Alpha`에서 관리한다. 아래는 두 브랜치에서 팀원이 사용하는 주요 경로다. **예약** 폴더는 빈 폴더를 Git으로 공유하기 위해 `.gitkeep` 하나를 둔 자리다. **뼈대**에는 구현할 기능만 명시했다.

```text
3차 팀 프로젝트/
├─ demos/mobot/                             메인 Windows PC
│  ├─ templates/                            구매 화면 HTML (예약)
│  ├─ static/                               구매 화면 JS·CSS (예약)
│  ├─ factory_v1/                           업무 API·SQLite·공정 엔진 (예약)
│  ├─ assembly_vision/
│  │  ├─ plugin.pyi                         CAM2·CAM3 플러그인 함수 계약
│  │  ├─ calibration.py                     CAM2 집기·장착 좌표 보정 뼈대
│  │  ├─ inspection.py                      CAM3 QR·부품 판독 뼈대
│  │  └─ training/LABELS.md                 학습 라벨과 모델 매핑
│  └─ tests/                                서버 단위 검증 (예약)
├─ WPF/
│  ├─ RobotFactory.sln                      관리자 앱 솔루션
│  └─ RobotFactory.Admin/
│     ├─ RobotFactory.Admin.csproj
│     ├─ App.xaml · App.xaml.cs
│     ├─ MainWindow.xaml · MainWindow.xaml.cs
│     ├─ Views/                             로그인·관제 화면
│     ├─ ViewModels/                        화면 표시 상태
│     ├─ Resources/                         공통 테마
│     └─ Services/                          업무 서버 API 클라이언트 (예약)
├─ devices/
│  ├─ line_pi/                              현장 Raspberry Pi
│  │  ├─ adapter.py                         LINE 명령·상태·이벤트 뼈대
│  │  └─ serial_link.py                     UNO USB 시리얼 송수신 뼈대
│  └─ mycobot_pi/                           myCobot 280 Pi
│     ├─ adapter.py                         ARM 명령·상태·이벤트 뼈대
│     └─ assembly.py                        부품 집기·장착·대기 복귀 뼈대
├─ firmware/
│  ├─ RobotWorkSimulation/                  UNO 액추에이터·컨베이어·IR 펌웨어 (예약)
│  └─ tests/                                UNO 검증 (예약)
├─ AGV/
│  ├─ AgvRouteTest/                         ESP32 주행·정차·지게 펌웨어 (예약)
│  └─ tests/                                AGV 검증 (예약)
├─ tests/integration/SCENARIOS.md           전체 공정 시험 계획
├─ docs/
│  ├─ PROJECT_TREE.md                       파트별 작업 위치
│  ├─ HARDWARE_WORKFLOW.md                  장비 구성과 12단계 공정
│  ├─ 프로젝트_전체규약_팀답변_공유본.md     팀 운영 기준
│  └─ 통신규약/
│     ├─ 공통_통신규약과_DTO.md
│     └─ openapi.yaml                       공통 API·DTO
├─ scripts/                                 저장소 정책 검사와 해당 테스트
└─ .github/                                 협업 설정과 CI
```

저장소 안내는 루트 `README.md`, 협업 규칙은 `AGENTS.md`에 있다. 개발 트리에는 주요 소스와 기준 문서를 표시했다.

## 파일별 담당 기능

아래에서 **Alpha**는 팀원이 내려받을 수 있는 뼈대, **PR**은 아직 개발 브랜치에 있는 구현 경로다. 경로 앞에 붙는 기준 폴더는 각 표 위에 적었다. [Alpha 개발 트리](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/tree/Alpha)

### 구매 웹 · 업무 서버 · SQLite

메인 Windows PC의 `demos/mobot/` 기준이다. Alpha에는 웹·서버용 폴더가 예약돼 있다. 아래 파일은 [서버·웹 PR #13](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/13)에 있다.

| 폴더 또는 파일 | 여기에 넣을 부분 |
|---|---|
| `templates/layout.html`, `home.html` 등 | 공통 페이지 틀과 구매 웹 화면. `templates/order.html`과 같은 폴더의 `order-*.html`에서 부품 선택, 주소 입력, 주문 제출 화면을 구성한다. |
| `static/order.js`, `sections.js`, `site.css`, `images/` | 모두 `static/` 아래에 둔다. 각각 주문 화면 동작, 화면 구역 동작, 공통 스타일, 웹 이미지다. |
| `app.py`, `factory_v1/runtime.py` | Flask 앱 진입점과 v1 초기화. API 등록, DB·장비 클라이언트·비전·공정 엔진의 시작을 연결한다. |
| `factory-v1.example.json` | 장비 연결과 카메라·비전 설정의 공유 예제. 실제 환경값은 실행 PC에 둔다. |
| `factory_v1/api.py` | 소비자 주문·조회·취소, 관리자 로그인·권한·수락, 설비 제어, 재시도와 이력 조회의 HTTP 입출력을 맡는다. |
| `factory_v1/common.py`, `schema.sql` | 둘 다 `factory_v1/` 아래에 둔다. 전자는 권한·검증·트랜잭션·중복 요청 처리 등 공통 함수, 후자는 주문·재고·팔레트·회차·명령·이력을 저장하는 SQLite 스키마다. |
| `factory_v1/contracts.py` | 장치 상태·명령·이벤트·작업 좌표·검사 DTO를 검증한다. 검사 관측값을 주문 사양과 대조해 판정·목적지를 계산한다. |
| `factory_v1/engine.py` | 수락순 대기열에서 한 회차를 시작하고 QR 배정, 공정 단계 전이, 장치 명령·이벤트 대조, 검사 및 복귀 완료를 기록한다. |
| `factory_v1/devices.py` | 메인 PC에서 LINE·ARM·AGV로 명령을 보내고 상태·결과·이벤트를 조회한다. 같은 인터페이스의 모의 장치도 여기서 연결한다. |
| `factory_v1/vision.py` | 메인 PC가 카메라를 열고 프레임을 공급한다. CAM1 투입 QR, CAM2 위치 보정, CAM3 QR·완성품 검사, CAM4 실시간 관제의 진입점이다. |
| `tests/` | 서버·웹·공정 엔진의 반복 가능한 검증. PR의 `tests/test_factory_v1.py`는 v1 API·공정 검증 위치다. |

### 카메라 2 좌표 보정 · 카메라 3 완성품 검사

메인 PC의 [demos/mobot/assembly_vision/](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/tree/Alpha/demos/mobot/assembly_vision) 기준이다. 다음 표는 뼈대에 실제 알고리즘을 채울 위치를 정한다.

| Alpha 파일 | 구현할 함수와 결과 |
|---|---|
| `plugin.pyi` | 서버가 호출하는 두 함수의 키워드 인자와 반환 형식을 확인하는 타입 명세다. 실행 시 카메라 연결은 서버가 맡는다. |
| `calibration.py` | CAM2의 `locate_assembly`를 구현한다. 프레임과 보정 ID로 부품 집기·팔레트 장착 자세 `PoseSet`을 반환한다. 같은 모듈에서 `inspection.py`의 `inspect_product`도 가져와 공개한다. |
| `inspection.py` | CAM3의 `inspect_product`를 구현한다. 같은 프레임에서 읽은 팔레트 QR과 머리·왼팔·오른팔 관측값을 `VisionInspection`으로 반환한다. |
| `training/LABELS.md` | 학습 모델 클래스 ID와 머리 H1·H2, 왼팔 L1~L3, 오른팔 R1~R3의 대응 및 평가 기준을 기록한다. |

두 함수 구현이 끝나면 서버의 `vision_module`에 `assembly_vision.calibration`을 지정한다. 서버가 모듈 하나에서 두 함수를 찾으므로 현재 배치로 연결할 수 있다. 뼈대 단계의 카메라 2·3 실물 인식은 미구현 상태다.

### 관리자 WPF

솔루션은 [WPF/RobotFactory.sln](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/Alpha/WPF/RobotFactory.sln)이다. 표의 경로는 `WPF/RobotFactory.Admin/` 기준이다.

| 파일 | 여기에 넣을 부분 | 위치 |
|---|---|---|
| `RobotFactory.Admin.csproj`, `App.xaml`, `App.xaml.cs` | 프로젝트 빌드 설정, 시작 창과 공통 리소스 등록, 앱 시작·종료 처리 | Alpha |
| `MainWindow.xaml`, `MainWindow.xaml.cs` | 로그인·관제 화면을 담는 기본 창과 화면 상태 연결 | Alpha |
| `Views/LoginView.xaml` 및 `.xaml.cs` | 관리자 로그인 입력·결과와 화면 이벤트 | Alpha |
| `Views/DashboardView.xaml` 및 `.xaml.cs` | 주문 수락, 설비 정지·재시작, QR 일치 여부, 팔레트·카메라·검사 결과를 표시하는 관제 화면 | Alpha |
| `ViewModels/ScreenModels.cs`, `Resources/Theme.xaml` | 화면 바인딩용 모델·명령과 공통 스타일. HTTP 요청·응답 필드는 OpenAPI를 기준으로 한다. | Alpha |
| `Services/ApiConnection.cs`, `AuthApiClient.cs`, `OrderApiClient.cs` | 모두 `Services/` 아래에 둔다. 업무 서버 연결·인증 세션과 주문·수락 요청을 담당한다. | PR |
| `Services/FactoryApiClient.cs` | v1 공정 상태·제어 요청을 업무 API와 교환한다. 기존 `EquipmentApiClient.cs`, `AgvApiClient.cs`의 데모 호출은 통합할 때 v1 경로와 대조한다. | PR |

WPF 연동 변경은 같은 루트 솔루션을 사용하는 [PR #10](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/10)과 [PR #13](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/13)에 있다.

### 현장 Raspberry Pi · UNO · 로봇팔 · AGV

다음은 저장소 루트 기준이다. 같은 장치 명령이라도 서버의 공정 판단, 장치의 명령 수신, 모터·센서 처리가 실행되는 위치가 다르다.

| 실행 위치 | 폴더 또는 파일 | 여기에 넣을 부분 | 위치 |
|---|---|---|---|
| 현장 Pi | `devices/line_pi/adapter.py` | LINE 명령·상태·완료 이벤트. 서버 명령 ID와 UNO 세션·회차·순번을 연결한다. | Alpha |
| 현장 Pi | `devices/line_pi/serial_link.py` | UNO USB 시리얼 전문 송수신. 팔레트 배출과 벨트 출발을 분리하고 정차·센서 결과를 읽는다. | Alpha |
| Arduino UNO | `firmware/RobotWorkSimulation/RobotWorkSimulation.ino` | 타워 액추에이터, 벨트 구동, IR1·IR2 감지와 정차 보고. 펌웨어 단위 검증은 `firmware/tests/`가 맡는다. | 폴더는 Alpha, 소스는 [PR #11](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/11) |
| 로봇 내장 Pi | `devices/mycobot_pi/adapter.py` | ARM 명령·상태·이벤트. 주문 사양·좌표를 조립 절차에 전달하고 대기 자세 도착을 보고한다. | Alpha |
| 로봇 내장 Pi | `devices/mycobot_pi/assembly.py` | 그리퍼 열기·집기·이동·장착·대기 자세 복귀와 실제 작업 결과 확인 | Alpha |
| 메인 PC | `AGV/device_adapter.py`, `AGV/agv_client.py` | AGV 연결 모듈과 ESP32 통신 클라이언트. 서버 명령을 주행·지게 동작으로 연결하고 상태를 돌려준다. | [PR #12](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/12) |
| ESP32 AGV | `AGV/AgvRouteTest/AgvRouteTest.ino` | 라인트레이싱, 인수·검사 정차·목적지 이동, 지게 상승·하강과 복귀. AGV 통신·펌웨어 검증은 `AGV/tests/`가 맡는다. | 폴더는 Alpha, 소스는 [PR #12](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/12) |

### 공정 사이에서 넘겨야 하는 결과

| 단계 | 다음 동작을 시작하는 조건 |
|---|---|
| 구매 → 수락 → 투입 | 업무 서버가 주문을 저장하고 WPF 수락 순서대로 한 번에 한 장을 투입한다. |
| 배출 → 최초 이송 | CAM1 QR과 이번 회차의 주문·팔레트 배정 저장이 모두 성공해야 벨트를 출발시킨다. 판독은 2초 간격으로 총 3회까지 시도한다. 저장만 실패하면 같은 배정을 재시도한다. |
| IR1 정차 → 조립 → 이송 재개 | 정차 확인 후 CAM2가 작업 좌표를 구한다. 마지막 부품 장착 후 팔의 대기 자세 도착 이벤트를 받아 벨트를 재가동한다. |
| IR2 정차 → 인수 → 검사 | 벨트 끝 정차 확인 후 AGV가 인수한다. 적재한 AGV의 암실 도착 완료 보고를 받아 CAM3로 촬영한다. |
| 검사 → 하역 → 다음 주문 | 서버가 관측값을 대조해 정상은 서울 3·평택 4·수원 5번, 부품 누락은 6번, 그 밖의 불일치·판정 불가는 7번으로 보낸다. 판정 불가는 WPF에 즉시 표시한다. 하역 뒤 AGV가 2번에 정차하고 지게가 85°로 복귀하면 다음 주문을 투입한다. |

### 규약 · 시험 · 저장소 관리

| 파일 또는 폴더 | 용도 |
|---|---|
| [하드웨어 공정](HARDWARE_WORKFLOW.md), [팀 규약](프로젝트_전체규약_팀답변_공유본.md) | 장치별 역할, 12단계 순서와 예외 처리에 관한 팀 결정 |
| [공통 통신 규약](통신규약/공통_통신규약과_DTO.md), [OpenAPI](통신규약/openapi.yaml) | 공통 명령·이벤트의 의미, API 경로와 요청·응답 DTO의 원본 |
| [tests/integration/SCENARIOS.md](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/Alpha/tests/integration/SCENARIOS.md) | 정상 HUB 3개, 오류 구역 2개, 예외 5개의 통합 시험 절차와 기대 결과. 실물 시험 때 회차·근거 로그와 실제 결과를 남긴다. |
| [README.md](../README.md), [AGENTS.md](../AGENTS.md), `scripts/`, `.github/` | 저장소 시작 안내, 협업 규칙, 정책 검사와 GitHub Actions |
