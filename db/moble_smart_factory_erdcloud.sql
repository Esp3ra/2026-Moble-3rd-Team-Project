-- =============================================================================
-- ERDCloud (erdcloud.com) 전용 DDL 가져오기 스크립트
-- 모블전자 7단계 무인 자동화 공정 (Moble_Smart_Factory)
-- 사용 방법: erdcloud.com 접속 -> [가져오기(Import)] -> [SQL] 선택 후 아래 내용 붙여넣기
-- =============================================================================

CREATE TABLE `USERS` (
  `user_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '회원 고유 식별자 (PK)',
  `username` VARCHAR(50) NOT NULL UNIQUE COMMENT '로그인 아이디',
  `password_hash` VARCHAR(255) NOT NULL COMMENT '암호화된 비밀번호 (Bcrypt/SHA-256)',
  `name` VARCHAR(50) NOT NULL COMMENT '고객/관리자 이름',
  `email` VARCHAR(100) NOT NULL UNIQUE COMMENT '이메일 주소 (중복 불가)',
  `phone` VARCHAR(20) NOT NULL COMMENT '전화번호 (010-XXXX-XXXX)',
  `postal_code` VARCHAR(10) NULL COMMENT '우편번호',
  `address_main` VARCHAR(255) NULL COMMENT '기본 배송지 주소 (도로명/공장 주소)',
  `address_detail` VARCHAR(255) NULL COMMENT '상세 주소 (동/호수, 라인 번호 등)',
  `role` VARCHAR(20) NOT NULL DEFAULT 'CUSTOMER' COMMENT '권한 (CUSTOMER, OPERATOR, ADMIN)',
  `marketing_opt_in` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '마케팅 정보 수신 동의 여부',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '가입 시각',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '정보 수정 시각',
  PRIMARY KEY (`user_id`)
) COMMENT='회원 및 관제 관리자 마스터 테이블';

CREATE TABLE `ORDERS` (
  `job_id` VARCHAR(50) NOT NULL COMMENT '주문 고유 식별자 (예: JOB-025)',
  `user_id` BIGINT NULL COMMENT '주문 고객 식별자 (FK -> USERS.user_id, 비회원 주문 시 NULL)',
  `model_name` VARCHAR(50) NOT NULL COMMENT '생산 대상 로봇 모델명 (예: MODEL_A)',
  `target_head_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 머리 부품 ID (H1, H2)',
  `target_body_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 몸통 부품 ID (B1)',
  `target_arm_l_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 왼팔 부품 ID (L1~L3)',
  `target_arm_r_id` VARCHAR(50) NOT NULL COMMENT '투입 요구 오른팔 부품 ID (R1~R3)',
  `target_station` INT NOT NULL COMMENT '최종 배송 목적지 (1: H1구역, 2: H2구역, 3: 보관구역)',
  `status` VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT '주문 진행 상태 (PENDING, IN_PROGRESS, COMPLETED, CANCELED)',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '주문 생성 시각',
  PRIMARY KEY (`job_id`),
  CONSTRAINT `FK_USERS_TO_ORDERS` FOREIGN KEY (`user_id`) REFERENCES `USERS` (`user_id`)
) COMMENT='1단계: 작업 지시 및 주문 마스터 테이블';

CREATE TABLE `PALLETS` (
  `pallet_id` VARCHAR(50) NOT NULL COMMENT '파레트 부착 QR 코드 식별자 (예: PALLET-01)',
  `current_status` VARCHAR(30) NOT NULL DEFAULT 'READY' COMMENT '파레트 실시간 상태',
  `cycle_count` INT NOT NULL DEFAULT 0 COMMENT '파레트 누적 순환 및 재사용 횟수',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '파레트 상태 변경 시각',
  PRIMARY KEY (`pallet_id`)
) COMMENT='1~7단계: 공정 내 순환하는 키팅 파레트 마스터 테이블';

CREATE TABLE `JOB_PALLET_MAPPINGS` (
  `mapping_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '공정 매핑 고유 식별자 (PK)',
  `job_id` VARCHAR(50) NOT NULL COMMENT '연결된 주문 번호 (FK)',
  `pallet_id` VARCHAR(50) NOT NULL COMMENT '매핑된 파레트 QR ID (FK)',
  `qr_read_success` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '1단계 상단 웹캠 QR 코드 인식 성공 여부',
  `body_detected` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '1단계 파레트 위 몸통 부품 안착 감지 여부',
  `mapped_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '1단계 주문-파레트 매핑 시작 시각',
  `unmapped_at` DATETIME NULL COMMENT '7단계 공정 완료 후 파레트 매핑 해제 시각',
  `is_active` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '현재 공정 진행 활성화 여부',
  PRIMARY KEY (`mapping_id`),
  CONSTRAINT `FK_ORDERS_TO_MAPPINGS` FOREIGN KEY (`job_id`) REFERENCES `ORDERS` (`job_id`),
  CONSTRAINT `FK_PALLETS_TO_MAPPINGS` FOREIGN KEY (`pallet_id`) REFERENCES `PALLETS` (`pallet_id`)
) COMMENT='1단계: 주문-파레트 매핑 및 웹캠 사전 검수 관리 테이블';

CREATE TABLE `ASSEMBLY_LOGS` (
  `assembly_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '조립 이력 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL UNIQUE COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `stopper_clamped` BOOLEAN NOT NULL COMMENT '컨베이어 스토퍼 정지 및 클램핑 고정 성공 여부',
  `arm_l_assembled` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '왼팔 부품 조립 완료 여부 (lane 0)',
  `arm_r_assembled` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '오른팔 부품 조립 완료 여부 (lane 1)',
  `head_assembled` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '머리 부품 조립 완료 여부 (lane 2 or 3)',
  `assembled_head_id` VARCHAR(50) NULL COMMENT '실제 장착된 머리 부품 ID (H1 파랑, H2 빨강)',
  `assembled_arm_l_id` VARCHAR(50) NULL COMMENT '실제 장착된 왼팔 부품 ID (L1~L3 오장착 추적용)',
  `assembled_arm_r_id` VARCHAR(50) NULL COMMENT '실제 장착된 오른팔 부품 ID (R1~R3 오장착 추적용)',
  `magnetic_fixed` BOOLEAN NOT NULL COMMENT '몸통 지그 및 소형 자석 부품 결합 고정 완료 여부',
  `assembled_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '조립 공정 완료 시각',
  PRIMARY KEY (`assembly_id`),
  CONSTRAINT `FK_MAPPINGS_TO_ASSEMBLY` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`)
) COMMENT='2단계: myCobot 280-Pi 로봇팔 조립 및 자석 결합 이력 테이블';

CREATE TABLE `INSPECTION_1ST_LOGS` (
  `inspect_1st_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '1차 검사 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL UNIQUE COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `is_missing_part` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '조립 직후 부품 누락 발생 여부',
  `tilt_angle_degree` FLOAT NOT NULL DEFAULT 0.0 COMMENT '부품 체결 오차 및 각도 틀어짐(도)',
  `result_pass` BOOLEAN NOT NULL COMMENT '1차 비전 검사 최종 합격 여부',
  `image_path` VARCHAR(255) NULL COMMENT '1차 비전 카메라 촬영 원본 이미지 저장 경로',
  `inspected_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '1차 검사 수행 시각',
  PRIMARY KEY (`inspect_1st_id`),
  CONSTRAINT `FK_MAPPINGS_TO_INSPECT_1ST` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`)
) COMMENT='3단계: 1차 인라인 비전 검사 이력 테이블';

CREATE TABLE `AGV_TRANSPORT_LOGS` (
  `agv_log_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT 'AGV 이송 로그 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL COMMENT '공정 매핑 식별자 (FK)',
  `agv_id` VARCHAR(50) NOT NULL COMMENT '이송 담당 지게차 AGV 장비 식별자',
  `from_location` VARCHAR(50) NULL COMMENT '이동 출발지 (ASSY_STATION, DARK_CHAMBER)',
  `to_location` VARCHAR(50) NULL COMMENT '이동 목적지 (DARK_CHAMBER, STATION_01, STATION_02)',
  `docking_sensor` BOOLEAN NOT NULL COMMENT '포크 하부 파레트 적재 감지 센서 (수평 안착)',
  `fork_lifted` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '포크 안전 이동 높이(19.2mm) 승강 완료 여부 (수직 승강)',
  `handshake_status` BOOLEAN NOT NULL COMMENT '컨베이어 ↔ AGV 간 RF 무선 신호 교환 정상 여부',
  `action_type` VARCHAR(30) NOT NULL COMMENT 'AGV 물류 동작 유형 (TAKEOVER, UNLOAD)',
  `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'AGV 물류 동작 수행 시각',
  PRIMARY KEY (`agv_log_id`),
  CONSTRAINT `FK_MAPPINGS_TO_AGV` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`)
) COMMENT='4단계 및 6단계: 지게차 AGV 도킹, 리프팅(19.2mm), 무선 Handshake 이력 테이블';

CREATE TABLE `INSPECTION_2ND_LOGS` (
  `inspect_2nd_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '2차 밀실 검사 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL UNIQUE COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `top_qr_verified` BOOLEAN NOT NULL COMMENT '상단 카메라: QR 재촬영 및 JOB ID 일치 검증 여부',
  `head_model_match` BOOLEAN NOT NULL COMMENT '상단 카메라: 머리 모델(H1/H2) 정품/종류 일치 여부',
  `arm_model_match` BOOLEAN NOT NULL DEFAULT TRUE COMMENT '측면 카메라: 양팔 모델(L1~L3, R1~R3) 규격 일치 여부',
  `actual_head_id` VARCHAR(50) NULL COMMENT '실제 판독된 머리 부품 ID (오장착 추적용)',
  `side_joint_angle` FLOAT NOT NULL DEFAULT 0.0 COMMENT '측면 카메라: 양팔(L1~L3, R1~R3) 자석 결합 각도 오차(도)',
  `confidence_score` FLOAT NOT NULL DEFAULT 1.0 COMMENT 'YOLO 비전 AI 신뢰도 점수 (0.0~1.0, 0.70 미만 시 ERR_3_HOLD 판정)',
  `transport_detach` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '운반 탈락 검사 (AGV 이동 중 자석 이탈 여부)',
  `overall_result` VARCHAR(30) NOT NULL COMMENT '2차 검사 종합 판정 결과 (PASS, ERR_MISMATCH, ERR_DEFECT, ERR_HOLD)',
  `assigned_lane` INT NOT NULL COMMENT '지게차 AGV 하달 분기 레일 번호 (1: OK 1, 2: OK 2, 4~6: 오류 레일)',
  `top_image_path` VARCHAR(255) NULL COMMENT '상단 비전 카메라 촬영 이미지 경로',
  `side_image_path` VARCHAR(255) NULL COMMENT '측면 비전 카메라 촬영 이미지 경로',
  `inspected_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '2차 밀실 검사 완료 시각',
  PRIMARY KEY (`inspect_2nd_id`),
  CONSTRAINT `FK_MAPPINGS_TO_INSPECT_2ND` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`)
) COMMENT='5단계: 밀실 듀얼 비전 검사 이력 테이블';

CREATE TABLE `SORTING_RESULTS` (
  `sort_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '최종 분류 이력 고유 식별자 (PK)',
  `mapping_id` BIGINT NOT NULL UNIQUE COMMENT '공정 매핑 식별자 (FK, 1:1)',
  `final_category` VARCHAR(30) NOT NULL COMMENT '최종 분류 카테고리 (NORMAL, ERR_1_MISMATCH, ERR_2_DEFECT, ERR_3_HOLD)',
  `unloaded_station` INT NOT NULL COMMENT '하차된 스테이션 번호 (1: OK 1, 2: OK 2)',
  `pallet_returned` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '공정 완료 후 빈 파레트 회수 완료 여부',
  `completed_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '전체 공정 종료 시각',
  PRIMARY KEY (`sort_id`),
  CONSTRAINT `FK_MAPPINGS_TO_SORTING` FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`)
) COMMENT='6~7단계: 최종 자동 분류, 레일 하차 및 파레트 회수 관리 테이블';

CREATE TABLE `ALARM_LOGS` (
  `alarm_id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '경보 고유 식별자 (PK)',
  `error_code` VARCHAR(50) NOT NULL COMMENT '장애/경보 코드 (예: ERR_1_MISMATCH, E_STOP_INTERLOCK)',
  `description` VARCHAR(255) NOT NULL COMMENT '경보 상세 내역 (예: Step 05 밀실 비전 자석 이탈 감지)',
  `severity` VARCHAR(20) NOT NULL DEFAULT 'WARNING' COMMENT '경보 심각도 (INFO, WARNING, CRITICAL)',
  `occurred_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '경보 발생 시각',
  `resolved` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '관리자 조치 및 경보 해제 여부',
  PRIMARY KEY (`alarm_id`)
) COMMENT='설비 경보 및 인터락 장애 이력 관리 테이블 (MOBLE-ALRM-04 연동)';
