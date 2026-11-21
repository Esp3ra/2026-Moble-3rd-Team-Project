import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request
from auth import create_admin_required


CONTACT_LIMITS = {"company": 100, "phone": 40, "address": 300}
ORDER_FIELDS = ("company", "phone", "address", "head", "left", "right")
PAYMENT_FIELDS = ("payment_method", "payment_bank", "installment_months", "card_issuer")
BANKS = {"kb": "KB국민은행", "shinhan": "신한은행", "woori": "우리은행"}
CARD_ISSUERS = {
    "kb": BANKS["kb"],
    "nh": "NH농협은행",
    "shinhan": BANKS["shinhan"],
    "ibk": "IBK기업은행",
}
SCHEMA_PATH = Path(__file__).with_name("smart_factory_sqlite.sql")
HEAD_CODES = {"dual": "H1", "mono": "H2"}
LEFT_CODES = {"grip": "L1", "weld": "L2", "fasten": "L3"}
RIGHT_CODES = {"grip": "R1", "weld": "R2", "fasten": "R3"}
HEAD_OPTIONS = {part: option for option, part in HEAD_CODES.items()}
LEFT_OPTIONS = {part: option for option, part in LEFT_CODES.items()}
RIGHT_OPTIONS = {part: option for option, part in RIGHT_CODES.items()}
LEGACY_HEAD_IDS = {"head-A": "H1", "head-B": "H2"}
LEGACY_LEFT_IDS = {"arm-a": "L1", "arm-b": "L2", "arm-c": "L3"}
LEGACY_RIGHT_IDS = {"arm-1": "R1", "arm-2": "R2", "arm-3": "R3"}
PART_ID_UPDATES = (
    ("ORDERS", "target_head_id", LEGACY_HEAD_IDS),
    ("ORDERS", "target_arm_l_id", LEGACY_LEFT_IDS),
    ("ORDERS", "target_arm_r_id", LEGACY_RIGHT_IDS),
    ("ASSEMBLY_LOGS", "assembled_head_id", LEGACY_HEAD_IDS),
    ("ASSEMBLY_LOGS", "assembled_arm_l_id", LEGACY_LEFT_IDS),
    ("ASSEMBLY_LOGS", "assembled_arm_r_id", LEGACY_RIGHT_IDS),
    ("INSPECTION_2ND_LOGS", "actual_head_id", LEGACY_HEAD_IDS),
)
HUBS = {3: {"서울", "서울시", "서울특별시"},
        4: {"평택", "평택시"}, 5: {"수원", "수원시"}}
ORDER_COLUMNS = """
    job_id, company_name, phone, address, target_head_id, target_arm_l_id,
    target_arm_r_id, model_name, target_station, status, created_at,
    payment_method, payment_bank, installment_months, card_issuer
"""
INSERT_ORDER = """
    INSERT INTO ORDERS (
        job_id, company_name, phone, address, target_head_id, target_arm_l_id,
        target_arm_r_id, model_name, target_station, created_at, web_request_id,
        payment_method, payment_bank, installment_months, card_issuer
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def next_order_number(db):
    return db.execute(
        """
        SELECT COALESCE(MAX(CAST(SUBSTR(job_id, 7) AS INTEGER)), 0) + 1
        FROM ORDERS
        WHERE job_id GLOB 'mobot-[0-9]*'
          AND SUBSTR(job_id, 7) NOT GLOB '*[^0-9]*'
        """
    ).fetchone()[0]


def initialize_orders(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8-sig")
    with closing(connect(path)) as db, db:
        migrate_agv_destinations(db)
        migrate_remote_modes(db)
        # Execute complete SQL statements individually so the schema and any
        # legacy migration share one transaction, including the table rename.
        db.execute("BEGIN IMMEDIATE")
        pallets_existed = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'PALLETS' COLLATE NOCASE"
        ).fetchone() is not None
        columns = {row["name"] for row in db.execute("PRAGMA table_info(ORDERS)")}
        legacy = {"id", "request_id", "created_at", "status", *ORDER_FIELDS} == columns
        if legacy:
            db.execute("ALTER TABLE orders RENAME TO legacy_web_orders")

        statement = ""
        for line in schema.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                # Seed pallets only when creating their table for the first time.
                pallet_seed = re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\s+PALLETS\b", statement, re.IGNORECASE)
                if not (pallets_existed and pallet_seed):
                    db.execute(statement)
                statement = ""

        if columns and not legacy:
            for field in PAYMENT_FIELDS:
                if field not in columns:
                    kind = "INTEGER" if field == "installment_months" else "TEXT"
                    db.execute(f"ALTER TABLE ORDERS ADD COLUMN {field} {kind}")

        if legacy:
            for row in db.execute("SELECT * FROM legacy_web_orders"):
                db.execute(INSERT_ORDER, order_values(row, row["id"], row["created_at"], row["request_id"]))
            db.execute("DROP TABLE legacy_web_orders")

        for table, column, replacements in PART_ID_UPDATES:
            db.executemany(
                f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                ((new_id, old_id) for old_id, new_id in replacements.items()),
            )
        db.execute("""UPDATE ORDERS SET model_name=target_head_id || '-' || target_arm_l_id || '-' || target_arm_r_id
            WHERE target_head_id IN ('H1','H2') AND target_arm_l_id IN ('L1','L2','L3')
            AND target_arm_r_id IN ('R1','R2','R3')""")

        number = next_order_number(db)
        for row in db.execute("SELECT job_id FROM ORDERS ORDER BY julianday(created_at), job_id").fetchall():
            if not re.fullmatch(r"mobot-[1-9][0-9]*", row["job_id"]):
                db.execute("UPDATE ORDERS SET job_id = ? WHERE job_id = ?", (f"mobot-{number}", row["job_id"]))
                number += 1


def migrate_remote_modes(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='EQUIPMENT_RUNS'").fetchone()
    if not row or "CHECK (mode = 'DEMO')" not in row['sql']:
        return
    indexes = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='EQUIPMENT_RUNS' AND sql IS NOT NULL").fetchall()
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        sql = row['sql'].replace('CREATE TABLE EQUIPMENT_RUNS', 'CREATE TABLE EQUIPMENT_RUNS_NEW', 1).replace("CHECK (mode = 'DEMO')", "CHECK (mode IN ('DEMO', 'REMOTE'))")
        db.execute(sql)
        db.execute('INSERT INTO EQUIPMENT_RUNS_NEW SELECT * FROM EQUIPMENT_RUNS')
        db.execute('DROP TABLE EQUIPMENT_RUNS')
        db.execute('ALTER TABLE EQUIPMENT_RUNS_NEW RENAME TO EQUIPMENT_RUNS')
        for index in indexes:
            db.execute(index['sql'])
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.IntegrityError('원격 장비 스키마 변경 후 외래키 검사 실패')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def migrate_agv_destinations(db):
    """Rebuild the old station CHECK without renaming its referenced table."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ORDERS'").fetchone()
    if not row or not re.search(r'target_station\s+IN\s*\(1,\s*2,\s*3\)', row['sql'], re.I):
        return
    indexes = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='ORDERS' AND sql IS NOT NULL").fetchall()
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        sql = re.sub(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?ORDERS', 'CREATE TABLE ORDERS_AGV_NEW', row['sql'], count=1, flags=re.I)
        sql = re.sub(r'target_station\s+IN\s*\(1,\s*2,\s*3\)', 'target_station IN (3, 4, 5, 6, 7)', sql, flags=re.I)
        db.execute(sql)
        names = [r['name'] for r in db.execute('PRAGMA table_info(ORDERS)')]
        columns = ','.join('"' + name + '"' for name in names)
        # Old hub numbers have different meanings. Recompute from addresses.
        selected = ','.join('NULL' if name == 'target_station' else '"' + name + '"' for name in names)
        db.execute(f'INSERT INTO ORDERS_AGV_NEW ({columns}) SELECT {selected} FROM ORDERS')
        for order in db.execute('SELECT job_id,address FROM ORDERS_AGV_NEW').fetchall():
            db.execute('UPDATE ORDERS_AGV_NEW SET target_station=? WHERE job_id=?',
                       (destination_for(order['address'] or ''), order['job_id']))
        db.execute('DROP TABLE ORDERS')
        db.execute('ALTER TABLE ORDERS_AGV_NEW RENAME TO ORDERS')
        for index in indexes:
            db.execute(index['sql'])
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.IntegrityError('AGV 목적지 변경 후 외래키 검사 실패')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def destination_for(address):
    address = re.sub(r"^\s*(?:\(\s*\d{5}\s*\)|\d{5}(?=\s))\s*", "", address)
    regions = address.split()[:2]
    if regions and regions[0] in HUBS[3]:
        return 3
    city = regions[1] if len(regions) > 1 and regions[0] in {'경기', '경기도'} else (regions[0] if regions else '')
    for hub in (4, 5):
        if city in HUBS[hub]:
            return hub
    return None


def order_values(order, order_id, created_at, request_id):
    head = HEAD_CODES[order["head"]]
    left = LEFT_CODES[order["left"]]
    right = RIGHT_CODES[order["right"]]
    model_name = f"{head}-{left}-{right}"
    return (
        order_id, order["company"], order["phone"], order["address"], head,
        left, right, model_name, destination_for(order["address"]), created_at, request_id,
        *(order[field] if field in order.keys() else None for field in PAYMENT_FIELDS),
    )


def order_response(row):
    return {
        "id": row["job_id"],
        "created_at": row["created_at"],
        "company": row["company_name"],
        "phone": row["phone"],
        "address": row["address"],
        "head": HEAD_OPTIONS.get(row["target_head_id"], row["target_head_id"]),
        "left": LEFT_OPTIONS.get(row["target_arm_l_id"], row["target_arm_l_id"]),
        "right": RIGHT_OPTIONS.get(row["target_arm_r_id"], row["target_arm_r_id"]),
        "status": "received" if row["status"] == "PENDING" else row["status"],
        "factory_status": row["status"],
        "model_name": row["model_name"],
        "target_head_id": row["target_head_id"],
        "target_arm_l_id": row["target_arm_l_id"],
        "target_arm_r_id": row["target_arm_r_id"],
        "target_station": row["target_station"],
        **{field: row[field] for field in PAYMENT_FIELDS},
    }


def create_order_api(heads, arms):
    api = Blueprint("orders", __name__, url_prefix="/api")
    head_codes = {code for code, _, _ in heads}
    arm_codes = {code for code, _, _ in arms}
    options = {"head": head_codes, "left": arm_codes, "right": arm_codes}

    admin_required = create_admin_required(connect)

    @api.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.post("/orders")
    def submit_order():
        if not request.is_json:
            return {"error": "JSON 형식으로 전송해 주세요."}, 415

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return {"error": "주문 정보 형식을 확인해 주세요."}, 400

        order = {}
        for field, limit in CONTACT_LIMITS.items():
            value = payload.get(field)
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
                return {"error": "주문자 정보를 확인해 주세요.", "field": field}, 400
            order[field] = value.strip()

        for slot, allowed in options.items():
            value = payload.get(slot)
            if isinstance(value,str):
                value = {"head":HEAD_OPTIONS,"left":LEFT_OPTIONS,"right":RIGHT_OPTIONS}[slot].get(value,value)
            if not isinstance(value, str) or value not in allowed:
                return {"error": "선택한 모듈을 확인해 주세요.", "field": slot}, 400
            order[slot] = value

        method, bank, months, issuer = (payload.get(field) for field in PAYMENT_FIELDS)
        if method == "card":
            if not isinstance(issuer, str) or issuer not in CARD_ISSUERS:
                return {"error": "카드사를 선택해 주세요."}, 400
            if type(months) is not int or months < 1 or months > 2**53 - 1 or bank is not None:
                return {"error": "할부 개월을 1 이상의 정수로 입력해 주세요."}, 400
        elif method == "transfer":
            if not isinstance(bank, str) or bank not in BANKS or months is not None or issuer is not None:
                return {"error": "계좌이체 은행을 선택해 주세요."}, 400
        elif any(field in payload for field in PAYMENT_FIELDS):
            return {"error": "결제 수단을 선택해 주세요."}, 400
        order.update(payment_method=method, payment_bank=bank, installment_months=months, card_issuer=issuer)

        try:
            request_id = str(UUID(payload.get("request_id", "")))
        except (AttributeError, TypeError, ValueError):
            return {"error": "주문 요청 번호가 올바르지 않습니다."}, 400

        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with closing(connect(current_app.config["ORDER_DB"])) as db, db:
            db.execute("BEGIN IMMEDIATE")
            next_number = next_order_number(db)
            inserted = db.execute(
                INSERT_ORDER + " ON CONFLICT(web_request_id) DO NOTHING",
                order_values(order, f"mobot-{next_number}", created_at, request_id),
            ).rowcount
            row = db.execute(
                f"SELECT {ORDER_COLUMNS} FROM ORDERS WHERE web_request_id = ?", (request_id,)
            ).fetchone()
            saved = order_response(row)
            if any(saved[field] != value for field, value in order.items()):
                return {"error": "이 요청 번호로 다른 주문이 이미 접수되었습니다."}, 409

        return {field: saved[field] for field in ("id", "status", "created_at")}, (
            201 if inserted else 200
        )

    @api.get("/orders")
    @admin_required
    def list_orders():
        with closing(connect(current_app.config["ORDER_DB"])) as db:
            rows = db.execute(
                f"""
                SELECT {ORDER_COLUMNS}
                FROM ORDERS
                ORDER BY julianday(created_at) DESC, job_id DESC
                LIMIT 100
                """
            ).fetchall()
        return jsonify(orders=[order_response(row) for row in rows])

    @api.post("/orders/<order_id>/cancel")
    @admin_required
    def cancel_order(order_id):
        with closing(connect(current_app.config["ORDER_DB"])) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT {ORDER_COLUMNS} FROM ORDERS WHERE job_id = ?", (order_id,)
            ).fetchone()
            if row is None:
                return {"error": "주문을 찾을 수 없습니다."}, 404
            if row["status"] == "CANCELED":
                return {"order": order_response(row)}
            if row["status"] != "PENDING":
                return {"error": "생산 대기 중인 주문만 드롭할 수 있습니다."}, 409
            running = db.execute(
                "SELECT 1 FROM EQUIPMENT_RUNS WHERE job_id = ? AND finished_at IS NULL", (order_id,)
            ).fetchone()
            if running:
                return {"error": "장비 회차가 진행 중인 주문입니다. 먼저 데모를 정지해 주세요."}, 409
            mapped = db.execute(
                """
                SELECT 1 FROM JOB_PALLET_MAPPINGS
                WHERE job_id = ? AND (is_active = 1 OR unmapped_at IS NULL)
                LIMIT 1
                """, (order_id,)
            ).fetchone()
            if mapped:
                return {"error": "팔레트에 배정된 주문입니다. 공정과 배정 상태를 먼저 확인해 주세요."}, 409
            db.execute("UPDATE ORDERS SET status = 'CANCELED' WHERE job_id = ?", (order_id,))
            row = db.execute(
                f"SELECT {ORDER_COLUMNS} FROM ORDERS WHERE job_id = ?", (order_id,)
            ).fetchone()
        return {"order": order_response(row)}

    @api.get("/pallets")
    @admin_required
    def list_pallets():
        with closing(connect(current_app.config["ORDER_DB"])) as db:
            rows = db.execute(
                """
                SELECT pallet_id, current_status, cycle_count,
                       strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AS updated_at
                FROM PALLETS
                ORDER BY pallet_id
                """
            ).fetchall()
        return jsonify(pallets=[dict(row) for row in rows])

    return api
