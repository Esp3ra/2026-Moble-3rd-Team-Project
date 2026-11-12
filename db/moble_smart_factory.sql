-- =============================================================================
-- 프로젝트: 모블전자 7단계 무인 자동화 공정 (Moble_Smart_Factory)
-- 데이터베이스: MySQL 8.0+
-- 인코딩: UTF8MB4 (utf8mb4_unicode_ci)
-- =============================================================================

CREATE DATABASE IF NOT EXISTS `moble_smart_factory`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `moble_smart_factory`;

-- 기존 외래키 참조 순서를 고려한 테이블 삭제 구문 (재실행용)
DROP TABLE IF EXISTS `ALARM_LOGS`;
DROP TABLE IF EXISTS `SORTING_RESULTS`;
DROP TABLE IF EXISTS `INSPECTION_2ND_LOGS`;
DROP TABLE IF EXISTS `AGV_TRANSPORT_LOGS`;
DROP TABLE IF EXISTS `INSPECTION_1ST_LOGS`;
DROP TABLE IF EXISTS `ASSEMBLY_LOGS`;
DROP TABLE IF EXISTS `JOB_PALLET_MAPPINGS`;
DROP TABLE IF EXISTS `PALLETS`;
DROP TABLE IF EXISTS `ORDERS`;
DROP TABLE IF EXISTS `USERS`;
DROP TABLE IF EXISTS `ROLES`;

-- =============================================================================
-- [TableGroup: 관제계정_및_권한관리]
-- 1. ROLES: 권한 및 역할 마스터 테이블 (최상위 / 운영관리자)
-- =============================================================================
CREATE TABLE `ROLES` (
  `role_id` INT NOT NULL AUTO_INCREMENT COMMENT '역할 고유 식별자 (PK)',
  `role_code` VARCHAR(30) NOT NULL UNIQUE COMMENT '역할 코드 (SUPER_ADMIN, OPERATOR)',
  `role_name` VARCHAR(50) NOT NULL COMMENT '역할 명칭 (최상위 관리자, 운영 관리자)',
  `description` VARCHAR(255) DEFAULT NULL COMMENT '역할 상세 설명',
  PRIMARY KEY (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='WPF 관제 사용자 권한 마스터';

-- 초기 2대 역할 데이터 등록
INSERT INTO `ROLES` (`role_id`, `role_code`, `role_name`, `description`) VALUES
(1, 'SUPER_ADMIN', '최상위 관리자', '전체 시스템 제어, 설비 파라미터 설정, 계정 관리 등 전체 탭 풀 권한'),
(2, 'OPERATOR', '운영 관리자', '7단계 실시간 공정 모니터링 및 알람 확인 등 현장 운영 관제 권한');

-- =============================================================================
-- [TableGroup: 관제계정_및_권한관리]
-- 2. USERS: 관제 시스템 로그인 계정 마스터 테이블
-- =============================================================================
CREATE TABLE `USERS` (
  `user_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '사용자 고유 식별자 (PK)',
  `role_id` INT NOT NULL DEFAULT 2 COMMENT '부여된 역할 (FK -> ROLES.role_id, 기본값: OPERATOR)',
  `emp_no` VARCHAR(50) NOT NULL UNIQUE COMMENT '로그인 사번/아이디 (예: admin, op_01)',
  `password_hash` VARCHAR(255) NOT NULL COMMENT '암호화된 비밀번호 (Bcrypt/SHA-256)',
  `name` VARCHAR(50) NOT NULL COMMENT '담당자 성명',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '계정 생성 일시',
  PRIMARY KEY (`user_id`),
  KEY `idx_users_role_id` (`role_id`),
  CONSTRAINT `fk_users_roles` FOREIGN KEY (`role_id`) REFERENCES `ROLES` (`role_id`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='WPF 관제 시스템 사용자 및 관리자 계정';

-- =============================================================================
-- [TableGroup: 관리자_정적데이터]
-- 3. ORDERS: 작업 지시 및 주문 마스터 테이블 (상위 MES 시스템 연동)
-- =============================================================================
CREATE TABLE `ORDERS` (
  `job_id` VARCHAR(50) NOT NULL COMMENT '주문 고유 식별자 (예: JOB-025)',
  `user_id` BIGINT DEFAULT NULL COMMENT '주문 고객 식별자 (FK -> USERS.user_id, 비회원 주문 시 NULL)',
  `model_name` VARCHAR(50) NOT NULL COMMENT '생산 대상 로봇 모델명 (예: MODEL_A)',
  `target_head_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 머리 부품 ID (예: H1, H2)',
  `target_body_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 몸통 부품 ID (예: B1)',
  `target_arm_l_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 왼팔 부품 ID (L1: 표준, L2: 정밀, L3: 그리퍼)',
  `target_arm_r_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 오른팔 부품 ID (R1: 표준, R2: 정밀, R3: 그리퍼)',
  `target_station` INT NOT NULL COMMENT '최종 배송 목적지 레일/스테이션 번호 (1: H1파랑 구역, 2: H2빨강 구역, 3: 보관구역)',
  `status` VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT '주문 진행 상태 (PENDING, IN_PROGRESS, COMPLETED, CANCELED)',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '주문 생성 시각',
  PRIMARY KEY (`job_id`),
  KEY `idx_orders_created_at` (`created_at`),
  KEY `idx_orders_user_id` (`user_id`),
  CONSTRAINT `fk_orders_users` FOREIGN KEY (`user_id`) REFERENCES `USERS` (`user_id`) ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='1단계: 작업 지시 및 주문 마스터 테이블 (상위 MES 시스템 연동)';

-- =============================================================================
-- [TableGroup: 관리자_정적데이터]
-- 3. PALLETS: 공정 내 순환하는 키팅 파레트 마스터 테이블
-- =============================================================================
CREATE TABLE `PALLETS` (
  `pallet_id` VARCHAR(50) NOT NULL COMMENT '파레트 부착 QR 코드 식별자 (예: PALLET-01)',
  `current_status` VARCHAR(30) NOT NULL DEFAULT 'READY' COMMENT '파레트 실시간 상태 (READY, MAPPED, ASSEMBLING, INSPECT_1ST, AGV_MOVE, INSPECT_2ND, SORTING)',
  `cycle_count` INT NOT NULL DEFAULT 0 COMMENT '파레트 누적 순환 및 재사용 횟수',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '파레트 상태 변경 시각',
  PRIMARY KEY (`pallet_id`),
  KEY `idx_pallets_status` (`current_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='1~7단계: 공정 내 순환하는 키팅 파레트 마스터 테이블';

-- =============================================================================
-- [TableGroup: 공정_트래킹]
-- 4. JOB_PALLET_MAPPINGS: 주문-파레트 QR 매핑 및 1단계 웹캠 검수 관리 테이블
-- =============================================================================
CREATE TABLE `JOB_PALLET_MAPPINGS` (
  `mapping_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '공정 매핑 고유 식별자 (PK)',
  `job_id` VARCHAR(50) NOT NULL COMMENT '연결된 주문 번호 (FK -> ORDERS.job_id)',
  `pallet_id` VARCHAR(50) NOT NULL COMMENT '매핑된 파레트 QR ID (FK -> PALLETS.pallet_id)',
  `qr_read_success` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '1단계 하향 웹캠 파레트 QR 코드 인식 성공 여부',
  `body_detected` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '1단계 파레트 위 몸통 부품 안착 감지 여부',
  `mapped_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '1단계 주문-파레트 QR 매핑 시작 시각',
  `unmapped_at` DATETIME DEFAULT NULL COMMENT '7단계 공정 완료 후 파레트 매핑 해제 시각',
  `is_active` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '현재 공정 진행 활성화 여부 (True: 진행중, False: 완료)',
  PRIMARY KEY (`mapping_id`),
  KEY `idx_job_id` (`job_id`),
  KEY `idx_pallet_id` (`pallet_id`),
  KEY `idx_mappings_active` (`is_active`),
  CONSTRAINT `fk_mapping_orders` FOREIGN KEY (`job_id`) REFERENCES `ORDERS` (`job_id`) ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT `fk_mapping_pallets` FOREIGN KEY (`pallet_id`) REFERENCES `PALLETS` (`pallet_id`) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='1단계: 주문-파레트 QR 매핑 및 1단계 웹캠 검수 관리 테이블';

-- =============================================================================
-- [TableGroup: 생산_및_물류이력]
-- 5. ASSEMBLY_LOGS: myCobot 280-Pi 로봇팔 단계별 조립 및 자석 결합 이력 (1:1 매핑)
-- =============================================================================
CREATE TABLE `ASSEMBLY_LOGS` (
  `assembly_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '조립 이력 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `stopper_clamped` BOOLEAN NOT NULL COMMENT '컨베이어 스토퍼 정지 및 클램핑 고정 성공 여부',
  `arm_l_assembled` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '왼팔 부품 조립 동작 완료 여부 (lane 0)',
  `arm_r_assembled` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '오른팔 부품 조립 동작 완료 여부 (lane 1)',
  `head_assembled` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '머리 부품 조립 동작 완료 여부 (lane 2 or 3)',
  `assembled_head_id` VARCHAR(50) DEFAULT NULL COMMENT '실제 장착된 머리 부품 ID (H1 파랑, H2 빨강)',
  `assembled_arm_l_id` VARCHAR(50) DEFAULT NULL COMMENT '실제 장착된 왼팔 부품 ID (L1~L3 오장착 추적용)',
  `assembled_arm_r_id` VARCHAR(50) DEFAULT NULL COMMENT '실제 장착된 오른팔 부품 ID (R1~R3 오장착 추적용)',
  `magnetic_fixed` BOOLEAN NOT NULL COMMENT '몸통 지그 및 소형 자석 부품 결합 고정 완료 여부',
  `assembled_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '조립 공정 완료 시각',
  PRIMARY KEY (`assembly_id`),
  UNIQUE KEY `uq_assembly_mapping_id` (`mapping_id`),
  CONSTRAINT `fk_assembly_mapping` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='2단계: myCobot 280-Pi 로봇팔 단계별 조립 및 자석 결합 이력';

-- =============================================================================
-- [TableGroup: 비전검사_및_분기결과]
-- 6. INSPECTION_1ST_LOGS: 1차 인라인 비전 검사 이력 테이블 (1:1 매핑)
-- =============================================================================
CREATE TABLE `INSPECTION_1ST_LOGS` (
  `inspect_1st_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '1차 검사 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `is_missing_part` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '조립 직후 부품 누락 발생 여부',
  `tilt_angle_degree` FLOAT NOT NULL DEFAULT 0.0 COMMENT '부품 체결 오차 및 자석 결합 각도 틀어짐(도)',
  `result_pass` BOOLEAN NOT NULL COMMENT '1차 비전 검사 최종 합격 여부',
  `image_path` VARCHAR(255) DEFAULT NULL COMMENT '1차 비전 카메라 촬영 원본 이미지 저장 경로',
  `inspected_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '1차 검사 수행 시각',
  PRIMARY KEY (`inspect_1st_id`),
  UNIQUE KEY `uq_inspect_1st_mapping_id` (`mapping_id`),
  CONSTRAINT `fk_inspect_1st_mapping` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='3단계: 1차 인라인 비전 검사 이력 테이블 (기준 판정 데이터)';

-- =============================================================================
-- [TableGroup: 생산_및_물류이력]
-- 7. AGV_TRANSPORT_LOGS: 지게차 AGV 도킹, 리프팅(19.2mm), 무선 Handshake 이력 (1:N 매핑)
-- =============================================================================
CREATE TABLE `AGV_TRANSPORT_LOGS` (
  `agv_log_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT 'AGV 이송 로그 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL COMMENT '공정 매핑 식별자 (FK)',
  `agv_id` VARCHAR(50) NOT NULL COMMENT '이송 담당 지게차 AGV 장비 식별자',
  `from_location` VARCHAR(50) DEFAULT NULL COMMENT '이동 출발지 (예: ASSY_STATION, DARK_CHAMBER)',
  `to_location` VARCHAR(50) DEFAULT NULL COMMENT '이동 목적지 (예: DARK_CHAMBER, STATION_01, STATION_02)',
  `docking_sensor` BOOLEAN NOT NULL COMMENT 'AGV 포크 하부 파레트 적재 감지 센서 정상 작동 여부',
  `fork_lifted` BOOLEAN NOT NULL DEFAULT FALSE COMMENT 'AGV 포크 안전 주행 높이(19.2mm) 상한 스위치 승강 완료 여부',
  `handshake_status` BOOLEAN NOT NULL COMMENT '컨베이어 ↔ AGV 간 RF 무선 신호 교환 정상 여부',
  `action_type` VARCHAR(30) NOT NULL COMMENT 'AGV 물류 동작 유형 (TAKEOVER: 파레트 인수/승강, UNLOAD: 파레트 하차/하강)',
  `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'AGV 물류 동작 수행 시각',
  PRIMARY KEY (`agv_log_id`),
  KEY `idx_agv_mapping_id` (`mapping_id`),
  CONSTRAINT `fk_agv_mapping` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='4단계 및 6단계: 지게차 AGV 도킹, 리프팅, 무선 Handshake 이력 테이블';

-- =============================================================================
-- [TableGroup: 비전검사_및_분기결과]
-- 8. INSPECTION_2ND_LOGS: 밀실 듀얼 비전 검사 이력 테이블 (1:1 매핑)
-- =============================================================================
CREATE TABLE `INSPECTION_2ND_LOGS` (
  `inspect_2nd_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '2차 밀실 검사 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `top_qr_verified` BOOLEAN NOT NULL COMMENT '상단 카메라: QR 재촬영 및 JOB ID 일치 검증 여부',
  `head_model_match` BOOLEAN NOT NULL COMMENT '상단 카메라: 머리 모델(H1/H2) 정품/종류 일치 여부',
  `arm_model_match` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '측면 카메라: 양팔 모델(L1~L3, R1~R3) 규격 일치 여부',
  `actual_head_id` VARCHAR(50) DEFAULT NULL COMMENT '실제 판독된 머리 부품 ID (오장착 추적용)',
  `side_joint_angle` FLOAT NOT NULL DEFAULT 0.0 COMMENT '측면 카메라: 양팔(L1~L3, R1~R3) 자석 결합 각도 오차(도)',
  `confidence_score` FLOAT NOT NULL DEFAULT 1.0 COMMENT 'YOLO 비전 AI 신뢰도 점수 (0.0~1.0, 0.70 미만 시 ERR_3_HOLD 판정)',
  `transport_detach` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '운반 탈락 검사 (1차 검사 대비 AGV 이동 중 자석 이탈 여부)',
  `overall_result` VARCHAR(30) NOT NULL COMMENT '2차 검사 종합 판정 결과 (PASS, ERR_MISMATCH, ERR_DEFECT, ERR_HOLD)',
  `assigned_lane` INT NOT NULL COMMENT '지게차 AGV 하달 분기 레일 번호 (1: OK 1, 2: OK 2, 4~6: 오류 레일)',
  `top_image_path` VARCHAR(255) DEFAULT NULL COMMENT '상단 비전 카메라 촬영 이미지 경로',
  `side_image_path` VARCHAR(255) DEFAULT NULL COMMENT '측면 비전 카메라 촬영 이미지 경로',
  `inspected_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '2차 밀실 검사 완료 시각',
  PRIMARY KEY (`inspect_2nd_id`),
  UNIQUE KEY `uq_inspect_2nd_mapping_id` (`mapping_id`),
  CONSTRAINT `fk_inspect_2nd_mapping` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='5단계: 밀실 듀얼 비전 검사 이력 테이블 (차광 챔버 검사 및 이동 중 자석 이탈 추적)';

-- =============================================================================
-- [TableGroup: 비전검사_및_분기결과]
-- 9. SORTING_RESULTS: 최종 자동 분류, 레일 하차 및 파레트 회수 관리 테이블 (1:1 매핑)
-- =============================================================================
CREATE TABLE `SORTING_RESULTS` (
  `sort_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '최종 분류 이력 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `final_category` VARCHAR(30) NOT NULL COMMENT '최종 분류 카테고리 (NORMAL, ERR_1_MISMATCH, ERR_2_DEFECT, ERR_3_HOLD)',
  `unloaded_station` INT NOT NULL COMMENT '실제 지게차 AGV가 파레트를 하차한 스테이션/오류 존 번호 (1: OK 1, 2: OK 2)',
  `pallet_returned` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '공정 완료 후 빈 파레트 회수 컨베이어 진입 완료 여부',
  `completed_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '전체 공정 종료 및 해제 시각',
  PRIMARY KEY (`sort_id`),
  UNIQUE KEY `uq_sorting_mapping_id` (`mapping_id`),
  CONSTRAINT `fk_sorting_mapping` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='6~7단계: 최종 자동 분류, 레일 하차 및 파레트 회수 관리 테이블';

-- =============================================================================
-- [TableGroup: 설비_및_경보관리]
-- 10. ALARM_LOGS: 설비 경보 및 인터락 장애 이력 관리 테이블 (MOBLE-ALRM-04 연동)
-- =============================================================================
CREATE TABLE `ALARM_LOGS` (
  `alarm_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '경보 고유 식별자 (PK)',
  `error_code` VARCHAR(50) NOT NULL COMMENT '장애/경보 코드 (예: ERR_1_MISMATCH, ERR_2_DEFECT, ERR_3_HOLD, E_STOP_INTERLOCK)',
  `description` VARCHAR(255) NOT NULL COMMENT '경보 상세 내역 (예: Step 05 밀실 비전 자석 이탈 감지)',
  `severity` VARCHAR(20) NOT NULL DEFAULT 'WARNING' COMMENT '경보 심각도 (INFO, WARNING, CRITICAL)',
  `occurred_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '경보 발생 시각',
  `resolved` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '관리자 조치 및 경보 해제 여부',
  PRIMARY KEY (`alarm_id`),
  KEY `idx_alarm_occurred_at` (`occurred_at`),
  KEY `idx_alarm_resolved` (`resolved`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='설비 경보 및 인터락 장애 이력 관리 테이블 (MOBLE-ALRM-04 연동)';
