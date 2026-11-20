# MOBOT 주문 서버

AGV 지도에 맞춘 목적지는 3=서울, 4=평택, 5=수원, 6=오류1, 7=오류2입니다. UNO 완료 후 WPF에서 AGV 배송 데모를 실행할 수 있습니다. [AGV 연결·DB 변경 안내](AGV_DEMO_INTEGRATION.md)를 참고하세요.

SQLite와 WPF의 UNO 장비 없는 데모 연결을 추가했습니다. WPF에서 선택 주문으로 시작하면 OpenCV가 팔레트 QR 이미지를 판독해 해당 주문에 배정합니다. 실행·API·상태·검증 범위는 [UNO 데모 연결 안내](UNO_DEMO_INTEGRATION.md)를 참고하세요.

## Windows에서 시작하기

Python 3.9 이상을 설치한 PC에서 프로젝트 폴더를 열고 처음 한 번 아래 명령을 실행합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`Start-MOBOT.cmd`를 실행한 뒤 Chrome이나 Edge에서 [MOBOT 홈](http://127.0.0.1:8000/)을 엽니다. [주문 구성](http://127.0.0.1:8000/order)에서 주문자 정보, 모듈, 결제 옵션을 입력하고 **주문 정보 확인 → 주문 접수**를 누르면 접수 번호를 확인할 수 있습니다. 주문은 기본적으로 `instance/mobot.sqlite3`에 저장됩니다. 주문번호는 `mobot-1`, `mobot-2`처럼 증가하며 기존 긴 주문번호도 서버 초기화 시 순번으로 변환합니다. 동시 주문과 재접수는 같은 DB 트랜잭션으로 번호 중복을 방지합니다. 카드의 경우 1개월은 일시불로 표시됩니다. 결제 옵션은 주문과 함께 기록되며 결제 승인이나 송금은 처리하지 않습니다.

## C# 관리자 앱에서 조회하기

`../../WPF/RobotFactory.sln`의 관리자 앱에서 새 주문을 3초마다 자동 조회합니다. 실행과 연결 설정은 [WPF 사용 안내](../../WPF/README.md)를 참고하세요.

관리자 앱은 시작 시 로그인 화면을 표시합니다. DB에 등록된 아이디와 비밀번호로 로그인하면 이름·권한과 관제 화면을 표시합니다. 로그인 후 발급받은 세션으로 주문·팔레트를 조회하고 주문을 드롭합니다.

`POST /api/auth/login`은 JSON의 `login_id`, `password`를 검증하고 `token`, `expires_at`(Unix 초), `account`를 반환합니다. `account`에는 `account_id`, `login_id`, `name`, `role_code`, `role_name`이 포함됩니다. 이후 `Authorization: Bearer <세션 토큰>`으로 관리자 API를 호출합니다. `GET /api/auth/me`는 현재 계정을 조회하고 `POST /api/auth/logout`은 세션을 해제합니다. 로그인 세션은 8시간이며 비밀번호와 세션 토큰은 DB에 해시로 저장합니다. 기존 외부 호출자의 `MOBOT_ADMIN_TOKEN` 인증도 유지합니다.

`GET /api/orders` 응답의 `orders` 배열에는 최신 주문 최대 100건의 `id`, `created_at`(UTC), `company`, `phone`, `address`, `head`, `left`, `right`, `status`가 포함됩니다. `ADMIN`(최상위 관리자)와 `OPERATOR`(1차 관리자) 계정 모두 현재 주문 조회·드롭과 팔레트 조회를 사용할 수 있습니다.

웹 주문 화면은 같은 서버의 `POST /api/orders`에 JSON을 보냅니다. 주문자 정보와 세 모듈 코드 외에 `request_id`로 UUID를 받습니다. 같은 요청 번호와 주문 내용으로 재시도하면 기존 접수 번호를 돌려줍니다.

카드 주문은 `payment_method: "card"`, `card_issuer` 코드(`kb`, `nh`, `shinhan`, `ibk`), 정수 `installment_months`를 전송합니다. 할부를 체크하지 않으면 1(일시불)을 보내고, 체크하면 2 이상을 입력합니다. 계좌이체는 `payment_method: "transfer"`와 `payment_bank` 코드(`kb`, `shinhan`, `woori`)를 전송합니다. 관리자 조회에도 이 필드가 포함됩니다.

## 스마트 팩토리 SQLite

서버 시작 시 `smart_factory_sqlite.sql`을 적용합니다. 이전 버전의 웹 주문이 있으면 기존 주문 번호와 연락처를 보존해 `ORDERS`로 옮깁니다. 이후에는 주문·팔레트·공정 이력 데이터를 보존하면서 필요한 테이블과 초기값을 생성합니다.

- 헤드 부품 ID는 듀얼아이 `head-A`, 모노아이 `head-B`입니다. 이송/용접/체결 순으로 오른팔은 `arm-1`/`arm-2`/`arm-3`, 왼팔은 `arm-a`/`arm-b`/`arm-c`입니다. 이전 부품 ID는 서버를 시작할 때 주문·조립·검사 기록에서 새 ID로 변환합니다.
- 모델 코드는 헤드 대문자, 오른팔 숫자, 왼팔 소문자 순입니다. 예를 들어 모노아이·오른팔 이송·왼팔 체결은 `B1c`입니다.
- 서울은 AGV 목적지 3, 평택은 4, 수원은 5로 저장합니다. 다른 주소는 `target_station = NULL`이며, 관리자가 WPF에서 배송 목적지를 선택합니다. 오류 구역 6·7도 관리자 선택입니다. 이전 목적지 CHECK 제약과 기존 주소의 목적지는 서버 시작 시 변환합니다.
- 관리자 응답에는 기존 필드에 더해 `model_name`, `target_head_id`, `target_arm_l_id`, `target_arm_r_id`, `target_station`, `factory_status`가 포함됩니다. SQLite의 초기 공정 상태 `PENDING`은 기존 API의 `status`에서 `received`로 표시됩니다.

관리자 앱은 같은 인증 헤더로 `GET /api/pallets`를 호출해 `pallets` 배열의 `pallet_id`, `current_status`, `cycle_count`, `updated_at`(UTC)을 조회합니다. 팔레트 테이블을 처음 생성할 때 `PALLET-01`부터 `PALLET-08`까지 등록합니다. 기존 테이블의 데이터와 삭제 상태는 유지하므로 전체 초기화 후 서버를 재시작해도 팔레트가 다시 생성되지 않습니다.

WPF의 **주문 드롭**은 관리자 인증을 사용해 `POST /api/orders/<주문 ID>/cancel`을 호출합니다. 생산 대기이며 활성 팔레트 배정이 없는 주문을 `CANCELED`로 보관하고 WPF 목록에서 제외합니다. 같은 요청의 재전송은 기존 취소 결과를 반환합니다.

## 전체 진행본 공유 경로

서버·웹·SQLite 스키마·UNO 및 AGV 데모는 이 폴더에, 최신 통합 WPF는 저장소 루트의 `WPF/`에 있습니다. `WPF-CameraPreview/`는 별도 카메라 미리보기 작업본입니다. GitHub 저장소 루트에서 WPF를 빌드하고, 서버는 이 폴더를 작업 디렉터리로 실행하세요.

실제 DB와 계정·토큰·로그·실행 산출물은 공유본에 포함하지 않습니다. 처음 사용하는 서버의 관리자 계정은 서버 담당자에게 발급받으세요. AGV 스케치의 Wi-Fi 값은 공유용 자리표시자로 바꿨고, 장비 모듈의 MySQL 기본 주소와 사용자도 로컬 주소·자리표시자로 바꿨습니다. 장치 사용 시 담당자가 본인의 환경 설정을 적용해야 합니다.
