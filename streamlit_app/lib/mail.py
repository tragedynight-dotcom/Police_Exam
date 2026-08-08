from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib import error, request


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
            raise RuntimeError(
                "MAIL_HOST가 없습니다. Secrets에 MAIL_HOST를 넣어 주세요."
            )
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
                raise RuntimeError(f"EmailJS 발송 실패: {resp.status}")
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"EmailJS 발송 실패: {body or e.code}") from e


def _build_message(user: str, to: str, code: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "[DaMoa] OTP code"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(
        f"인증번호: {code}\n유효시간: 10분",
        charset="utf-8",
    )
    msg.add_alternative(
        f"""
      <div style="font-family:'Malgun Gothic',sans-serif;line-height:1.6;color:#132238">
        <h2 style="color:#0b2a4a">DaMoa 인증번호</h2>
        <p>아래 인증번호를 입력하세요.</p>
        <p style="font-size:28px;font-weight:700;letter-spacing:6px">{code}</p>
        <p>유효시간: <strong>10분</strong></p>
      </div>
        """,
        subtype="html",
        charset="utf-8",
    )
    return msg


def _send_smtp(to: str, code: str) -> None:
    host, port, user, password = _resolve_smtp()
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
            "메일 로그인 실패: MAIL_USER/MAIL_PASS와 호스트가 맞는지 확인하세요. "
            f"({e.smtp_code})"
        ) from e
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP 발송 실패: {e}") from e
    except OSError as e:
        raise RuntimeError(f"메일 서버 연결 실패({host}:{port}): {e}") from e


def send_otp_email(to: str, code: str) -> str:
    if has_smtp():
        _send_smtp(to, code)
        return "smtp"
    if has_emailjs():
        _send_emailjs(to, code)
        return "emailjs"
    raise RuntimeError(
        "메일 발송 설정이 없습니다. Streamlit Secrets에 "
        "MAIL_USER, MAIL_PASS, MAIL_HOST, MAIL_PORT를 넣어 주세요."
    )
