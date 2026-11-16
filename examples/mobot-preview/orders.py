import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from secrets import compare_digest
from uuid import UUID, uuid4

from flask import Blueprint, current_app, jsonify, request


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
HEAD_CODES = {"dual": "A", "mono": "B"}
LEFT_CODES = {"grip": "a", "weld": "b", "fasten": "c"}
RIGHT_CODES = {"grip": "1", "weld": "2", "fasten": "3"}
HEAD_OPTIONS = {f"head-{part}": option for option, part in HEAD_CODES.items()}
LEFT_OPTIONS = {f"arm-{part}": option for option, part in LEFT_CODES.items()}
RIGHT_OPTIONS = {f"arm-{part}": option for option, part in RIGHT_CODES.items()}
LEGACY_HEAD_IDS = {"H1": "head-A", "H2": "head-B"}
LEGACY_LEFT_IDS = {"L1": "arm-a", "L2": "arm-b", "L3": "arm-c"}
LEGACY_RIGHT_IDS = {"R1": "arm-1", "R2": "arm-2", "R3": "arm-3"}
PART_ID_UPDATES = (
    ("ORDERS", "target_head_id", LEGACY_HEAD_IDS),
    ("ORDERS", "target_arm_l_id", LEGACY_LEFT_IDS),
    ("ORDERS", "target_arm_r_id", LEGACY_RIGHT_IDS),
    ("ASSEMBLY_LOGS", "assembled_head_id", LEGACY_HEAD_IDS),
    ("ASSEMBLY_LOGS", "assembled_arm_l_id", LEGACY_LEFT_IDS),
    ("ASSEMBLY_LOGS", "assembled_arm_r_id", LEGACY_RIGHT_IDS),
    ("INSPECTION_2ND_LOGS", "actual_head_id", LEGACY_HEAD_IDS),
)
HUBS = {
    1: {"대전", "대전시", "대전광역시", "천안", "천안시"},
    2: {"서울", "서울시", "서울특별시", "경기", "경기도"},
    3: {
        "부산", "부산시", "부산광역시", "대구", "대구시", "대구광역시",
        "울산", "울산시", "울산광역시", "경북", "경상북도", "경남", "경상남도",
    },
}
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


def initialize_orders(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8-sig")
    with closing(connect(path)) as db, db:
        # Execute complete SQL statements individually so the schema and any
        # legacy migration share one transaction, including the table rename.
        db.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(ORDERS)")}
        legacy = {"id", "request_id", "created_at", "status", *ORDER_FIELDS} == columns
        if legacy:
            db.execute("ALTER TABLE orders RENAME TO legacy_web_orders")

        statement = ""
        for line in schema.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
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


def destination_for(address):
    address = re.sub(r"^\s*(?:\(\s*\d{5}\s*\)|\d{5}(?=\s))\s*", "", address)
    regions = address.split()[:2]
    for hub, aliases in HUBS.items():
        if regions and regions[0] in aliases:
            return hub
    if regions[:2] in (["충남", "천안시"], ["충청남도", "천안시"], ["충남", "천안"], ["충청남도", "천안"]):
        return 1
    return None


def order_values(order, order_id, created_at, request_id):
    head = HEAD_CODES[order["head"]]
    left = LEFT_CODES[order["left"]]
    right = RIGHT_CODES[order["right"]]
    model_name = f"{head}{right}{left}"
    return (
        order_id, order["company"], order["phone"], order["address"], f"head-{head}",
        f"arm-{left}", f"arm-{right}", model_name, destination_for(order["address"]), created_at, request_id,
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
            inserted = db.execute(
                INSERT_ORDER + " ON CONFLICT(web_request_id) DO NOTHING",
                order_values(order, str(uuid4()), created_at, request_id),
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
    def list_orders():
        token = current_app.config["ADMIN_TOKEN"]
        if not token:
            return {"error": "관리자 조회 토큰을 설정해 주세요."}, 503

        supplied = request.headers.get("Authorization", "")
        if not compare_digest(supplied.encode(), f"Bearer {token}".encode()):
            return {"error": "관리자 인증이 필요합니다."}, 401, {"WWW-Authenticate": "Bearer"}

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

    return api
