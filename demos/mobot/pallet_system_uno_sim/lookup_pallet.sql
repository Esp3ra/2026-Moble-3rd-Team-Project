-- MySQL 8.0 / 조회 전용. DB 초기화 SQL을 실행할 필요가 없습니다.
USE moble_smart_factory;
SET @pallet_id = 'PALLET-01';

SELECT m.mapping_id, m.pallet_id,
       p.current_status AS pallet_status, p.cycle_count,
       o.job_id, o.model_name,
       o.target_head_id, o.target_arm_l_id, o.target_arm_r_id,
       o.target_station, o.status AS order_status,
       m.qr_read_success, m.body_detected, m.mapped_at, m.is_active
FROM JOB_PALLET_MAPPINGS AS m
JOIN ORDERS AS o ON m.job_id = o.job_id
JOIN PALLETS AS p ON m.pallet_id = p.pallet_id
WHERE m.pallet_id = @pallet_id
  AND m.is_active = TRUE
ORDER BY m.mapped_at DESC
LIMIT 1;
-- 0행: 등록되지 않은 팔레트 또는 현재 매핑 없음.
-- 활성 매핑 중 mapped_at이 가장 최근인 1건을 반환합니다.
