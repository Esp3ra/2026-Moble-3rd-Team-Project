# MOBOT 주문 서버

> 팀 자료 공유용 예제입니다. 이 폴더에서 서버를 실행하고 가짜 정보로 주문을 접수하면 로컬 SQLite의 `ORDERS` 테이블에서 화면에 표시된 접수 번호를 `job_id`로 확인할 수 있습니다.


## Windows에서 시작하기

Python 3.9 이상을 설치한 PC에서 프로젝트 폴더를 열고 처음 한 번 아래 명령을 실행합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`Start-MOBOT.cmd`를 실행한 뒤 Chrome이나 Edge에서 [MOBOT 홈](http://127.0.0.1:8000/)을 엽니다. [주문 구성](http://127.0.0.1:8000/order)에서 주문자 정보, 모듈, 결제 옵션을 입력하고 **주문 정보 확인 → 주문 접수**를 누르면 접수 번호를 확인할 수 있습니다. 주문은 기본적으로 `instance/mobot.sqlite3`에 저장됩니다. 카드의 경우 1개월은 일시불로 표시됩니다. 결제 옵션은 주문과 함께 기록되며 결제 승인이나 송금은 처리하지 않습니다.

## C# 관리자 앱에서 조회하기

서버를 시작할 때 `MOBOT_ADMIN_TOKEN` 환경 변수로 팀의 임의 토큰을 설정합니다. 아래 예시에서 생성한 토큰은 현재 PowerShell 세션에만 설정됩니다. C# 앱에서 사용하는 토큰도 같은 값으로 설정합니다.

```powershell
$env:MOBOT_ADMIN_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\Start-MOBOT.cmd
```

관리자 앱의 `HttpClient`에서 `Authorization: Bearer <토큰>` 헤더를 넣어 `GET http://127.0.0.1:8000/api/orders`를 호출합니다. 응답의 `orders` 배열에는 최신 주문 최대 100건의 `id`, `created_at`(UTC), `company`, `phone`, `address`, `head`, `left`, `right`, `status`가 포함됩니다. 토큰을 설정하지 않은 서버에서는 관리자 조회가 비활성화됩니다.

웹 주문 화면은 같은 서버의 `POST /api/orders`에 JSON을 보냅니다. 주문자 정보와 세 모듈 코드 외에 `request_id`로 UUID를 받습니다. 같은 요청 번호와 주문 내용으로 재시도하면 기존 접수 번호를 돌려줍니다.

카드 주문은 `payment_method: "card"`, `card_issuer` 코드(`kb`, `nh`, `shinhan`, `ibk`), 정수 `installment_months`를 전송합니다. 할부를 체크하지 않으면 1(일시불)을 보내고, 체크하면 2 이상을 입력합니다. 계좌이체는 `payment_method: "transfer"`와 `payment_bank` 코드(`kb`, `shinhan`, `woori`)를 전송합니다. 관리자 조회에도 이 필드가 포함됩니다.

## 스마트 팩토리 SQLite

서버 시작 시 `smart_factory_sqlite.sql`을 적용합니다. 이전 버전의 웹 주문이 있으면 기존 주문 번호와 연락처를 보존해 `ORDERS`로 옮깁니다. 이후에는 주문·팔레트·공정 이력 데이터를 보존하면서 필요한 테이블과 초기값을 생성합니다.

- 헤드 부품 ID는 듀얼아이 `head-A`, 모노아이 `head-B`입니다. 이송/용접/체결 순으로 오른팔은 `arm-1`/`arm-2`/`arm-3`, 왼팔은 `arm-a`/`arm-b`/`arm-c`입니다. 이전 부품 ID는 서버를 시작할 때 주문·조립·검사 기록에서 새 ID로 변환합니다.
- 모델 코드는 헤드 대문자, 오른팔 숫자, 왼팔 소문자 순입니다. 예를 들어 모노아이·오른팔 이송·왼팔 체결은 `B1c`입니다.
- 주소의 첫 행정구역을 기준으로 대전·천안은 허브 1, 서울·경기는 허브 2, 영남은 허브 3으로 저장합니다. 허브를 판단할 수 없는 주소는 `target_station = NULL`로 저장됩니다.
- 관리자 응답에는 기존 필드에 더해 `model_name`, `target_head_id`, `target_arm_l_id`, `target_arm_r_id`, `target_station`, `factory_status`가 포함됩니다. SQLite의 초기 공정 상태 `PENDING`은 기존 API의 `status`에서 `received`로 표시됩니다.
