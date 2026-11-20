"""팔레트 ID 검증 및 활성 주문 조회."""
import re

QUERY = """
SELECT m.mapping_id, m.pallet_id,
       p.current_status AS pallet_status, p.cycle_count,
       o.job_id, o.model_name,
       o.target_head_id, o.target_arm_l_id, o.target_arm_r_id,
       o.target_station, o.status AS order_status,
       m.qr_read_success, m.body_detected, m.mapped_at, m.is_active
FROM JOB_PALLET_MAPPINGS AS m
JOIN ORDERS AS o ON m.job_id = o.job_id
JOIN PALLETS AS p ON m.pallet_id = p.pallet_id
WHERE m.pallet_id = %s AND m.is_active = TRUE
ORDER BY m.mapped_at DESC
LIMIT 1
"""


def validate_pallet_id(value):
    value = value.strip()
    if not re.fullmatch(r"PALLET-[0-9]+", value) or len(value) > 50:
        raise ValueError("QR 내용은 PALLET-01 형태의 팔레트 번호여야 합니다.")
    return value


def lookup(connection, pallet_id):
    pallet_id = validate_pallet_id(pallet_id)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(QUERY, (pallet_id,))
        rows = cursor.fetchall()
    finally:
        cursor.close()
    if not rows:
        raise ValueError(f"{pallet_id}: 등록된 팔레트 또는 활성 주문 매핑이 없습니다.")
    return rows[0]
