"""주문 접수 및 검증. 접수는 팔레트 매핑과 독립적으로 PENDING을 저장합니다."""
import mysql.connector

TEXT_FIELDS = {
    'job_id': 50, 'company_name': 100, 'model_name': 50,
    'target_head_id': 50, 'target_arm_l_id': 50, 'target_arm_r_id': 50,
}


class OrderConflict(ValueError):
    pass


def validate_order(data):
    if not isinstance(data, dict):
        raise ValueError('JSON 객체가 필요합니다.')
    allowed = set(TEXT_FIELDS) | {'address', 'target_station'}
    if set(data) - allowed:
        raise ValueError('지원하지 않는 필드: ' + ', '.join(sorted(set(data) - allowed)))
    result = {}
    for field, limit in TEXT_FIELDS.items():
        value = data.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f'{field}: 1~{limit}자의 문자열이 필요합니다.')
        result[field] = value.strip()
    for field, choices in [('target_head_id', {'H1', 'H2'}),
                           ('target_arm_l_id', {'L1', 'L2', 'L3'}),
                           ('target_arm_r_id', {'R1', 'R2', 'R3'})]:
        if result[field] not in choices:
            raise ValueError(f'{field}: 허용값 {sorted(choices)}')
    station = data.get('target_station')
    if type(station) is not int or station not in (1, 2, 3):
        raise ValueError('target_station: 정수 1, 2, 3 중 하나가 필요합니다.')
    result['target_station'] = station
    address = data.get('address')
    if address is not None and (not isinstance(address, str) or len(address.strip()) > 255):
        raise ValueError('address: 255자 이하 문자열 또는 null이 필요합니다.')
    result['address'] = address.strip() if address is not None else None
    return result


def receive_order(connection, data):
    order = validate_order(data)
    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        try:
            cursor.execute(
                'INSERT INTO ORDERS (job_id, company_name, model_name, target_head_id, '
                'target_arm_l_id, target_arm_r_id, target_station, address, status) '
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING')",
                tuple(order[k] for k in (*TEXT_FIELDS, 'target_station', 'address')),
            )
            connection.commit()
            return {'job_id': order['job_id'], 'status': 'PENDING', 'created': True}
        except mysql.connector.IntegrityError as error:
            if error.errno != 1062:
                raise
            # PK를 이용해 동시 접수와 재전송을 구분합니다.
            cursor.execute('SELECT * FROM ORDERS WHERE job_id = %s FOR UPDATE', (order['job_id'],))
            existing = cursor.fetchone()
            if existing is None or any(existing[k] != v for k, v in order.items()):
                raise OrderConflict('같은 job_id에 다른 주문정보가 이미 등록되어 있습니다.')
            connection.commit()
            return {'job_id': existing['job_id'], 'status': existing['status'], 'created': False}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
