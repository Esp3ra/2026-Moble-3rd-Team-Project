"""기존 주문을 QR 팔레트에 연결합니다. 모든 변경은 한 트랜잭션으로 처리합니다."""


def assign_next_order(connection, pallet_id):
    """팔레트를 잠근 뒤 가장 오래된 대기 주문을 잠가 배정합니다."""
    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        cursor.execute('SELECT pallet_id, current_status FROM PALLETS WHERE pallet_id = %s FOR UPDATE', (pallet_id,))
        pallet = cursor.fetchone()
        if pallet is None:
            raise ValueError(f'등록되지 않은 팔레트: {pallet_id}')
        cursor.execute(
            'SELECT mapping_id, job_id, unmapped_at FROM JOB_PALLET_MAPPINGS '
            'WHERE pallet_id = %s AND is_active = TRUE FOR UPDATE', (pallet_id,))
        active = cursor.fetchall()
        if active:
            if len(active) != 1 or active[0]['unmapped_at'] is not None:
                raise ValueError('팔레트의 활성 매핑 데이터가 일관되지 않습니다.')
            connection.commit()
            return active[0]['mapping_id'], active[0]['job_id'], False
        if pallet['current_status'] != 'READY':
            raise ValueError(f"사용 가능(READY) 팔레트만 연결할 수 있습니다: {pallet['current_status']}")
        cursor.execute(
            "SELECT o.job_id FROM ORDERS o WHERE o.status = 'PENDING' "
            'AND NOT EXISTS (SELECT 1 FROM JOB_PALLET_MAPPINGS m '
            'WHERE m.job_id = o.job_id AND m.is_active = TRUE) '
            'ORDER BY o.created_at, o.job_id LIMIT 1 FOR UPDATE')
        order = cursor.fetchone()
        if order is None:
            raise ValueError('대기 주문이 없습니다. 주문 API로 접수되면 다시 시도합니다.')
        job_id = order['job_id']
        # 잠금을 기다리는 동안 다른 스캐너가 주문을 배정했을 경우 재검사합니다.
        cursor.execute('SELECT status FROM ORDERS WHERE job_id = %s FOR UPDATE', (job_id,))
        if cursor.fetchone()['status'] != 'PENDING':
            raise ValueError('다른 스캐너가 주문을 배정했습니다. 다시 시도합니다.')
        cursor.execute('SELECT mapping_id FROM JOB_PALLET_MAPPINGS WHERE job_id = %s AND is_active = TRUE FOR UPDATE', (job_id,))
        if cursor.fetchall():
            raise ValueError('다른 스캐너가 주문을 배정했습니다. 다시 시도합니다.')
        cursor.execute(
            'INSERT INTO JOB_PALLET_MAPPINGS '
            '(job_id, pallet_id, qr_read_success, body_detected, is_active) '
            'VALUES (%s, %s, TRUE, FALSE, TRUE)', (job_id, pallet_id))
        mapping_id = cursor.lastrowid
        cursor.execute("UPDATE PALLETS SET current_status = 'MAPPED' WHERE pallet_id = %s", (pallet_id,))
        cursor.execute("UPDATE ORDERS SET status = 'IN_PROGRESS' WHERE job_id = %s", (job_id,))
        connection.commit()
        return mapping_id, job_id, True
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def assign_order(connection, pallet_id, job_id):
    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        # 동일 주문/팔레트에 대한 이 프로그램의 동시 매핑을 직렬화합니다.
        cursor.execute("SELECT job_id, status FROM ORDERS WHERE job_id = %s FOR UPDATE", (job_id,))
        order = cursor.fetchone()
        if order is None:
            raise ValueError(f"존재하지 않는 주문: {job_id}")
        cursor.execute("SELECT pallet_id, current_status FROM PALLETS WHERE pallet_id = %s FOR UPDATE", (pallet_id,))
        pallet = cursor.fetchone()
        if pallet is None:
            raise ValueError(f"등록되지 않은 팔레트: {pallet_id}")
        cursor.execute(
            "SELECT mapping_id, job_id, pallet_id, unmapped_at FROM JOB_PALLET_MAPPINGS "
            "WHERE is_active = TRUE AND (pallet_id = %s OR job_id = %s) FOR UPDATE",
            (pallet_id, job_id),
        )
        active = cursor.fetchall()
        if active:
            if (len(active) == 1 and active[0]['pallet_id'] == pallet['pallet_id']
                    and active[0]['job_id'] == order['job_id']
                    and active[0]['unmapped_at'] is None
                    and order['status'] == 'IN_PROGRESS'):
                connection.commit()
                return active[0]['mapping_id'], False
            raise ValueError("팔레트 또는 주문에 이미 활성 매핑이 있습니다. 기존 연결을 확인하세요.")
        if order['status'] != 'PENDING':
            raise ValueError(f"대기(PENDING) 주문만 새로 연결할 수 있습니다: {order['status']}")
        if pallet['current_status'] != 'READY':
            raise ValueError(f"사용 가능(READY) 팔레트만 연결할 수 있습니다: {pallet['current_status']}")
        cursor.execute(
            "INSERT INTO JOB_PALLET_MAPPINGS "
            "(job_id, pallet_id, qr_read_success, body_detected, is_active) "
            "VALUES (%s, %s, TRUE, FALSE, TRUE)", (job_id, pallet_id),
        )
        mapping_id = cursor.lastrowid
        cursor.execute("UPDATE PALLETS SET current_status = 'MAPPED' WHERE pallet_id = %s", (pallet_id,))
        cursor.execute("UPDATE ORDERS SET status = 'IN_PROGRESS' WHERE job_id = %s", (job_id,))
        connection.commit()
        return mapping_id, True
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
