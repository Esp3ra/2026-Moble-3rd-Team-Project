-- MOBOT draft schema for MySQL 8.0.17+.
-- Run as a complete script against a server where the draft schema name is unused.
-- Batch mode stops on error. Do not use --force. This script expects CREATE and INSERT privileges.
CREATE DATABASE moble_smart_factory_draft CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;
USE moble_smart_factory_draft;
SET SESSION time_zone = '+00:00';
SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION';

CREATE TABLE `PARTS` (
  `part_id` VARCHAR(32) NOT NULL PRIMARY KEY,
  `slot` VARCHAR(8) NOT NULL,
  `label` VARCHAR(100) NOT NULL,
  `is_enabled` BOOLEAN NOT NULL DEFAULT true,
  CONSTRAINT `ck_parts_1` CHECK (slot IN ('HEAD', 'BODY', 'ARM_L', 'ARM_R')),
  CONSTRAINT `ck_parts_2` CHECK (is_enabled IN (0, 1))
) ENGINE=InnoDB;

CREATE TABLE `WEB_PART_OPTIONS` (
  `slot` VARCHAR(8) NOT NULL,
  `option_code` VARCHAR(32) NOT NULL,
  `part_id` VARCHAR(32),
  PRIMARY KEY (`slot`, `option_code`),
  FOREIGN KEY (`part_id`) REFERENCES `PARTS` (`part_id`),
  CONSTRAINT `ck_web_part_options_1` CHECK (slot IN ('HEAD', 'ARM_L', 'ARM_R'))
) ENGINE=InnoDB;

CREATE TABLE `LOCATIONS` (
  `location_code` VARCHAR(32) NOT NULL PRIMARY KEY,
  `label` VARCHAR(100) NOT NULL,
  `location_kind` VARCHAR(16) NOT NULL,
  CONSTRAINT `ck_locations_1` CHECK (location_kind IN ('PROCESS', 'DESTINATION', 'REJECT', 'RETURN'))
) ENGINE=InnoDB;

CREATE TABLE `ADMIN_ACCOUNTS` (
  `admin_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `auth_issuer` VARCHAR(255) NOT NULL,
  `auth_subject` VARCHAR(255) NOT NULL,
  `display_name` VARCHAR(100) NOT NULL,
  `role_code` VARCHAR(16) NOT NULL,
  `is_enabled` BOOLEAN NOT NULL DEFAULT true,
  UNIQUE KEY (`auth_issuer`, `auth_subject`),
  CONSTRAINT `ck_admin_accounts_1` CHECK (role_code IN ('OPERATOR', 'SUPER_ADMIN')),
  CONSTRAINT `ck_admin_accounts_2` CHECK (is_enabled IN (0, 1))
) ENGINE=InnoDB;

CREATE TABLE `EQUIPMENT` (
  `equipment_id` VARCHAR(32) NOT NULL PRIMARY KEY,
  `equipment_kind` VARCHAR(32) NOT NULL,
  `label` VARCHAR(100) NOT NULL,
  `run_state` VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN',
  `last_seen_at` DATETIME(6),
  CONSTRAINT `ck_equipment_1` CHECK (run_state IN ('UNKNOWN', 'IDLE', 'RUNNING', 'PAUSED', 'ERROR'))
) ENGINE=InnoDB;

CREATE TABLE `RECIPE_VERSIONS` (
  `recipe_version_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `recipe_code` VARCHAR(32) NOT NULL,
  `version_no` INT NOT NULL,
  `config` JSON NOT NULL,
  `published_at` DATETIME(6),
  `published_by` BIGINT,
  UNIQUE KEY (`recipe_code`, `version_no`),
  FOREIGN KEY (`published_by`) REFERENCES `ADMIN_ACCOUNTS` (`admin_id`),
  CONSTRAINT `ck_recipe_versions_1` CHECK (version_no > 0),
  CONSTRAINT `ck_recipe_versions_2` CHECK ((published_at IS NULL) = (published_by IS NULL))
) ENGINE=InnoDB;

CREATE TABLE `RECIPE_STEPS` (
  `recipe_version_id` BIGINT NOT NULL,
  `step_no` INT NOT NULL,
  `stage_no` INT NOT NULL,
  `equipment_id` VARCHAR(32) NOT NULL,
  `action_code` VARCHAR(32) NOT NULL,
  `parameters` JSON NOT NULL,
  PRIMARY KEY (`recipe_version_id`, `step_no`),
  FOREIGN KEY (`recipe_version_id`) REFERENCES `RECIPE_VERSIONS` (`recipe_version_id`),
  FOREIGN KEY (`equipment_id`) REFERENCES `EQUIPMENT` (`equipment_id`),
  CONSTRAINT `ck_recipe_steps_1` CHECK (step_no > 0 AND stage_no BETWEEN 1 AND 7)
) ENGINE=InnoDB;

CREATE TABLE `ORDERS` (
  `job_id` VARCHAR(32) NOT NULL PRIMARY KEY,
  `model_name` VARCHAR(64) NOT NULL,
  `target_head_id` VARCHAR(32) NOT NULL,
  `target_body_id` VARCHAR(32) NOT NULL,
  `target_arm_l_id` VARCHAR(32) NOT NULL,
  `target_arm_r_id` VARCHAR(32) NOT NULL,
  `target_location_code` VARCHAR(32),
  `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  `approval_status` VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  `approved_by` BIGINT,
  `approved_at` DATETIME(6),
  `hold_reason` VARCHAR(255),
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `ended_at` DATETIME(6),
  KEY (`status`, `approval_status`, `created_at`),
  FOREIGN KEY (`target_head_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_body_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_arm_l_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_arm_r_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_location_code`) REFERENCES `LOCATIONS` (`location_code`),
  FOREIGN KEY (`approved_by`) REFERENCES `ADMIN_ACCOUNTS` (`admin_id`),
  CONSTRAINT `ck_orders_1` CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'CANCELED', 'ABORTED')),
  CONSTRAINT `ck_orders_2` CHECK (approval_status IN ('PENDING', 'APPROVED', 'ON_HOLD')),
  CONSTRAINT `ck_orders_3` CHECK ((approval_status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_at >= created_at AND target_location_code IS NOT NULL AND hold_reason IS NULL) OR (approval_status <> 'APPROVED' AND approved_by IS NULL AND approved_at IS NULL AND ((approval_status = 'ON_HOLD') = (hold_reason IS NOT NULL)) AND (hold_reason IS NULL OR CHAR_LENGTH(TRIM(hold_reason)) > 0))),
  CONSTRAINT `ck_orders_4` CHECK ((ended_at IS NOT NULL) = (status IN ('COMPLETED', 'FAILED', 'CANCELED', 'ABORTED'))),
  CONSTRAINT `ck_orders_5` CHECK (ended_at IS NULL OR ended_at >= created_at)
) ENGINE=InnoDB;

CREATE TABLE `PALLETS` (
  `pallet_id` VARCHAR(32) NOT NULL PRIMARY KEY,
  `current_status` VARCHAR(16) NOT NULL DEFAULT 'READY',
  `cycle_count` INT NOT NULL DEFAULT 0,
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  CONSTRAINT `ck_pallets_1` CHECK (current_status IN ('READY', 'MAPPED', 'ASSEMBLING', 'INSPECT_1ST', 'AGV_MOVE', 'INSPECT_2ND', 'SORTING', 'RECOVERY')),
  CONSTRAINT `ck_pallets_2` CHECK (cycle_count >= 0)
) ENGINE=InnoDB;

CREATE TABLE `JOB_PALLET_MAPPINGS` (
  `mapping_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `job_id` VARCHAR(32) NOT NULL,
  `pallet_id` VARCHAR(32) NOT NULL,
  `recipe_version_id` BIGINT NOT NULL,
  `target_head_id` VARCHAR(32) NOT NULL,
  `target_body_id` VARCHAR(32) NOT NULL,
  `target_arm_l_id` VARCHAR(32) NOT NULL,
  `target_arm_r_id` VARCHAR(32) NOT NULL,
  `target_location_code` VARCHAR(32) NOT NULL,
  `qr_read_status` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `body_presence` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `status` VARCHAR(16) NOT NULL DEFAULT 'IN_PROGRESS',
  `mapped_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `ended_at` DATETIME(6),
  `unmapped_at` DATETIME(6),
  `is_active` BOOLEAN GENERATED ALWAYS AS (unmapped_at IS NULL) VIRTUAL NOT NULL,
  `active_pallet_id` VARCHAR(32) GENERATED ALWAYS AS (CASE WHEN unmapped_at IS NULL THEN pallet_id END) VIRTUAL UNIQUE,
  `active_job_id` VARCHAR(32) GENERATED ALWAYS AS (CASE WHEN unmapped_at IS NULL THEN job_id END) VIRTUAL UNIQUE,
  KEY (`job_id`, `mapped_at`),
  KEY (`pallet_id`, `mapped_at`),
  FOREIGN KEY (`job_id`) REFERENCES `ORDERS` (`job_id`),
  FOREIGN KEY (`pallet_id`) REFERENCES `PALLETS` (`pallet_id`),
  FOREIGN KEY (`recipe_version_id`) REFERENCES `RECIPE_VERSIONS` (`recipe_version_id`),
  FOREIGN KEY (`target_head_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_body_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_arm_l_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_arm_r_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`target_location_code`) REFERENCES `LOCATIONS` (`location_code`),
  CONSTRAINT `ck_job_pallet_mappings_1` CHECK (qr_read_status IN ('NOT_RUN', 'READ', 'FAILED', 'UNKNOWN')),
  CONSTRAINT `ck_job_pallet_mappings_2` CHECK (body_presence IN ('NOT_RUN', 'PRESENT', 'ABSENT', 'UNDETERMINED', 'UNKNOWN')),
  CONSTRAINT `ck_job_pallet_mappings_3` CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'CANCELED', 'ABORTED')),
  CONSTRAINT `ck_job_pallet_mappings_4` CHECK ((ended_at IS NULL) = (status = 'IN_PROGRESS')),
  CONSTRAINT `ck_job_pallet_mappings_5` CHECK (ended_at IS NULL OR ended_at >= mapped_at),
  CONSTRAINT `ck_job_pallet_mappings_6` CHECK (unmapped_at IS NULL OR (ended_at IS NOT NULL AND unmapped_at >= ended_at))
) ENGINE=InnoDB;

CREATE TABLE `ASSEMBLY_LOGS` (
  `assembly_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `mapping_id` BIGINT NOT NULL,
  `attempt_no` INT NOT NULL,
  `commanded_head_id` VARCHAR(32) NOT NULL,
  `commanded_arm_l_id` VARCHAR(32) NOT NULL,
  `commanded_arm_r_id` VARCHAR(32) NOT NULL,
  `stopper_clamped` BOOLEAN,
  `arm_l_motion_done` BOOLEAN,
  `arm_r_motion_done` BOOLEAN,
  `head_motion_done` BOOLEAN,
  `magnetic_fix_status` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `magnetic_fix_evidence` VARCHAR(512),
  `status` VARCHAR(16) NOT NULL DEFAULT 'RUNNING',
  `started_at` DATETIME(6) NOT NULL,
  `ended_at` DATETIME(6),
  UNIQUE KEY (`mapping_id`, `attempt_no`),
  UNIQUE KEY (`mapping_id`, `assembly_id`),
  FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`),
  FOREIGN KEY (`commanded_head_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`commanded_arm_l_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`commanded_arm_r_id`) REFERENCES `PARTS` (`part_id`),
  CONSTRAINT `ck_assembly_logs_1` CHECK (attempt_no > 0),
  CONSTRAINT `ck_assembly_logs_2` CHECK (magnetic_fix_status IN ('NOT_RUN', 'UNKNOWN', 'VERIFIED', 'NOT_FIXED', 'UNDETERMINED')),
  CONSTRAINT `ck_assembly_logs_3` CHECK (magnetic_fix_status NOT IN ('VERIFIED', 'NOT_FIXED') OR (magnetic_fix_evidence IS NOT NULL AND CHAR_LENGTH(TRIM(magnetic_fix_evidence)) > 0)),
  CONSTRAINT `ck_assembly_logs_4` CHECK (stopper_clamped IN (0,1) AND arm_l_motion_done IN (0,1) AND arm_r_motion_done IN (0,1) AND head_motion_done IN (0,1)),
  CONSTRAINT `ck_assembly_logs_5` CHECK (status <> 'COMPLETED' OR (stopper_clamped <=> 1 AND arm_l_motion_done <=> 1 AND arm_r_motion_done <=> 1 AND head_motion_done <=> 1)),
  CONSTRAINT `ck_assembly_logs_6` CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELED', 'ABORTED')),
  CONSTRAINT `ck_assembly_logs_7` CHECK ((ended_at IS NULL) = (status = 'RUNNING') AND (ended_at IS NULL OR ended_at >= started_at))
) ENGINE=InnoDB;

CREATE TABLE `INSPECTION_1ST_LOGS` (
  `inspect_1st_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `mapping_id` BIGINT NOT NULL,
  `assembly_id` BIGINT NOT NULL,
  `attempt_no` INT NOT NULL,
  `parts_presence` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `tilt_angle_degree` DECIMAL(7,3),
  `result` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `reason` VARCHAR(255),
  `image_path` VARCHAR(512),
  `started_at` DATETIME(6),
  `ended_at` DATETIME(6),
  UNIQUE KEY (`mapping_id`, `attempt_no`),
  UNIQUE KEY (`mapping_id`, `assembly_id`, `inspect_1st_id`),
  FOREIGN KEY (`mapping_id`, `assembly_id`) REFERENCES `ASSEMBLY_LOGS` (`mapping_id`, `assembly_id`),
  CONSTRAINT `ck_inspection_1st_logs_1` CHECK (attempt_no > 0),
  CONSTRAINT `ck_inspection_1st_logs_2` CHECK (parts_presence IN ('NOT_RUN', 'UNKNOWN', 'ALL_PRESENT', 'MISSING', 'UNDETERMINED')),
  CONSTRAINT `ck_inspection_1st_logs_3` CHECK (result IN ('NOT_RUN', 'RUNNING', 'PASS', 'FAIL', 'UNDETERMINED', 'ABORTED')),
  CONSTRAINT `ck_inspection_1st_logs_4` CHECK (result <> 'PASS' OR parts_presence = 'ALL_PRESENT'),
  CONSTRAINT `ck_inspection_1st_logs_5` CHECK (result NOT IN ('FAIL', 'UNDETERMINED', 'ABORTED') OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) > 0)),
  CONSTRAINT `ck_inspection_1st_logs_6` CHECK ((started_at IS NULL) = (result = 'NOT_RUN') AND (ended_at IS NULL) = (result IN ('NOT_RUN', 'RUNNING')) AND (ended_at IS NULL OR ended_at >= started_at)),
  CONSTRAINT `ck_inspection_1st_logs_7` CHECK (result <> 'NOT_RUN' OR (parts_presence = 'NOT_RUN' AND tilt_angle_degree IS NULL AND image_path IS NULL))
) ENGINE=InnoDB;

CREATE TABLE `INSPECTION_2ND_LOGS` (
  `inspect_2nd_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `mapping_id` BIGINT NOT NULL,
  `assembly_id` BIGINT NOT NULL,
  `baseline_inspect_1st_id` BIGINT,
  `attempt_no` INT NOT NULL,
  `qr_verification` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `actual_head_id` VARCHAR(32),
  `head_detection` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `head_confidence` DECIMAL(6,5),
  `head_reason` VARCHAR(255),
  `actual_arm_l_id` VARCHAR(32),
  `arm_l_detection` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `arm_l_confidence` DECIMAL(6,5),
  `arm_l_reason` VARCHAR(255),
  `actual_arm_r_id` VARCHAR(32),
  `arm_r_detection` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `arm_r_confidence` DECIMAL(6,5),
  `arm_r_reason` VARCHAR(255),
  `side_joint_angle` DECIMAL(7,3),
  `detach_status` VARCHAR(20) NOT NULL DEFAULT 'NOT_RUN',
  `detach_evidence` VARCHAR(512),
  `result` VARCHAR(16) NOT NULL DEFAULT 'NOT_RUN',
  `reason` VARCHAR(255),
  `assigned_location_code` VARCHAR(32),
  `top_image_path` VARCHAR(512),
  `side_image_path` VARCHAR(512),
  `started_at` DATETIME(6),
  `ended_at` DATETIME(6),
  UNIQUE KEY (`mapping_id`, `attempt_no`),
  UNIQUE KEY (`mapping_id`, `inspect_2nd_id`),
  FOREIGN KEY (`actual_head_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`actual_arm_l_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`actual_arm_r_id`) REFERENCES `PARTS` (`part_id`),
  FOREIGN KEY (`assigned_location_code`) REFERENCES `LOCATIONS` (`location_code`),
  FOREIGN KEY (`mapping_id`, `assembly_id`) REFERENCES `ASSEMBLY_LOGS` (`mapping_id`, `assembly_id`),
  FOREIGN KEY (`mapping_id`, `assembly_id`, `baseline_inspect_1st_id`) REFERENCES `INSPECTION_1ST_LOGS` (`mapping_id`, `assembly_id`, `inspect_1st_id`),
  CONSTRAINT `ck_inspection_2nd_logs_1` CHECK (attempt_no > 0),
  CONSTRAINT `ck_inspection_2nd_logs_2` CHECK (qr_verification IN ('NOT_RUN', 'UNKNOWN', 'MATCH', 'MISMATCH', 'UNDETERMINED')),
  CONSTRAINT `ck_inspection_2nd_logs_3` CHECK (head_detection IN ('NOT_RUN', 'UNKNOWN', 'IDENTIFIED', 'ABSENT', 'UNDETERMINED') AND (head_detection = 'IDENTIFIED') = (actual_head_id IS NOT NULL) AND (head_confidence IS NULL OR (head_detection IN ('IDENTIFIED', 'ABSENT', 'UNDETERMINED') AND head_confidence BETWEEN 0 AND 1)) AND (head_detection NOT IN ('ABSENT', 'UNDETERMINED') OR (head_reason IS NOT NULL AND CHAR_LENGTH(TRIM(head_reason)) > 0))),
  CONSTRAINT `ck_inspection_2nd_logs_4` CHECK (arm_l_detection IN ('NOT_RUN', 'UNKNOWN', 'IDENTIFIED', 'ABSENT', 'UNDETERMINED') AND (arm_l_detection = 'IDENTIFIED') = (actual_arm_l_id IS NOT NULL) AND (arm_l_confidence IS NULL OR (arm_l_detection IN ('IDENTIFIED', 'ABSENT', 'UNDETERMINED') AND arm_l_confidence BETWEEN 0 AND 1)) AND (arm_l_detection NOT IN ('ABSENT', 'UNDETERMINED') OR (arm_l_reason IS NOT NULL AND CHAR_LENGTH(TRIM(arm_l_reason)) > 0))),
  CONSTRAINT `ck_inspection_2nd_logs_5` CHECK (arm_r_detection IN ('NOT_RUN', 'UNKNOWN', 'IDENTIFIED', 'ABSENT', 'UNDETERMINED') AND (arm_r_detection = 'IDENTIFIED') = (actual_arm_r_id IS NOT NULL) AND (arm_r_confidence IS NULL OR (arm_r_detection IN ('IDENTIFIED', 'ABSENT', 'UNDETERMINED') AND arm_r_confidence BETWEEN 0 AND 1)) AND (arm_r_detection NOT IN ('ABSENT', 'UNDETERMINED') OR (arm_r_reason IS NOT NULL AND CHAR_LENGTH(TRIM(arm_r_reason)) > 0))),
  CONSTRAINT `ck_inspection_2nd_logs_6` CHECK (detach_status IN ('NOT_RUN', 'UNKNOWN', 'NO_EVIDENCE', 'SUSPECTED', 'CONFIRMED', 'UNDETERMINED')),
  CONSTRAINT `ck_inspection_2nd_logs_7` CHECK (detach_status <> 'SUSPECTED' OR baseline_inspect_1st_id IS NOT NULL),
  CONSTRAINT `ck_inspection_2nd_logs_8` CHECK (detach_status NOT IN ('SUSPECTED', 'CONFIRMED') OR (detach_evidence IS NOT NULL AND CHAR_LENGTH(TRIM(detach_evidence)) > 0)),
  CONSTRAINT `ck_inspection_2nd_logs_9` CHECK (result IN ('NOT_RUN', 'RUNNING', 'PASS', 'MISMATCH', 'DEFECT', 'HOLD', 'UNDETERMINED', 'ABORTED')),
  CONSTRAINT `ck_inspection_2nd_logs_10` CHECK (result <> 'PASS' OR (qr_verification = 'MATCH' AND head_detection = 'IDENTIFIED' AND arm_l_detection = 'IDENTIFIED' AND arm_r_detection = 'IDENTIFIED' AND detach_status NOT IN ('SUSPECTED', 'CONFIRMED'))),
  CONSTRAINT `ck_inspection_2nd_logs_11` CHECK (result NOT IN ('MISMATCH', 'DEFECT', 'HOLD', 'UNDETERMINED', 'ABORTED') OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) > 0)),
  CONSTRAINT `ck_inspection_2nd_logs_12` CHECK (assigned_location_code IS NULL OR result IN ('PASS', 'MISMATCH', 'DEFECT', 'HOLD')),
  CONSTRAINT `ck_inspection_2nd_logs_13` CHECK ((started_at IS NULL) = (result = 'NOT_RUN') AND (ended_at IS NULL) = (result IN ('NOT_RUN', 'RUNNING')) AND (ended_at IS NULL OR ended_at >= started_at)),
  CONSTRAINT `ck_inspection_2nd_logs_14` CHECK (result <> 'NOT_RUN' OR (qr_verification = 'NOT_RUN' AND head_detection = 'NOT_RUN' AND arm_l_detection = 'NOT_RUN' AND arm_r_detection = 'NOT_RUN' AND detach_status = 'NOT_RUN' AND side_joint_angle IS NULL AND top_image_path IS NULL AND side_image_path IS NULL))
) ENGINE=InnoDB;

CREATE TABLE `EQUIPMENT_COMMANDS` (
  `command_id` VARCHAR(64) NOT NULL PRIMARY KEY,
  `equipment_id` VARCHAR(32) NOT NULL,
  `mapping_id` BIGINT,
  `command_type` VARCHAR(32) NOT NULL,
  `payload` JSON NOT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'ISSUED',
  `issued_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `accepted_at` DATETIME(6),
  `started_at` DATETIME(6),
  `ended_at` DATETIME(6),
  `error_detail` VARCHAR(255),
  KEY (`equipment_id`, `status`, `issued_at`),
  KEY (`mapping_id`, `issued_at`),
  FOREIGN KEY (`equipment_id`) REFERENCES `EQUIPMENT` (`equipment_id`),
  FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`),
  CONSTRAINT `ck_equipment_commands_1` CHECK (status IN ('ISSUED', 'ACCEPTED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELED', 'ABORTED')),
  CONSTRAINT `ck_equipment_commands_2` CHECK (status NOT IN ('ACCEPTED', 'RUNNING', 'COMPLETED') OR accepted_at IS NOT NULL),
  CONSTRAINT `ck_equipment_commands_3` CHECK (status NOT IN ('RUNNING', 'COMPLETED') OR started_at IS NOT NULL),
  CONSTRAINT `ck_equipment_commands_4` CHECK (status NOT IN ('ISSUED', 'ACCEPTED') OR started_at IS NULL),
  CONSTRAINT `ck_equipment_commands_5` CHECK (status <> 'ISSUED' OR accepted_at IS NULL),
  CONSTRAINT `ck_equipment_commands_6` CHECK ((ended_at IS NOT NULL) = (status IN ('COMPLETED', 'FAILED', 'CANCELED', 'ABORTED'))),
  CONSTRAINT `ck_equipment_commands_7` CHECK (accepted_at IS NULL OR accepted_at >= issued_at),
  CONSTRAINT `ck_equipment_commands_8` CHECK (started_at IS NULL OR (accepted_at IS NOT NULL AND started_at >= accepted_at)),
  CONSTRAINT `ck_equipment_commands_9` CHECK (ended_at IS NULL OR ended_at >= COALESCE(started_at, accepted_at, issued_at)),
  CONSTRAINT `ck_equipment_commands_10` CHECK (status <> 'FAILED' OR (error_detail IS NOT NULL AND CHAR_LENGTH(TRIM(error_detail)) > 0))
) ENGINE=InnoDB;

CREATE TABLE `AGV_TRANSPORT_LOGS` (
  `agv_log_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `mapping_id` BIGINT NOT NULL,
  `agv_id` VARCHAR(32) NOT NULL,
  `command_id` VARCHAR(64) UNIQUE,
  `from_location_code` VARCHAR(32) NOT NULL,
  `planned_to_location_code` VARCHAR(32) NOT NULL,
  `actual_to_location_code` VARCHAR(32),
  `docking_sensor_ok` BOOLEAN,
  `pallet_present` BOOLEAN,
  `fork_lift_completed` BOOLEAN,
  `handshake_ok` BOOLEAN,
  `action_type` VARCHAR(16) NOT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'RUNNING',
  `started_at` DATETIME(6) NOT NULL,
  `ended_at` DATETIME(6),
  KEY (`mapping_id`, `started_at`),
  UNIQUE KEY (`mapping_id`, `agv_log_id`),
  FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`),
  FOREIGN KEY (`agv_id`) REFERENCES `EQUIPMENT` (`equipment_id`),
  FOREIGN KEY (`command_id`) REFERENCES `EQUIPMENT_COMMANDS` (`command_id`),
  FOREIGN KEY (`from_location_code`) REFERENCES `LOCATIONS` (`location_code`),
  FOREIGN KEY (`planned_to_location_code`) REFERENCES `LOCATIONS` (`location_code`),
  FOREIGN KEY (`actual_to_location_code`) REFERENCES `LOCATIONS` (`location_code`),
  CONSTRAINT `ck_agv_transport_logs_1` CHECK (action_type IN ('TAKEOVER', 'UNLOAD')),
  CONSTRAINT `ck_agv_transport_logs_2` CHECK (docking_sensor_ok IN (0,1) AND pallet_present IN (0,1) AND fork_lift_completed IN (0,1) AND handshake_ok IN (0,1)),
  CONSTRAINT `ck_agv_transport_logs_3` CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELED', 'ABORTED')),
  CONSTRAINT `ck_agv_transport_logs_4` CHECK (action_type <> 'UNLOAD' OR status <> 'COMPLETED' OR actual_to_location_code IS NOT NULL),
  CONSTRAINT `ck_agv_transport_logs_5` CHECK ((ended_at IS NULL) = (status = 'RUNNING') AND (ended_at IS NULL OR ended_at >= started_at))
) ENGINE=InnoDB;

CREATE TABLE `SORTING_RESULTS` (
  `mapping_id` BIGINT NOT NULL PRIMARY KEY,
  `inspect_2nd_id` BIGINT NOT NULL,
  `unload_agv_log_id` BIGINT,
  `classified_at` DATETIME(6) NOT NULL,
  `pallet_returned_at` DATETIME(6),
  FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`),
  FOREIGN KEY (`mapping_id`, `inspect_2nd_id`) REFERENCES `INSPECTION_2ND_LOGS` (`mapping_id`, `inspect_2nd_id`),
  FOREIGN KEY (`mapping_id`, `unload_agv_log_id`) REFERENCES `AGV_TRANSPORT_LOGS` (`mapping_id`, `agv_log_id`),
  CONSTRAINT `ck_sorting_results_1` CHECK (pallet_returned_at IS NULL OR (unload_agv_log_id IS NOT NULL AND pallet_returned_at >= classified_at))
) ENGINE=InnoDB;

CREATE TABLE `ALARM_LOGS` (
  `alarm_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `equipment_id` VARCHAR(32) NOT NULL,
  `mapping_id` BIGINT,
  `error_code` VARCHAR(64) NOT NULL,
  `description` VARCHAR(500) NOT NULL,
  `severity` VARCHAR(16) NOT NULL DEFAULT 'WARNING',
  `occurred_at` DATETIME(6) NOT NULL,
  `resolved_by` BIGINT,
  `resolved_at` DATETIME(6),
  `resolution` VARCHAR(500),
  KEY (`equipment_id`, `occurred_at`),
  KEY (`mapping_id`, `occurred_at`),
  KEY (`resolved_at`, `occurred_at`),
  FOREIGN KEY (`equipment_id`) REFERENCES `EQUIPMENT` (`equipment_id`),
  FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`),
  FOREIGN KEY (`resolved_by`) REFERENCES `ADMIN_ACCOUNTS` (`admin_id`),
  CONSTRAINT `ck_alarm_logs_1` CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
  CONSTRAINT `ck_alarm_logs_2` CHECK ((resolved_at IS NULL AND resolved_by IS NULL AND resolution IS NULL) OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL AND resolution IS NOT NULL AND CHAR_LENGTH(TRIM(resolution)) > 0 AND resolved_at >= occurred_at))
) ENGINE=InnoDB;

CREATE TABLE `EQUIPMENT_EVENTS` (
  `event_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `equipment_id` VARCHAR(32) NOT NULL,
  `mapping_id` BIGINT,
  `command_id` VARCHAR(64),
  `event_key` VARCHAR(64) NOT NULL,
  `event_type` VARCHAR(32) NOT NULL,
  `occurred_at` DATETIME(6) NOT NULL,
  `detail` JSON,
  UNIQUE KEY (`equipment_id`, `event_key`),
  KEY (`equipment_id`, `occurred_at`),
  KEY (`mapping_id`, `occurred_at`),
  KEY (`command_id`, `occurred_at`),
  FOREIGN KEY (`equipment_id`) REFERENCES `EQUIPMENT` (`equipment_id`),
  FOREIGN KEY (`mapping_id`) REFERENCES `JOB_PALLET_MAPPINGS` (`mapping_id`),
  FOREIGN KEY (`command_id`) REFERENCES `EQUIPMENT_COMMANDS` (`command_id`)
) ENGINE=InnoDB;

CREATE TABLE `ADMIN_ACTION_LOGS` (
  `audit_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `admin_id` BIGINT NOT NULL,
  `action_code` VARCHAR(32) NOT NULL,
  `entity_type` VARCHAR(32) NOT NULL,
  `entity_id` VARCHAR(64) NOT NULL,
  `before_data` JSON,
  `after_data` JSON,
  `reason` VARCHAR(255),
  `occurred_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY (`entity_type`, `entity_id`, `occurred_at`),
  KEY (`admin_id`, `occurred_at`),
  FOREIGN KEY (`admin_id`) REFERENCES `ADMIN_ACCOUNTS` (`admin_id`)
) ENGINE=InnoDB;

-- Website option keys only. All physical part bindings are pending.
INSERT INTO WEB_PART_OPTIONS (slot, option_code) VALUES
  ('HEAD', 'dual'), ('HEAD', 'mono'),
  ('ARM_L', 'grip'), ('ARM_L', 'weld'), ('ARM_L', 'fasten'),
  ('ARM_R', 'grip'), ('ARM_R', 'weld'), ('ARM_R', 'fasten');
