from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib import error, request


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is not None and name in secrets:
            return str(secrets[name]).strip()
    except Exception:
        pass
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


def _send_emailjs(to: str, code: str) -> None:
    payload = {
        "service_id": _env("EMAILJS_SERVICE_ID"),
        "template_id": _env("EMAILJS_TEMPLATE_ID"),
        "user_id": _env("EMAILJS_PUBLIC_KEY"),
        "accessToken": _env("EMAILJS_PRIVATE_KEY"),
        "template_params": {
            "to_email": to,
            "code": code,
            "from_name": "지역 경찰 실무 역량 평가 DaMoa",
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


def _send_smtp(to: str, code: str) -> None:
    user = _env("MAIL_USER")
    password = _env("MAIL_PASS")
    host = _env("MAIL_HOST")
    port = int(_env("MAIL_PORT") or "465")

    if not host:
        if user.endswith("@gmail.com"):
            host, port = "smtp.gmail.com", 465
        elif user.endswith("@naver.com"):
            host, port = "smtp.naver.com", 465
        else:
            raise RuntimeError("MAIL_HOST를 설정해 주세요.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[DaMoa] 이메일 인증번호"
    msg["From"] = f"지역 경찰 실무 역량 평가 DaMoa <{user}>"
    msg["To"] = to
    text = f"인증번호: {code}\n유효시간: 10분"
    html = f"""
      <div style="font-family:'Malgun Gothic',sans-serif;line-height:1.6;color:#132238">
        <h2 style="color:#0b2a4a">DaMoa 인증번호</h2>
        <p>아래 인증번호를 입력하세요.</p>
        <p style="font-size:28px;font-weight:700;letter-spacing:6px">{code}</p>
        <p>유효시간: <strong>10분</strong></p>
      </div>
    """
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as server:
        server.login(user, password)
        server.sendmail(user, [to], msg.as_string())


def send_otp_email(to: str, code: str) -> str:
    if has_emailjs():
        _send_emailjs(to, code)
        return "emailjs"
    if has_smtp():
        _send_smtp(to, code)
        return "smtp"
    raise RuntimeError("메일 발송 설정이 없습니다.")
