# WPF 주문 관제

## 실행

Windows에서 .NET 10 SDK로 빌드하고 실행합니다.

```powershell
dotnet build .\RobotFactory.sln -c Release
dotnet run --project .\RobotFactory.Admin\RobotFactory.Admin.csproj
```

Visual Studio에서는 `RobotFactory.sln`을 열고 `RobotFactory.Admin`을 실행합니다.

## 서버 연결

로그인 화면에서 서버의 기본 주소, 관리자 아이디와 비밀번호를 입력합니다. 원격 서버는 HTTPS 주소를 사용합니다. 같은 PC에서 서버를 실행하면 `http://127.0.0.1:8000`처럼 루프백 HTTP 주소를 사용할 수 있습니다. `MOBOT_API_URL` 환경 변수는 로그인 화면에 처음 표시할 기본 주소를 지정합니다.

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
