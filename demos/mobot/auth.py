from contextlib import closing
from functools import wraps
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from time import time

from flask import Blueprint, current_app, g, request
from werkzeug.security import check_password_hash, generate_password_hash


SESSION_SECONDS = 8 * 60 * 60
DUMMY_HASH = generate_password_hash(token_urlsafe(32))
ACCOUNT_SELECT = """
    SELECT a.account_id, a.login_id, a.name, a.password_hash, r.role_code, r.role_name
    FROM ACCOUNTS a JOIN ROLES r ON r.role_id = a.role_id
"""


def account_response(row):
    return {key: row[key] for key in ("account_id", "login_id", "name", "role_code", "role_name")}


def bearer_token():
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


def token_hash(token):
    return sha256(token.encode()).hexdigest()


def create_admin_required(connect):
    def admin_required(view):
        @wraps(view)
        def authenticated(*args, **kwargs):
            token = bearer_token()
            legacy_token = current_app.config["ADMIN_TOKEN"]
            if token and legacy_token and compare_digest(token.encode(), legacy_token.encode()):
                g.admin_account = None
                return view(*args, **kwargs)
            with closing(connect(current_app.config["ORDER_DB"])) as db:
                row = db.execute(
                    ACCOUNT_SELECT + """
                    JOIN ADMIN_SESSIONS s ON s.account_id = a.account_id
                    WHERE s.token_hash = ? AND s.expires_at > ?
                      AND r.role_code IN ('ADMIN', 'OPERATOR')
                    """, (token_hash(token), int(time()))
                ).fetchone() if token else None
            if row is None:
                return {"error": "로그인이 필요하거나 세션이 만료되었습니다."}, 401, {"WWW-Authenticate": "Bearer"}
            g.admin_account = account_response(row)
            return view(*args, **kwargs)
        return authenticated
    return admin_required


def create_auth_api(connect):
    api = Blueprint("auth", __name__, url_prefix="/api/auth")
    admin_required = create_admin_required(connect)

    @api.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.post("/login")
    def login():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return {"error": "아이디와 비밀번호를 입력해 주세요."}, 400
        login_id, password = payload.get("login_id"), payload.get("password")
        if not isinstance(login_id, str) or not 1 <= len(login_id.strip()) <= 64:
            return {"error": "아이디와 비밀번호를 입력해 주세요."}, 400
        if not isinstance(password, str) or not 1 <= len(password) <= 256:
            return {"error": "아이디와 비밀번호를 입력해 주세요."}, 400
        with closing(connect(current_app.config["ORDER_DB"])) as db, db:
            row = db.execute(ACCOUNT_SELECT + " WHERE a.login_id = ?", (login_id.strip(),)).fetchone()
            valid = check_password_hash(row["password_hash"] if row else DUMMY_HASH, password)
            if not valid or row is None or row["role_code"] not in ("ADMIN", "OPERATOR"):
                return {"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, 401
            now = int(time())
            token = token_urlsafe(32)
            expires_at = now + SESSION_SECONDS
            db.execute("DELETE FROM ADMIN_SESSIONS WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO ADMIN_SESSIONS (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash(token), row["account_id"], now, expires_at),
            )
        return {"token": token, "expires_at": expires_at, "account": account_response(row)}

    @api.get("/me")
    @admin_required
    def me():
        if g.admin_account is None:
            return {"error": "계정으로 로그인해 주세요."}, 401
        return {"account": g.admin_account}

    @api.post("/logout")
    @admin_required
    def logout():
        with closing(connect(current_app.config["ORDER_DB"])) as db, db:
            db.execute("DELETE FROM ADMIN_SESSIONS WHERE token_hash = ?", (token_hash(bearer_token()),))
        return "", 204

    return api
