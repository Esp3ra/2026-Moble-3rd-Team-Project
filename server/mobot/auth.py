from hashlib import sha256
from secrets import token_urlsafe

from flask import request
from werkzeug.security import generate_password_hash


DUMMY_HASH = generate_password_hash(token_urlsafe(32))
ACCOUNT_SELECT = """
    SELECT a.account_id, a.login_id, a.name, a.password_hash, r.role_code, r.role_name
    FROM ACCOUNTS a JOIN ROLES r ON r.role_id = a.role_id
"""


def bearer_token():
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


def token_hash(token):
    return sha256(token.encode()).hexdigest()
