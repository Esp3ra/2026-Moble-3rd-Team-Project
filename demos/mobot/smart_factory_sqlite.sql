-- =============================================================================
-- 프로젝트: 모블전자 스마트 팩토리 (SQLite 3 표준 스키마)
-- 요구사항 반영:
--   1. [119번] JOB_PALLET_MAPPINGS: body_detected 삭제
--   2. [146번] ASSEMBLY_LOGS: magnetic_fixed 삭제
--   3. [162번] INSPECTION_1ST_LOGS: tilt_angle_degree 삭제 (누락만 체크)
--   4. [197번] INSPECTION_2ND_LOGS: side_joint_angle, side_image_path 삭제 (전면 1대만)
--   5. [222번] SORTING_RESULTS: pallet_returned 삭제 (허브 하차 시 종료)
--   6. [176번] AGV_TRANSPORT_LOGS 테이블 전체 제거
--   7. [70번] ORDERS: address 앞자리 파싱 기반 3대 허브 배송 목적지 매핑
--   8. [71번] ORDERS: model_name 모듈 조합형 코드 반영 (예: A1a)
-- =============================================================================

PRAGMA foreign_keys = ON;

-- =============================================================================
-- [1. 사용자 및 권한 관리]
-- =============================================================================
CREATE TABLE IF NOT EXISTS ROLES (
    role_id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_code TEXT NOT NULL UNIQUE,              -- ADMIN, OPERATOR
    role_name TEXT NOT NULL,                     -- 최상위 관리자, 운영 관리자
    description TEXT
);

INSERT OR IGNORE INTO ROLES (role_id, role_code, role_name, description) VALUES
(1, 'ADMIN', '최상위 관리자', '전체 설비 제어, 파라미터 세팅 등 풀 권한'),
(2, 'OPERATOR', '1차 관리자', '실시간 공정 모니터링 및 알람 확인');

CREATE TABLE IF NOT EXISTS ACCOUNTS (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER NOT NULL DEFAULT 2,
    login_id TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES ROLES(role_id) ON UPDATE CASCADE
);

-- =============================================================================
-- [2. 생산 주문 및 파레트 마스터]
-- =============================================================================
CREATE TABLE IF NOT EXISTS ADMIN_SESSIONS (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    FOREIGN KEY (account_id) REFERENCES ACCOUNTS(account_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_admin_sessions_expires ON ADMIN_SESSIONS(expires_at);

CREATE TABLE IF NOT EXISTS ORDERS (
    job_id TEXT PRIMARY KEY,                     -- 주문 번호 (mobot-1, mobot-2, ...)
    account_id INTEGER,                          -- 등록 담당자 식별자 (선택)
    company_name TEXT,                           -- 주문 고객사
    phone TEXT,                                  -- 웹 주문 연락처
    web_request_id TEXT UNIQUE,                  -- 웹 주문 재전송 식별자
    payment_method TEXT,                         -- card, transfer
    payment_bank TEXT,                           -- 계좌이체 은행 코드
    card_issuer TEXT,                            -- 신용카드 발급사 코드
    installment_months INTEGER,                  -- 카드 결제 개월 수 (1: 일시불)
    address TEXT,                                -- 배송 주소 (앞자리 인식 -> 3개 허브로 배송)
    model_name TEXT NOT NULL,                    -- 모듈 조합형 모델명 (예: A1a = 머리1/오른손1/왼손1)
    target_head_id TEXT NOT NULL,                -- 머리 부품 (head-A, head-B)
    target_arm_l_id TEXT NOT NULL,               -- 왼팔 부품 (arm-a, arm-b, arm-c)
    target_arm_r_id TEXT NOT NULL,               -- 오른팔 부품 (arm-1, arm-2, arm-3)
    target_station INTEGER CHECK (target_station IN (3, 4, 5, 6, 7)), -- 3 서울, 4 평택, 5 수원, 6 오류1, 7 오류2, NULL 미배정
    status TEXT NOT NULL DEFAULT 'PENDING',      -- PENDING, IN_PROGRESS, COMPLETED, CANCELED
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES ACCOUNTS(account_id) ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS PALLETS (
    pallet_id TEXT PRIMARY KEY,                  -- 파레트 QR 식별자 (예: PALLET-01)
    current_status TEXT NOT NULL DEFAULT 'READY',-- READY, MAPPED, ASSEMBLING, INSPECT_1ST, INSPECT_2ND, SORTING
    cycle_count INTEGER NOT NULL DEFAULT 0,      -- 파레트 순환 횟수
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 기본 순환 파레트 등록
INSERT OR IGNORE INTO PALLETS (pallet_id, current_status, cycle_count) VALUES
('PALLET-01', 'READY', 0),
('PALLET-02', 'READY', 0),
('PALLET-03', 'READY', 0),
('PALLET-04', 'READY', 0),
('PALLET-05', 'READY', 0),
('PALLET-06', 'READY', 0),
('PALLET-07', 'READY', 0),
('PALLET-08', 'READY', 0);

-- =============================================================================
-- [3. 1단계: 주문-파레트 QR 매핑]
-- [119번 반영] body_detected 삭제 완료
-- =============================================================================
CREATE TABLE IF NOT EXISTS JOB_PALLET_MAPPINGS (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    pallet_id TEXT NOT NULL,
    qr_read_success INTEGER NOT NULL DEFAULT 1,  -- 1단계 웹캠 파레트 QR 인식 성공 여부 (1=True)
    mapped_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    unmapped_at DATETIME DEFAULT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,        -- 공정 진행 중 여부 (1=진행중, 0=완료)
    FOREIGN KEY (job_id) REFERENCES ORDERS(job_id) ON UPDATE CASCADE,
    FOREIGN KEY (pallet_id) REFERENCES PALLETS(pallet_id) ON UPDATE CASCADE
);

-- =============================================================================
-- [4. 2단계: myCobot 280-Pi 조립 이력]
-- [146번 반영] magnetic_fixed 삭제 완료
-- =============================================================================
CREATE TABLE IF NOT EXISTS EQUIPMENT_RUNS (
    run_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL DEFAULT 'UNO-DEMO',
    mode TEXT NOT NULL DEFAULT 'DEMO' CHECK (mode = 'DEMO'),
    job_id TEXT NOT NULL REFERENCES ORDERS(job_id) ON UPDATE CASCADE,
    expected_pallet_id TEXT NOT NULL REFERENCES PALLETS(pallet_id),
    pallet_id TEXT REFERENCES PALLETS(pallet_id),
    mapping_id INTEGER REFERENCES JOB_PALLET_MAPPINGS(mapping_id),
    account_id INTEGER REFERENCES ACCOUNTS(account_id) ON DELETE SET NULL,
    owner_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    database_status TEXT NOT NULL DEFAULT 'waiting',
    database_error TEXT NOT NULL DEFAULT '',
    trial_json TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    operator_confirmed_at DATETIME,
    finished_at DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_equipment_active_device
    ON EQUIPMENT_RUNS(device_id) WHERE finished_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_equipment_job ON EQUIPMENT_RUNS(job_id, created_at);
CREATE TABLE IF NOT EXISTS EQUIPMENT_DEVICE_LEASES (
    device_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    heartbeat_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS AGV_TASKS (
    task_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    equipment_run_id TEXT NOT NULL UNIQUE REFERENCES EQUIPMENT_RUNS(run_id),
    job_id TEXT NOT NULL REFERENCES ORDERS(job_id) ON UPDATE CASCADE,
    pallet_id TEXT NOT NULL REFERENCES PALLETS(pallet_id),
    mapping_id INTEGER NOT NULL REFERENCES JOB_PALLET_MAPPINGS(mapping_id),
    device_id TEXT NOT NULL DEFAULT 'AGV-DEMO',
    mode TEXT NOT NULL DEFAULT 'DEMO' CHECK (mode='DEMO'),
    destination INTEGER NOT NULL CHECK (destination IN (3,4,5,6,7)),
    account_id INTEGER REFERENCES ACCOUNTS(account_id) ON DELETE SET NULL,
    owner_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    status_json TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_agv_active_device ON AGV_TASKS(device_id) WHERE finished_at IS NULL;
CREATE TABLE IF NOT EXISTS AGV_TASK_EVENTS (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES AGV_TASKS(task_id),
    step_index INTEGER NOT NULL,
    event TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ASSEMBLY_LOGS (
    assembly_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id INTEGER UNIQUE NOT NULL,
    stopper_clamped INTEGER NOT NULL,            -- 공압 스토퍼 고정 성공 여부
    arm_l_assembled INTEGER NOT NULL DEFAULT 0,  -- 왼팔 조립 완료 여부
    arm_r_assembled INTEGER NOT NULL DEFAULT 0,  -- 오른팔 조립 완료 여부
    head_assembled INTEGER NOT NULL DEFAULT 0,   -- 머리 조립 완료 여부
    assembled_head_id TEXT DEFAULT NULL,         -- 실제 장착된 머리 부품 ID (head-A, head-B)
    assembled_arm_l_id TEXT DEFAULT NULL,        -- 실제 장착된 왼팔 부품 ID (arm-a, arm-b, arm-c)
    assembled_arm_r_id TEXT DEFAULT NULL,        -- 실제 장착된 오른팔 부품 ID (arm-1, arm-2, arm-3)
    assembled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mapping_id) REFERENCES JOB_PALLET_MAPPINGS(mapping_id) ON UPDATE CASCADE
);

-- =============================================================================
-- [5. 3단계: 1차 인라인 외관 비전 검사]
-- [162번 반영] tilt_angle_degree 삭제 완료 (각도 검사 제외, 누락만 체크)
-- =============================================================================
CREATE TABLE IF NOT EXISTS INSPECTION_1ST_LOGS (
    inspect_1st_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id INTEGER UNIQUE NOT NULL,
    is_missing_part INTEGER NOT NULL DEFAULT 0,  -- 부품 누락 여부 (1=누락 불량)
    result_pass INTEGER NOT NULL,                -- 1차 비전 검사 합격 여부 (1=합격, 0=불합격)
    image_path TEXT DEFAULT NULL,                -- 1차 비전 카메라 촬영 원본 이미지 경로
    inspected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mapping_id) REFERENCES JOB_PALLET_MAPPINGS(mapping_id) ON UPDATE CASCADE
);

-- =============================================================================
-- [6. 5단계: 2차 암실 YOLO 비전 검사]
-- [197번 반영] 전면부 단일 카메라 1대만 적용 (side_joint_angle, side_image_path 삭제 완료)
-- =============================================================================
CREATE TABLE IF NOT EXISTS INSPECTION_2ND_LOGS (
    inspect_2nd_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id INTEGER UNIQUE NOT NULL,
    top_qr_verified INTEGER NOT NULL DEFAULT 1,  -- QR 재촬영 및 JOB ID 일치 검증
    head_model_match INTEGER NOT NULL,           -- 머리 모델 일치 여부 (오부착 판정)
    arm_model_match INTEGER NOT NULL DEFAULT 1,  -- 양팔 모델 규격 일치 여부
    actual_head_id TEXT DEFAULT NULL,            -- 실제 판독된 머리 부품 ID (head-A, head-B)
    is_missing_part INTEGER NOT NULL DEFAULT 0,  -- 부품 누락(탈락) 여부
    confidence_score REAL NOT NULL DEFAULT 1.0,  -- YOLO AI 신뢰도 점수 (0.0~1.0)
    overall_result TEXT NOT NULL,                -- PASS, ERR_MISMATCH(오부착), ERR_DEFECT(누락), ERR_HOLD
    assigned_lane INTEGER NOT NULL,              -- 분류될 목적지 레일/허브 번호
    front_image_path TEXT DEFAULT NULL,          -- 전면부 단일 카메라 촬영 사진 경로
    inspected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mapping_id) REFERENCES JOB_PALLET_MAPPINGS(mapping_id) ON UPDATE CASCADE
);

-- =============================================================================
-- [7. 6~7단계: 최종 분류 및 하차 완료]
-- [222번 반영] pallet_returned 삭제 완료 (허브 하차 시 공정 종료)
-- =============================================================================
CREATE TABLE IF NOT EXISTS SORTING_RESULTS (
    sort_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id INTEGER UNIQUE NOT NULL,
    final_category TEXT NOT NULL,                -- 최종 분류 카테고리 (NORMAL, ERR_1_MISMATCH, ERR_2_DEFECT, ERR_3_HOLD)
    unloaded_hub INTEGER NOT NULL,               -- 실제 하차된 허브/레일 번호 (1, 2, 3)
    completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mapping_id) REFERENCES JOB_PALLET_MAPPINGS(mapping_id) ON UPDATE CASCADE
);

-- =============================================================================
-- [8. 설비 경보 및 이상 이력]
-- =============================================================================
CREATE TABLE IF NOT EXISTS ALARM_LOGS (
    alarm_id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_code TEXT NOT NULL,                    -- ERR_MISMATCH, ERR_DEFECT, E_STOP_INTERLOCK
    description TEXT NOT NULL,                   -- 경보 상세 내용
    severity TEXT NOT NULL DEFAULT 'WARNING',    -- INFO, WARNING, CRITICAL
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved INTEGER NOT NULL DEFAULT 0          -- 조치 및 해제 여부 (1=해제)
);
