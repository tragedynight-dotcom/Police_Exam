from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from urllib import error, request

# Bump when uploading so we can confirm Cloud got the new file
MAIL_MODULE_VERSION = "2026-08-09-ascii4"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve()
    for p in (here.parents[2] / ".env", here.parents[1] / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


_load_dotenv()


def _env(name: str) -> str:
    val = os.environ.get(name)
    if val is not None and str(val).strip():
        return str(val).strip().strip('"').strip("'")
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is None:
            return ""
        try:
            raw = secrets[name]
        except Exception:
            raw = None
        if raw is None:
            return ""
        return str(raw).strip().strip('"').strip("'")
    except Exception:
        return ""


def has_emailjs() -> bool:
    return all(
        _env(k)
        for k in (
            "EMAILJS_SERVICE_ID",
            "EMAILJS_TEMPLATE_ID",
            "EMAILJS_PUBLIC_KEY",
            "EMAILJS_PRIVATE_KEY",
        )
    )


def has_smtp() -> bool:
    return bool(_env("MAIL_USER") and _env("MAIL_PASS"))


def is_mail_configured() -> bool:
    return has_emailjs() or has_smtp()


def _resolve_smtp() -> tuple[str, int, str, str]:
    user = _env("MAIL_USER")
    password = _env("MAIL_PASS")
    host = _env("MAIL_HOST")
    port_raw = _env("MAIL_PORT")
    port = int(port_raw) if port_raw else 0

    lower = user.lower()
    if not host:
        if lower.endswith("@gmail.com"):
            host, port = "smtp.gmail.com", port or 465
        elif lower.endswith("@naver.com"):
            host, port = "smtp.naver.com", port or 465
        elif lower.endswith("@hanmail.net") or lower.endswith("@daum.net"):
            host, port = "smtp.daum.net", port or 465
        else:
            raise RuntimeError("MAIL_HOST missing in Streamlit Secrets")
    if not port:
        port = 465
    return host, port, user, password


def _send_emailjs(to: str, code: str) -> None:
    payload = {
        "service_id": _env("EMAILJS_SERVICE_ID"),
        "template_id": _env("EMAILJS_TEMPLATE_ID"),
        "user_id": _env("EMAILJS_PUBLIC_KEY"),
        "accessToken": _env("EMAILJS_PRIVATE_KEY"),
        "template_params": {
            "to_email": to,
            "code": code,
            "from_name": "DaMoa",
        },
    }
    req = request.Request(
        "https://api.emailjs.com/api/v1.0/email/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"EmailJS failed: {resp.status}")
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"EmailJS failed: {body or e.code}") from e


def _build_message(user: str, to: str, code: str) -> EmailMessage:
    # Streamlit Cloud Linux locale is often ASCII-only.
    # Keep headers + body ASCII to avoid encode errors.
    msg = EmailMessage(policy=SMTP)
    msg["Subject"] = f"[DaMoa] verification code {code}"
    msg["From"] = user
    msg["To"] = to
    body = (
        f"DaMoa verification code: {code}\n"
        f"Valid for 10 minutes.\n"
    )
    msg.set_content(body)
    return msg


def _assert_ascii(label: str, value: str) -> None:
    try:
        value.encode("ascii")
    except UnicodeEncodeError as e:
        # Show only safe preview (no secrets): which chars are non-ascii
        bad = "".join(sorted({ch for ch in value if ord(ch) > 127}))[:12]
        raise RuntimeError(
            f"Non-ASCII in {label} ({MAIL_MODULE_VERSION}). "
            f"Remove Korean/special chars. bad={bad!r} len={len(value)}"
        ) from e


def _send_smtp(to: str, code: str) -> None:
    host, port, user, password = _resolve_smtp()
    _assert_ascii("MAIL_USER", user)
    _assert_ascii("MAIL_PASS", password)
    _assert_ascii("MAIL_HOST", host)
    _assert_ascii("recipient email", to)

    msg = _build_message(user, to, code)
    context = ssl.create_default_context()
    try:
        if port == 587:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                server.login(user, password)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            f"SMTP login failed ({MAIL_MODULE_VERSION}) code={e.smtp_code}"
        ) from e
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP send failed ({MAIL_MODULE_VERSION}): {e}") from e
    except OSError as e:
        raise RuntimeError(
            f"SMTP connect failed ({MAIL_MODULE_VERSION}) {host}:{port}: {e}"
        ) from e
    except UnicodeEncodeError as e:
        raise RuntimeError(
            f"SMTP encode failed ({MAIL_MODULE_VERSION}): {e}"
        ) from e


def send_otp_email(to: str, code: str) -> str:
    if has_smtp():
        _send_smtp(to, code)
        return "smtp"
    if has_emailjs():
        _send_emailjs(to, code)
        return "emailjs"
    raise RuntimeError(
        f"Mail not configured ({MAIL_MODULE_VERSION}). "
        "Set MAIL_USER, MAIL_PASS, MAIL_HOST, MAIL_PORT in Secrets."
    )
