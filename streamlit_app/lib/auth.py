from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from .db import execute, fetch_one

ALLOWED_EMAIL_DOMAIN = "police.go.kr"
AUTH_COOKIE_NAME = "damoa_auth"
AUTH_COOKIE_DAYS = 30
_SESSION_SECRET = os.environ.get(
    "DAMOASESSION_SECRET", "damoa-dev-session-secret-change-me"
)


def new_id() -> str:
    return "c" + secrets.token_hex(12)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_police_email(email: str) -> bool:
    return normalize_email(email).endswith(f"@{ALLOWED_EMAIL_DOMAIN}")


def full_police_email(local_part: str) -> str:
    local = local_part.strip().lower().replace(f"@{ALLOWED_EMAIL_DOMAIN}", "")
    return f"{local}@{ALLOWED_EMAIL_DOMAIN}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def otp_expires_at(minutes: int = 10) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def create_otp_for_user(user_id: str) -> str:
    code = generate_otp()
    execute("DELETE FROM EmailVerification WHERE userId = ?", (user_id,))
    execute(
        """
        INSERT INTO EmailVerification (id, userId, codeHash, expiresAt, attempts, createdAt)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (new_id(), user_id, hash_otp(code), otp_expires_at(), now_iso()),
    )
    return code


def verify_otp(email: str, code: str) -> tuple[bool, str]:
    user = fetch_one("SELECT * FROM User WHERE email = ?", (normalize_email(email),))
    if not user:
        return False, "가입되지 않은 이메일입니다."

    row = fetch_one(
        """
        SELECT * FROM EmailVerification
        WHERE userId = ?
        ORDER BY createdAt DESC
        LIMIT 1
        """,
        (user["id"],),
    )
    if not row:
        return False, "인증번호가 없습니다. 다시 요청해 주세요."

    if row["attempts"] >= 5:
        return False, "인증 시도 횟수를 초과했습니다. 다시 요청해 주세요."

    if parse_dt(row["expiresAt"]) < datetime.now(timezone.utc):
        return False, "인증번호가 만료되었습니다. 다시 요청해 주세요."

    execute(
        "UPDATE EmailVerification SET attempts = attempts + 1 WHERE id = ?",
        (row["id"],),
    )

    if hash_otp(code.strip()) != row["codeHash"]:
        return False, "인증번호가 올바르지 않습니다."

    execute(
        """
        UPDATE User SET isVerified = 1, updatedAt = ? WHERE id = ?
        """,
        (now_iso(), user["id"]),
    )
    execute("DELETE FROM EmailVerification WHERE userId = ?", (user["id"],))
    return True, "인증이 완료되었습니다."


def register_user(
    name: str, email: str, password: str, organization: str | None
) -> tuple[bool, str, str | None]:
    email = normalize_email(email)
    if not is_police_email(email):
        return False, "@police.go.kr 이메일만 사용할 수 있습니다.", None
    if len(name.strip()) < 2:
        return False, "닉네임은 2자 이상이어야 합니다.", None
    if len(password) < 8:
        return False, "비밀번호는 8자 이상이어야 합니다.", None

    existing = fetch_one("SELECT * FROM User WHERE email = ?", (email,))
    pw_hash = hash_password(password)
    ts = now_iso()

    if existing:
        if existing["isVerified"]:
            return False, "이미 가입된 이메일입니다.", None
        execute(
            """
            UPDATE User
            SET name = ?, organization = ?, passwordHash = ?, updatedAt = ?
            WHERE id = ?
            """,
            (name.strip(), organization or None, pw_hash, ts, existing["id"]),
        )
        user_id = existing["id"]
    else:
        user_id = new_id()
        execute(
            """
            INSERT INTO User
            (id, email, passwordHash, name, organization, role, isVerified, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, 'user', 0, ?, ?)
            """,
            (user_id, email, pw_hash, name.strip(), organization or None, ts, ts),
        )

    code = create_otp_for_user(user_id)
    return True, "인증번호를 발급했습니다.", code


def public_user(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "isVerified": bool(row["isVerified"]),
    }


def get_user_by_id(user_id: str) -> dict | None:
    user = fetch_one("SELECT * FROM User WHERE id = ?", (user_id,))
    if not user or not user["isVerified"]:
        return None
    return public_user(user)


def make_auth_token(user_id: str) -> str:
    sig = hmac.new(
        _SESSION_SECRET.encode("utf-8"),
        user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{user_id}.{sig}"


def user_id_from_auth_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    user_id, sig = token.split(".", 1)
    if not user_id or not sig:
        return None
    expected = hmac.new(
        _SESSION_SECRET.encode("utf-8"),
        user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id


def login_user(email: str, password: str) -> tuple[dict | None, str, bool]:
    email = normalize_email(email)
    if not is_police_email(email):
        return None, "@police.go.kr 이메일만 사용할 수 있습니다.", False

    user = fetch_one("SELECT * FROM User WHERE email = ?", (email,))
    if not user or not verify_password(password, user["passwordHash"]):
        return None, "이메일 또는 비밀번호가 올바르지 않습니다.", False

    if not user["isVerified"]:
        return None, "이메일 인증이 필요합니다.", True

    return public_user(user), "로그인 성공", False


def forgot_password(email: str) -> tuple[bool, str, str | None]:
    email = normalize_email(email)
    user = fetch_one(
        "SELECT * FROM User WHERE email = ? AND isVerified = 1",
        (email,),
    )
    if not user:
        return False, "인증된 계정을 찾을 수 없습니다.", None
    code = create_otp_for_user(user["id"])
    return True, "비밀번호 재설정 인증번호를 발급했습니다.", code


def reset_password(email: str, code: str, password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "비밀번호는 8자 이상이어야 합니다."

    ok, msg = verify_otp(email, code)
    if not ok:
        return False, msg

    user = fetch_one("SELECT * FROM User WHERE email = ?", (normalize_email(email),))
    if not user:
        return False, "사용자를 찾을 수 없습니다."

    execute(
        "UPDATE User SET passwordHash = ?, updatedAt = ? WHERE id = ?",
        (hash_password(password), now_iso(), user["id"]),
    )
    return True, "비밀번호가 변경되었습니다. 로그인해주세요."
