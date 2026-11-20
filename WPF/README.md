# WPF 주문 관제

## 실행

Windows에서 .NET 10 SDK로 빌드하고 실행합니다.

```powershell
dotnet build .\RobotFactory.sln -c Release
dotnet run --project .\RobotFactory.Admin\RobotFactory.Admin.csproj
```

Visual Studio에서는 `RobotFactory.sln`을 열고 `RobotFactory.Admin`을 실행합니다.

## 서버 연결

서버 담당자에게 서버 주소와 관리자 계정을 전달받아 로그인 화면의 서버 주소, 관리자 아이디와 비밀번호 칸에 입력합니다. 원격 서버는 HTTPS 주소를 사용합니다. 같은 PC에서 서버를 실행하면 `http://127.0.0.1:8000`처럼 루프백 HTTP 주소를 사용할 수 있습니다. `MOBOT_API_URL` 환경 변수는 로그인 화면에 처음 표시할 기본 주소를 지정합니다.

로그인하면 계정과 권한을 표시하고 주문과 팔레트를 3초마다 갱신합니다. 주문을 선택하면 주문자, 부품, 목적지와 현재 상태를 확인할 수 있습니다. 대기 주문의 **주문 드롭**을 확인하면 서버에 취소를 요청합니다. 서버가 해당 주문의 상태를 `CANCELED`로 돌려주면 목록에서 제외합니다. 로그아웃이나 인증 만료 시 목록을 비우고 로그인 화면으로 돌아갑니다.

## 클라이언트가 기대하는 API

아래는 WPF 코드가 현재 기대하는 형식입니다. 팀 서버에서 지원하는 경로, 필드, 권한과 동작은 서버 담당자와 확인해야 합니다.

| 요청 | 클라이언트가 사용하는 내용 |
| --- | --- |
| `POST /api/auth/login` | `login_id`, `password`를 보내고 `token`, Unix 초 단위 `expires_at`, `account`를 받습니다. 계정에 `account_id`, `login_id`, `name`, `role_code`(`ADMIN` 또는 `OPERATOR`)가 필요합니다. |
| `GET /api/orders` | `orders` 배열의 `id`, `factory_status`와 주문 상세를 읽습니다. |
| `GET /api/pallets` | `pallets` 배열의 `pallet_id`, `current_status`, `cycle_count`, `updated_at`을 읽습니다. |
| `POST /api/orders/{id}/cancel` | 응답의 `order.id`와 `order.factory_status`를 확인합니다. 서버는 권한과 실제 주문 상태를 확인한 뒤 취소해야 합니다. |
| `POST /api/auth/logout` | 현재 세션의 로그아웃을 요청합니다. |

로그인 이후 요청에는 `Authorization: Bearer <token>` 헤더를 보냅니다. 앱에서 비밀번호와 세션 토큰을 파일에 저장하지 않습니다.

## 검증 범위

Windows에서 Release 빌드를 통과했습니다. PC 내부 모의 HTTP 서버로 로그인과 로그아웃, 주문·팔레트 조회, 자동 갱신과 선택 유지, 주문 취소, 인증 만료 처리를 확인했습니다. 팀 서버·DB·실계정 연동은 검증 대기입니다. 생산 승인, QR 배정, 검사·영상 수신, 설비 제어는 후속 연결 범위입니다.

## UNO·AGV 데모 화면

선택 주문과 팔레트로 UNO 데모를 시작하고 QR 판독 영상, 센서 상태, 회차 이벤트와 DB 배정 상태를 조회하는 화면을 추가했습니다. 시간 설정, 데모 정지, 배출 확인과 DB 재시도를 요청할 수 있습니다. UNO 배출 확인을 마친 주문은 AGV 패널에서 목적지를 선택해 배송 데모를 시작합니다. 목적지는 3=서울, 4=평택, 5=수원, 6=오류1, 7=오류2입니다. 이 번호 체계는 현재 데모 서버와 맞춰 사용해야 합니다.

이 화면을 사용하려면 다음 데모 API를 제공하는 서버가 필요합니다. 서버 담당자와 응답 형식과 권한을 확인한 뒤 연결하세요.

| 요청 | 용도 |
| --- | --- |
| `GET /api/equipment/state`, `GET /api/equipment/frame.jpg` | UNO 데모 상태와 QR 영상 조회 |
| `POST /api/equipment/start`, `heartbeat`, `stop`, `timings`, `confirm-ejection`, `database-retry` | 같은 equipment 경로 아래의 데모 시작·유지·정지·시간 설정·배출 확인·DB 재시도 |
| `GET /api/agv/state` | AGV 데모 상태 조회 |
| `POST /api/agv/start`, `heartbeat`, `stop` | 같은 agv 경로 아래의 배송 데모 시작·유지·정지 |

주문·팔레트는 3초 간격, 데모 상태는 1초 간격, QR 영상은 200ms 간격으로 조회합니다. 화면의 DEMO 표시는 서버가 제공하는 가상 장비 상태를 뜻합니다. 실제 UNO·카메라·AGV 동작은 별도 장치 연동과 검증이 필요합니다.

이번 갱신의 빌드 및 검사 결과는 PR에 기록합니다. 위 검증 범위의 이전 모의 서버 검사는 당시 로그인·주문 관제 기능에 대한 결과입니다.
