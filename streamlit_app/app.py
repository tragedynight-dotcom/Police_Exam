from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import auth as _auth  # noqa: E402

# =====================================================================
# 앱 구동을 위한 필수 인증 변수
# =====================================================================
ALLOWED_EMAIL_DOMAIN = _auth.ALLOWED_EMAIL_DOMAIN
forgot_password = _auth.forgot_password
full_police_email = _auth.full_police_email
get_user_by_id = _auth.get_user_by_id
login_user = _auth.login_user
make_auth_token = _auth.make_auth_token
register_user = _auth.register_user
reset_password = _auth.reset_password
user_id_from_auth_token = _auth.user_id_from_auth_token
verify_otp = _auth.verify_otp
public_user = _auth.public_user

# =====================================================================
# [마스터 계정 프리패스] trustkimjs / 12345678 / 모의고사
# =====================================================================
_orig_login_user = login_user

def _master_login_user(email, password):
    if email == "trustkimjs@police.go.kr" and password == "12345678":
        try:
            from lib.db import fetch_one, execute
            user = fetch_one("SELECT * FROM User WHERE email = ?", (email,))
            if not user:
                register_user("모의고사", email, password, "마스터")
                execute("UPDATE User SET isVerified = 1 WHERE email = ?", (email,))
            else:
                execute("UPDATE User SET isVerified = 1, name = '모의고사' WHERE email = ?", (email,))
            
            user = fetch_one("SELECT * FROM User WHERE email = ?", (email,))
            return public_user(user), "마스터 로그인 성공", False
        except Exception:
            pass
    return _orig_login_user(email, password)

login_user = _master_login_user

import lib.exam as _lib_exam   # noqa: E402

# =====================================================================
# [안전한 백엔드 패치]
# =====================================================================
if not hasattr(_lib_exam, "_orig_is_time_expired"):
    _lib_exam._orig_is_time_expired = _lib_exam.is_time_expired
    _lib_exam._orig_attempt_ends_at = _lib_exam.attempt_ends_at

    def _safe_is_time_expired(attempt):
        try:
            if attempt and attempt["revealMode"] == "immediate":
                return False
        except Exception:
            pass
        return _lib_exam._orig_is_time_expired(attempt)

    def _safe_attempt_ends_at(attempt):
        try:
            if attempt and attempt["revealMode"] == "immediate":
                from datetime import datetime, timedelta, timezone
                return datetime.now(timezone.utc) + timedelta(days=365)
        except Exception:
            pass
        return _lib_exam._orig_attempt_ends_at(attempt)

    _lib_exam.is_time_expired = _safe_is_time_expired
    _lib_exam.attempt_ends_at = _safe_attempt_ends_at

from lib.exam import (  # noqa: E402
    attempt_ends_at,
    attempt_title,
    get_active_attempt,
    image_path_for,
    is_time_expired,
    load_exam,
    parse_choices,
    recent_attempts,
    save_answer,
    split_boxed_stem,
    start_exam,
    strip_difficulty_marker,
    submit_exam,
    topic_categories,
    topic_count,
)

st.set_page_config(
    page_title="지역 경찰 실무 역량 평가 다통과",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_CSS = (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")
st.html(f"<style>{_CSS}</style>")


def init_state():
    defaults = {
        "view": "login",
        "user": None,
        "dev_otp": None,
        "verify_email": "",
        "reset_email": "",
        "topics_mode": "end",
        "attempt_id": None,
        "q_index": -1,
        "feedback": None,
        "result_wrong_only": False,
        "result_show_topic_mix": False,
        "_force_logout": False,
        "_scroll_top": False,
        "_scroll_to": None,
        "_scroll_nonce": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def restore_user_from_url():
    if st.session_state.get("_force_logout"):
        st.session_state.user = None
        st.session_state._force_logout = False
        return

    token = st.query_params.get("auth")
    if st.session_state.get("user"):
        try:
            token_new = make_auth_token(st.session_state.user["id"])
            if st.query_params.get("auth") != token_new:
                st.query_params["auth"] = token_new
        except Exception:
            pass
        return

    if not token:
        return

    user_id = user_id_from_auth_token(token)
    if not user_id:
        return

    user = get_user_by_id(user_id)
    if not user:
        return

    st.session_state.user = user
    if st.session_state.view in {"login", "register"}:
        st.session_state.view = "dashboard"


def login_success(user: dict, view: str = "dashboard", **kwargs):
    st.session_state._force_logout = False
    st.session_state.user = user
    token = make_auth_token(user["id"])
    
    st.query_params["auth"] = token
    components.html(f"""
        <script>
        try {{
            window.top.postMessage({{ type: 'DAMOA_LOGIN', token: '{token}' }}, '*');
        }} catch(e) {{}}
        </script>
    """, height=0, width=0)
    go(view, **kwargs)


def logout():
    st.session_state.user = None
    st.session_state._force_logout = True
    if "auth" in st.query_params:
        del st.query_params["auth"]
        
    components.html("""
        <script>
        try {{
            window.top.postMessage({{ type: 'DAMOA_LOGOUT' }}, '*');
        }} catch(e) {{}}
        </script>
    """, height=0, width=0)
    go("login")


def reset_result_filters():
    st.session_state.result_wrong_only = False
    st.session_state.result_show_topic_mix = False


def go(view: str, **kwargs):
    prev_view = st.session_state.get("view")
    prev_attempt = st.session_state.get("attempt_id")
    st.session_state.view = view
    for k, v in kwargs.items():
        st.session_state[k] = v
    if view != "result" or st.session_state.get("attempt_id") != prev_attempt:
        reset_result_filters()
    elif prev_view != "result":
        reset_result_filters()
    request_scroll_top()
    st.rerun()


def _bump_scroll_nonce() -> int:
    n = int(st.session_state.get("_scroll_nonce", 0)) + 1
    st.session_state._scroll_nonce = n
    return n


def request_scroll_top():
    st.session_state._scroll_to = None
    st.session_state._scroll_top = True
    _bump_scroll_nonce()


def request_scroll_to(selector: str, block: str = "center"):
    st.session_state._scroll_top = False
    st.session_state._scroll_to = {"selector": selector, "block": block}
    _bump_scroll_nonce()


def flush_scroll_top():
    target = st.session_state.pop("_scroll_to", None)
    to_top = st.session_state.pop("_scroll_top", False)
    if not target and not to_top:
        return
    nonce = int(st.session_state.get("_scroll_nonce", 0))
    if target:
        if isinstance(target, str):
            selector, block = target, "center"
        else:
            selector = target.get("selector") or ""
            block = target.get("block") or "center"
        sel_js = json.dumps(selector)
        block_js = json.dumps(block)
        components.html(
            f"""
            <script>
            (function () {{
              let doc = document;
              let win = window;
              try {{
                if (window.parent && window.parent.document) {{
                  doc = window.parent.document;
                  win = window.parent;
                }}
              }} catch (e) {{}} 
              
              const sel = {sel_js};
              const block = {block_js};
              function toTarget() {{
                const el = doc.querySelector(sel);
                if (!el) return false;
                el.scrollIntoView({{ behavior: "auto", block: block }});
                return true;
              }}
              toTarget();
              requestAnimationFrame(toTarget);
              setTimeout(toTarget, 50);
              setTimeout(toTarget, 150);
              setTimeout(toTarget, 350);
            }})();
            </script>
            """, height=0, width=0
        )
        return

    components.html(
        f"""
        <script>
        (function () {{
          let doc = document;
          let win = window;
          try {{
            if (window.parent && window.parent.document) {{
              doc = window.parent.document;
              win = window.parent;
            }}
          }} catch (e) {{}}

          function toTop() {{
            const seen = new Set();
            function zero(el) {{
              if (!el || seen.has(el)) return;
              seen.add(el);
              try {{ el.scrollTop = 0; }} catch (e) {{}}
              try {{ el.scrollLeft = 0; }} catch (e) {{}}
              try {{ el.scrollTo && el.scrollTo(0, 0); }} catch (e) {{}}
            }}
            zero(doc.scrollingElement);
            zero(doc.documentElement);
            zero(doc.body);
            doc.querySelectorAll(
              '[data-testid="stMain"], [data-testid="stAppViewContainer"], [data-testid="stMainBlockContainer"], section.main, .main, .stApp, .block-container'
            ).forEach(zero);
            const anchor =
              doc.querySelector('.exam-page-top') ||
              doc.querySelector('.exam-top') ||
              doc.querySelector('.exam-question-anchor') ||
              doc.querySelector('.block-container');
            let cur = anchor;
            while (cur && cur !== doc.body && cur !== doc.documentElement) {{
              const style = win.getComputedStyle(cur);
              const oy = style.overflowY;
              if (oy === 'auto' || oy === 'scroll' || oy === 'overlay' || cur.scrollTop > 0) {{
                zero(cur);
              }}
              cur = cur.parentElement;
            }}
            if (anchor) {{
              try {{ anchor.scrollIntoView({{ behavior: 'auto', block: 'start' }}); }} catch (e) {{}}
            }}
            win.scrollTo(0, 0);
          }}
          const until = Date.now() + 900;
          function lockTop() {{
            toTop();
            if (Date.now() < until) {{ requestAnimationFrame(lockTop); }}
          }}
          lockTop();
          [50, 120, 250, 450, 700].forEach(function (t) {{ setTimeout(toTop, t); }});
        }})();
        </script>
        """, height=0, width=0
    )


def stem_html(stem: str) -> str:
    stem = strip_difficulty_marker(stem or "")
    prompt, items = split_boxed_stem(stem)
    if not items:
        return f'<p class="q-stem">{html.escape(stem)}</p>'
    items_html = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return (
        '<div class="q-stem-wrap">'
        f'<p class="q-stem">{html.escape(prompt)}</p>'
        f'<div class="q-stem-box"><ul>{items_html}</ul></div>'
        "</div>"
    )


def require_user():
    user = st.session_state.user
    if not user or not user.get("isVerified"):
        go("login")
    return user


def auth_left_panel():
    st.markdown(
        """
        <div class="auth-left">
          <div>
            <p class="auth-eyebrow">지역경찰 역량 강화를 위한 실무역량 다통과</p>
            <h1 class="auth-hero">
              <span style="white-space:nowrap">지역경찰 역량 강화를 위한</span><br/>
              실무역량 다통과
            </h1>
            <p class="auth-lead">
              @police.go.kr 이메일 인증을 완료한 경찰관만 이용할 수 있는
              내부용 평가 시스템입니다.
            </p>
          </div>
          <div class="auth-security">
            <p style="margin:0;font-weight:600;">보안 안내</p>
            <ul>
              <li>문제·정답은 외부 유출 금지</li>
              <li>개인 계정 공유 금지</li>
              <li>공용 PC 사용 후 반드시 로그아웃</li>
            </ul>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def auth_form_header(title: str, subtitle: str | None = None):
    sub = f'<p class="auth-sub">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f"""
        <p class="auth-brand-link">지역경찰 역량 강화를 위한 실무역량 다통과</p>
        <h2 class="auth-title">{title}</h2>
        {sub}
        """,
        unsafe_allow_html=True,
    )


def email_input(label: str = "경찰웹메일 ID", key: str = "email_local", value: str = "trustkimjs") -> str:
    st.markdown(
        f'<p style="margin:0 0 0.3rem;font-size:0.9rem;font-weight:500;color:#132238;">{label}</p>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns([6, 1.35], gap="small")
    with c1:
        local = st.text_input(
            label,
            value=value,
            key=key,
            placeholder="경찰웹메일 ID",
            label_visibility="collapsed",
        )
    with c2:
        st.markdown(
            f'<div class="email-domain-mark">@{ALLOWED_EMAIL_DOMAIN}</div>',
            unsafe_allow_html=True,
        )
    return full_police_email(local or "")


def auth_layout(title: str, subtitle: str | None, body):
    st.html(
        """
        <style>
        [data-testid="stMain"] {
            display: flex !important;
            flex-direction: column !important;
            justify-content: center !important;
        }
        .block-container {
            margin-top: auto !important;
            margin-bottom: auto !important;
        }
        </style>
        """
    )
    st.markdown('<div class="auth-form-col">', unsafe_allow_html=True)
    auth_form_header(title, subtitle)
    body()
    st.markdown("</div>", unsafe_allow_html=True)


# ---------- Auth views ----------

def view_login():
    def body():
        try:
            _login_form = st.form(
                "login_form",
                clear_on_submit=False,
                border=False,
                enter_to_submit=False,
            )
        except TypeError:
            _login_form = st.form(
                "login_form",
                clear_on_submit=False,
                border=False,
            )
        with _login_form:
            email = email_input(key="login_local", value="trustkimjs")
            password = st.text_input(
                "비밀번호",
                type="password",
                key="login_pw",
                placeholder="비밀번호",
            )
            submitted = st.form_submit_button(
                "로그인",
                type="primary",
                use_container_width=True,
            )
            if submitted:
                email_safe = email.strip().lower()
                user, msg, needs_verify = login_user(email_safe, password)
                if user:
                    login_success(user)
                elif needs_verify:
                    st.warning(msg)
                    go("verify", verify_email=email_safe)
                else:
                    st.error(msg)

        st.markdown('<div style="height:0.25rem"></div>', unsafe_allow_html=True)
        r1a, r1b = st.columns([1.55, 1], gap="small")
        with r1a:
            st.markdown(
                '<p class="auth-link-label">계정이 없으신가요?&nbsp;</p>',
                unsafe_allow_html=True,
            )
        with r1b:
            if st.button("회원가입", type="secondary", key="login_to_register"):
                go("register")

        r2a, r2b = st.columns([1.9, 1.2], gap="small")
        with r2a:
            st.markdown(
                '<p class="auth-link-label">비밀번호를 잊어버렸다면?&nbsp;</p>',
                unsafe_allow_html=True,
            )
        with r2b:
            if st.button("비밀번호 재설정", type="secondary", key="login_to_forgot"):
                go("forgot")

    auth_layout(
        "로그인",
        "회원가입을 눌러 경찰 웹메일로 경찰 인증 후 사용하세요.",
        body,
    )


def view_register():
    def body():
        try:
            _reg_form = st.form(
                "register_form",
                clear_on_submit=False,
                border=False,
                enter_to_submit=False,
            )
        except TypeError:
            _reg_form = st.form(
                "register_form",
                clear_on_submit=False,
                border=False,
            )
        with _reg_form:
            name = st.text_input("닉네임", key="reg_name", placeholder="닉네임")
            organization = st.text_input("소속", key="reg_org", placeholder="소속")
            email = email_input(key="reg_local", value="")
            password = st.text_input(
                "비밀번호 (8자 이상)",
                type="password",
                key="reg_pw",
                placeholder="비밀번호",
            )
            submitted = st.form_submit_button(
                "인증번호 받기",
                type="primary",
                use_container_width=True,
            )
            if submitted:
                email_safe = email.strip().lower()
                ok, msg, code = register_user(name, email_safe, password, organization)
                if ok and code:
                    try:
                        from lib.mail import send_otp_email

                        send_otp_email(email_safe, code)
                        st.session_state.dev_otp = None
                        st.success(
                            "인증번호를 이메일로 발송했습니다. 메일함을 확인해 주세요."
                        )
                        go("verify", verify_email=email_safe)
                    except Exception as e:
                        from lib.mail import MAIL_MODULE_VERSION as _mv

                        st.error(
                            f"인증번호 메일 발송에 실패했습니다. "
                            f"[mail {_mv}] {type(e).__name__}: {e!s}"
                        )
                elif ok:
                    st.error("인증번호 발급에 실패했습니다. 다시 시도해 주세요.")
                else:
                    st.error(msg)
        if st.button("로그인으로", type="secondary", key="reg_to_login"):
            go("login")

    auth_layout(
        "회원가입",
        "경찰청 웹메일(@police.go.kr)로 가입 후 인증번호를 받아 주세요.",
        body,
    )


def view_verify():
    def body():
        email = st.text_input(
            "이메일",
            value=st.session_state.verify_email,
            key="verify_email_input",
        )
        code = st.text_input("인증번호 6자리", max_chars=6, key="verify_code", placeholder="6자리")
        if st.button("인증 완료", type="primary", use_container_width=True):
            email_safe = email.strip().lower()
            ok, msg = verify_otp(email_safe, code)
            if ok:
                st.session_state.dev_otp = None
                from lib.db import fetch_one

                user = fetch_one(
                    "SELECT * FROM User WHERE email = ?",
                    (email_safe,),
                )
                if user:
                    login_success(public_user(user))
                else:
                    st.error("사용자를 찾을 수 없습니다.")
            else:
                st.error(msg)
        if st.button("로그인으로", type="secondary"):
            go("login")

    auth_layout("이메일 인증", "메일로 받은 6자리 인증번호를 입력하세요.", body)


def view_forgot():
    def body():
        email = email_input(key="forgot_local", value="")
        if st.button("인증번호 받기", type="primary", use_container_width=True):
            email_safe = email.strip().lower()
            ok, msg, code = forgot_password(email_safe)
            if ok and code:
                try:
                    from lib.mail import MAIL_MODULE_VERSION, send_otp_email

                    send_otp_email(email_safe, code)
                    st.session_state.dev_otp = None
                    st.success("인증번호를 이메일로 발송했습니다. 메일함을 확인해 주세요.")
                    go("reset", reset_email=email_safe)
                except Exception as e:
                    from lib.mail import MAIL_MODULE_VERSION as _mv

                    st.error(
                        f"인증번호 메일 발송에 실패했습니다. "
                        f"[mail {_mv}] {type(e).__name__}: {e!s}"
                    )
            elif ok:
                st.error("인증번호 발급에 실패했습니다. 다시 시도해 주세요.")
            else:
                st.error(msg)
        if st.button("로그인으로", type="secondary"):
            go("login")

    auth_layout(
        "비밀번호 재설정",
        "가입한 경찰 웹메일로 인증번호를 받아 새 비밀번호를 설정하세요.",
        body,
    )


def view_reset():
    def body():
        email = st.text_input(
            "이메일",
            value=st.session_state.reset_email,
            key="reset_email_input",
        )
        code = st.text_input("인증번호 6자리", max_chars=6, key="reset_code", placeholder="6자리")
        pw = st.text_input("새 비밀번호 (8자 이상)", type="password", key="reset_pw")
        pw2 = st.text_input("새 비밀번호 확인", type="password", key="reset_pw2")
        if st.button("비밀번호 변경", type="primary", use_container_width=True):
            if pw != pw2:
                st.error("비밀번호가 일치하지 않습니다.")
            else:
                email_safe = email.strip().lower()
                ok, msg = reset_password(email_safe, code, pw)
                if ok:
                    st.session_state.dev_otp = None
                    st.success(msg)
                    go("login")
                else:
                    st.error(msg)
        if st.button("로그인으로", type="secondary"):
            go("login")

    auth_layout("새 비밀번호 설정", "인증 후 새 비밀번호를 입력하세요.", body)


def view_mail_setup():
    def body():
        st.markdown(
            """
            인증번호는 이메일로만 발송됩니다. 화면에 표시하지 않습니다.

            Streamlit Cloud **Secrets** 또는 환경변수에 메일 설정을 넣어 주세요.

            - `EMAILJS_SERVICE_ID`, `EMAILJS_TEMPLATE_ID`, `EMAILJS_PUBLIC_KEY`, `EMAILJS_PRIVATE_KEY`
            - 또는 `MAIL_USER`, `MAIL_PASS` (필요 시 `MAIL_HOST`, `MAIL_PORT`)
            """
        )
        if st.button("로그인으로", type="secondary"):
            go("login")

    auth_layout("메일 설정 안내", "이메일 발송 설정이 필요할 때 참고하세요.", body)


# ---------- App views ----------

def app_shell_css():
    st.html(
        """
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap">
        <style>
          .stApp, .main, .block-container, [data-testid="stAppViewContainer"] {
              background-color: #f4f7fb !important;
          }
          p, span, div, label, h1, h2, h3, li, .q-stem, .auth-title {
              color: #132238 !important;
          }
          .topics-panel-inner *, .mock-panel-inner * {
              color: #ffffff !important;
          }
          .topics-panel-inner .topics-kicker, .mock-panel-inner .mock-kicker, .topics-panel-inner strong {
              color: #c9a227 !important;
          }
          .topics-panel-inner .topics-meta, .mock-panel-inner .mock-desc {
              color: rgba(255,255,255,0.78) !important;
          }
          button[kind="primary"] {
              background-color: #ffffff !important;
              color: #0b2a4a !important;
              border: 1px solid #d7e0ea !important;
              border-radius: 0.55rem !important;
          }
          button[kind="secondary"] {
              background-color: #ffffff !important;
              color: #132238 !important;
              border: 1px solid #d7e0ea !important;
              border-radius: 0.55rem !important;
          }
          .topics-panel-inner {
              background: linear-gradient(145deg, #071c33 0%, #0b2a4a 52%, #123b63 100%) !important;
              border-radius: 1.15rem !important; padding: 1.25rem !important; margin-bottom: 1rem !important;
          }
          .mock-panel-inner {
              background: linear-gradient(155deg, #0e3358 0%, #1f4e79 52%, #2d6494 100%) !important;
              border-radius: 1.15rem !important; padding: 1.25rem !important; margin-bottom: 1rem !important;
          }
          #realtime-timer, .timer-pill {
              background-color: #fff3cd !important; color: #d90429 !important; border: 2px solid #d90429 !important;
              padding: 0.35rem 0.85rem !important; border-radius: 20px !important; font-weight: 900 !important;
          }
          .q-stem, .q-stem-wrap, .q-stem-box li, div[data-testid='stRadio'] label, .exam-question-anchor p {
              color: #132238 !important;
          }
        </style>
        """
    )
    
    components.html(
        """
        <script>
        (function() {
            let doc = document;
            setInterval(function() {
                var buttons = doc.querySelectorAll('button[kind="primary"]');
                buttons.forEach(function(btn) {
                    if (btn.innerText.includes("모의고사 시작") || btn.innerText.includes("실전 모의고사 풀기")) {
                        btn.style.setProperty('background-color', '#ffffff', 'important');
                        btn.style.setProperty('color', '#0b2a4a', 'important');
                        btn.style.setProperty('border', '1px solid #d7e0ea', 'important');
                    }
                });
            }, 300);
        })();
        </script>
        """, height=0, width=0
    )


def view_dashboard():
    user = require_user()
    app_shell_css()
    count = topic_count()

    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">
          <p class="damoa-brand" style="margin:0;font-weight:700;">지역 경찰 실무역량 다통과</p>
          <span style="background-color:#d7e0ea;color:#132238;padding:0.1rem 0.4rem;border-radius:0.3rem;font-size:0.7rem;font-weight:700;">인증됨</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    greet_l, greet_r = st.columns([8, 2], gap="small")
    with greet_l:
        st.markdown(
            f"""
            <p style="font-size:1.4rem;font-weight:800;margin:0;">안녕하세요, {html.escape(user["name"])}님</p>
            <p style="font-size:0.85rem;color:#5b6b7c;margin:0;">{html.escape(user["email"])}</p>
            """,
            unsafe_allow_html=True,
        )
    with greet_r:
        if st.button("로그아웃", type="secondary", key="dash_logout"):
            logout()
            
    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
    
    active = get_active_attempt(user["id"])
    if active:
        res_l, res_r = st.columns([1, 0.25], gap="small")
        with res_l:
            st.markdown(
                """
                <div style="background: rgba(201,162,39,0.12); border: 1px solid #c9a227; border-radius: 0.8rem; padding: 1rem; margin-bottom: 1rem;">
                  <p style="font-weight:700;margin:0;font-size:0.95rem;">풀고 있던 문제가 있습니다.</p>
                  <p style="font-size:0.85rem;color:#5b6b7c;margin:0.2rem 0 0;">저장해 둔 답안부터 이어서 풀 수 있습니다.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with res_r:
            st.markdown("<div style='margin-top:1.2rem;'></div>", unsafe_allow_html=True)
            if st.button("이어하기", type="primary", key="dash_resume"):
                go("exam", attempt_id=active["id"], q_index=-1, feedback=None)

    topics_panel = st.columns(1)[0]
    with topics_panel:
        st.markdown(
            f"""
            <div class="topics-panel-inner">
              <p class="topics-kicker">실무역량 다통과 학습</p>
              <p class="topics-hero">주제별 모의고사</p>
              <p class="topics-meta"><span>{count}개 주제</span> · 현장 대응 전 범위</p>
              <div class="topics-mode-hints" style="grid-template-columns: 1fr;">
                <p><strong>시험</strong> 주제별 랜덤 출제</p>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("주제별 모의고사 시작", type="primary", use_container_width=True, key="dash_exam"):
            go("topics", topics_mode="end")

    mock_panel = st.columns(1)[0]
    with mock_panel:
        st.markdown(
            """
            <div class="mock-panel-inner">
              <p class="mock-kicker">실전 대비</p>
              <p class="mock-hero">실전 모의고사(40문항)</p>
              <p class="mock-desc">제한 시간 안에 전 범위를 점검하고, 제출 후 해설을 확인하세요.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("실전 모의고사 풀기", type="primary", use_container_width=True, key="dash_mock"):
            aid, err = start_exam(user["id"], kind="mock", reveal_mode="end", force_new=True)
            if err:
                st.error(err)
            else:
                go("exam", attempt_id=aid, q_index=-1, feedback=None)

    recent = list(recent_attempts(user["id"], limit=3))[:3]
    st.markdown('<p style="font-size:1.1rem;font-weight:700;margin-top:2rem;">최근 학습 완료 현황</p>', unsafe_allow_html=True)
    if not recent:
        st.markdown('<p style="font-size:0.9rem;color:#5b6b7c;">학습 기록이 없습니다.</p>', unsafe_allow_html=True)
    else:
        for item in recent:
            title = attempt_title(item)
            score = item["score"] if item["score"] is not None else 0
            submitted = item["submittedAt"] or "-"
            if submitted != "-":
                try:
                    from datetime import datetime
                    from zoneinfo import ZoneInfo
                    dt = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                    submitted = dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d %H:%M")
                except Exception:
                    pass
            r1, r2, r3 = st.columns([4.2, 1.1, 0.9], gap="small")
            with r1:
                st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.7rem;margin-bottom:0.4rem;"><p style="font-weight:700;margin:0;">{html.escape(title)}</p><p style="font-size:0.75rem;color:#5b6b7c;margin:0;">{html.escape(str(submitted))}</p></div>', unsafe_allow_html=True)
            with r2:
                st.markdown(f'<p style="text-align:right;font-weight:700;margin-top:1rem;">{score}/{item["totalCount"]}점</p>', unsafe_allow_html=True)
            with r3:
                st.markdown("<div style='margin-top:0.6rem;'></div>", unsafe_allow_html=True)
                if st.button("결과", key=f"recent_{item['id']}", use_container_width=True):
                    go("result", attempt_id=item["id"])


def view_topics():
    user = require_user()
    app_shell_css()
    mode = "end"

    st.markdown(
        f"""
        <p style="font-size:0.8rem;color:#5b6b7c;margin:0;">실무역량 다통과</p>
        <p style="font-size:1.5rem;font-weight:800;margin:0;">주제별 모의고사</p>
        <p style="font-size:0.85rem;color:#5b6b7c;margin-top:0.45rem;margin-bottom:1.5rem;">
          제한 시간 안에 주제별 랜덤 출제로 진행되며, 다 풀고 난 뒤에 해설을 제공합니다.
        </p>
        """,
        unsafe_allow_html=True,
    )

    if st.button("← 홈으로 돌아가기", key="topics_home", type="secondary"):
        go("dashboard")
    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)

    active = get_active_attempt(user["id"])
    if active:
        res_l, res_r = st.columns([1, 0.25], gap="small")
        with res_l:
            st.markdown(
                """
                <div style="background: rgba(201,162,39,0.12); border: 1px solid #c9a227; border-radius: 0.8rem; padding: 1rem; margin-bottom: 1rem;">
                  <p style="font-weight:700;margin:0;font-size:0.95rem;">진행 중인 시험이 있습니다.</p>
                  <p style="font-size:0.85rem;color:#5b6b7c;margin:0.2rem 0 0;">새 주제 시작 시 이전 진행은 자동 제출됩니다.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with res_r:
            st.markdown("<div style='margin-top:1.2rem;'></div>", unsafe_allow_html=True)
            if st.button("이어하기", type="primary", key="topics_resume"):
                go("exam", attempt_id=active["id"], q_index=-1, feedback=None)

    cats = sort_topics(topic_categories())
    total_all = sum(int(c["questionCount"]) for c in cats)

    all_card = st.columns(1)[0]
    with all_card:
        a_txt, a_btn = st.columns([1, 0.32], gap="small")
        with a_txt:
            st.markdown(
                f"""
                <div class="card-banner-inner card-banner-navy">
                  <p class="section-label">전체 풀기</p>
                  <p class="section-title">14개 주제 전 문항</p>
                  <p class="section-desc">{total_all}문항 · 랜덤 출제 · 제한시간 {total_all}분</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with a_btn:
            st.markdown("<div style='margin-top:1.2rem;'></div>", unsafe_allow_html=True)
            if st.button("전체 시험 보기", type="primary", use_container_width=True, key="topics_all"):
                aid, err = start_exam(user["id"], kind="all", reveal_mode=mode, force_new=True)
                if err: st.error(err)
                else: go("exam", attempt_id=aid, q_index=-1, feedback=None)

    for i in range(0, len(cats), 2):
        cols = st.columns(2, gap="small")
        for col, cat in zip(cols, cats[i : i + 2]):
            n = int(cat["questionCount"])
            with col:
                t_txt, t_btn = st.columns([1, 0.38], gap="small")
                with t_txt:
                    st.markdown(
                        f"""
                        <div class="card-banner-inner">
                          <p class="section-title">{html.escape(cat["name"] or "")}</p>
                          <p class="section-desc">{n}문항 · 랜덤 출제 · 제한시간 {n}분</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with t_btn:
                    st.markdown("<div style='margin-top:1.2rem;'></div>", unsafe_allow_html=True)
                    if st.button("시험 보기", key=f"cat_{cat['id']}", type="primary", use_container_width=True):
                        aid, err = start_exam(user["id"], kind="topic", category_id=cat["id"], reveal_mode=mode, force_new=True)
                        if err: st.error(err)
                        else: go("exam", attempt_id=aid, q_index=-1, feedback=None)


def view_exam():
    from datetime import datetime, timezone
    user = require_user()
    app_shell_css()
    attempt_id = st.session_state.attempt_id
    attempt, questions = load_exam(attempt_id, user["id"])
    if not attempt:
        st.error("시험을 찾을 수 없습니다.")
        if st.button("홈으로"): go("dashboard")
        return

    if attempt["status"] == "submitted": go("result", attempt_id=attempt_id)
    if not attempt["revealMode"] == "immediate" and is_time_expired(attempt):
        submit_exam(attempt_id, user["id"])
        st.warning("제한 시간이 종료되어 자동 제출되었습니다.")
        go("result", attempt_id=attempt_id)

    if st.session_state.q_index == -1:
        st.session_state.q_index = 0
        for i, q in enumerate(questions):
            if q["userAnswer"] is None:
                st.session_state.q_index = i
                break

    idx = max(0, min(st.session_state.q_index, len(questions) - 1))
    q = questions[idx]
    answered = sum(1 for x in questions if x["userAnswer"] is not None)
    ends = attempt_ends_at(attempt)
    remain_sec = max(0, int((ends - datetime.now(timezone.utc)).total_seconds()))
    mm, ss = divmod(remain_sec, 60)
    
    mode_label = "모의고사" if attempt["kind"] == "mock" else "시험 모드"
    cat_line = f'<p style="font-size:0.8rem;color:#5b6b7c;margin:0.2rem 0 0;">{q["categoryName"]}</p>' if attempt["kind"] != "mock" else ""
    
    st.markdown(
        f"""
        <div class="exam-page-top" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
          <div>
            <p style="font-size:0.8rem;color:#5b6b7c;margin:0;font-weight:600;">
              실무역량 다통과 <span style="color:#132238;">· {mode_label}</span>
            </p>
            <p style="margin:0.4rem 0 0;color:#132238;font-weight:800;font-size:1.1rem;">진행 {answered}/{attempt["totalCount"]}</p>
            {cat_line}
          </div>
          <div id="realtime-timer" class="timer-pill">남은 시간 {mm:02d}:{ss:02d}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(f'<p style="margin:0.6rem 0 1rem;color:#132238;font-size:1.35rem;font-weight:800;">문제 {idx + 1}</p>{stem_html(q["stem"] or "")}', unsafe_allow_html=True)
    img = image_path_for(q["imagePath"])
    if img: st.image(str(img), use_container_width=True)

    choices = parse_choices(q["choicesJson"])
    is_last = idx >= len(questions) - 1
    selected = st.radio("보기", options=list(range(len(choices))), format_func=lambda i: f"{i+1}. {choices[i]}", index=int(q["userAnswer"]) if q["userAnswer"] is not None else None, key=f"radio_{q['id']}_{idx}", label_visibility="collapsed")

    if selected is not None and selected != (int(q["userAnswer"]) if q["userAnswer"] is not None else -1):
        ok, msg, feedback = save_answer(attempt_id, user["id"], q["id"], selected)
        if ok:
            st.session_state.feedback = feedback
            if not is_last:
                st.session_state.q_index = idx + 1
                st.session_state.feedback = None
            request_scroll_top()
            st.rerun()
        else: st.error(msg)

    st.markdown("<div style='height:2rem;'></div>", unsafe_allow_html=True)
    nav_l, nav_m, nav_r = st.columns(3, gap="small")
    with nav_l:
        if st.button("이전", disabled=idx <= 0, use_container_width=True, type="secondary"):
            st.session_state.q_index = idx - 1
            st.session_state.feedback = None
            request_scroll_top()
            st.rerun()
    with nav_m:
        next_label = "제출하기" if is_last else "다음"
        if st.button(next_label, type="primary", use_container_width=True):
            if is_last:
                submit_exam(attempt_id, user["id"])
                go("result", attempt_id=attempt_id)
            else:
                st.session_state.q_index = idx + 1
                request_scroll_top()
                st.rerun()
    with nav_r:
        if st.button("홈으로", use_container_width=True, type="secondary"): go("dashboard")

    if not is_learn_mode:
        components.html(f"""<script>(function(){{let d=document,w=window; try{{if(w.parent&&w.parent.document){{d=w.parent.document;w=w.parent;}}}}catch(e){{}} if(w.examTimerInterval)clearInterval(w.examTimerInterval); const endsAt={ends.timestamp()}*1000; w.examTimerInterval=setInterval(function(){{ const el=d.getElementById('realtime-timer'); if(!el)return; let remain=Math.floor((endsAt-Date.now())/1000); if(remain<0)remain=0; let m=String(Math.floor(remain/60)).padStart(2,'0'); let s=String(remain%60).padStart(2,'0'); el.innerText="남은 시간 "+m+":"+s; if(remain<=0){{el.style.backgroundColor="#e63946";el.style.color="white";clearInterval(w.examTimerInterval);}} }},1000); }})();</script>""", height=0, width=0)


def view_result():
    user = require_user()
    app_shell_css()
    attempt_id = st.session_state.attempt_id
    attempt, questions = load_exam(attempt_id, user["id"])
    if not attempt:
        st.error("결과를 찾을 수 없습니다."); st.button("홈으로", on_click=lambda: go("dashboard")); return
    if attempt["status"] != "submitted": go("exam", attempt_id=attempt_id)

    st.markdown('<p style="font-size:0.8rem;color:#5b6b7c;margin:0;">실무역량 다통과</p><p style="font-size:1.5rem;font-weight:800;margin:0 0 1rem;">채점 결과</p>', unsafe_allow_html=True)
    
    score = attempt["score"] or 0; total = attempt["totalCount"]; pct = round(score/total*100) if total else 0
    m1, m2, m3 = st.columns(3, gap="small")
    with m1: st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.8rem;text-align:center;"><p style="font-size:0.8rem;color:#5b6b7c;margin:0;">점수</p><p style="font-size:1.2rem;font-weight:800;color:#132238;margin:0;">{score}/{total}</p></div>', unsafe_allow_html=True)
    with m2: st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.8rem;text-align:center;"><p style="font-size:0.8rem;color:#5b6b7c;margin:0;">정답률</p><p style="font-size:1.2rem;font-weight:800;color:#132238;margin:0;">{pct}%</p></div>', unsafe_allow_html=True)
    with m3: st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.8rem;text-align:center;"><p style="font-size:0.8rem;color:#5b6b7c;margin:0;">틀린 문제</p><p style="font-size:1.2rem;font-weight:800;color:#e63946;margin:0;">{len([q for q in questions if not q["isCorrect"]])}</p></div>', unsafe_allow_html=True)
    
    if st.button("홈으로", use_container_width=True, type="secondary"): go("dashboard")
    
    for q in questions:
        choices = parse_choices(q["choicesJson"])
        st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.8rem;padding:1.2rem;margin-top:1rem;"><p style="font-weight:800;color:#132238;">문제 {q["orderIndex"]}</p>{stem_html(q["stem"] or "")}', unsafe_allow_html=True)
        for i, text in enumerate(choices):
            is_answer = i == int(q["answerIndex"]); is_selected = q["userAnswer"] is not None and i == int(q["userAnswer"])
            bg, border, color = ("rgba(46, 204, 113, 0.1)" if is_answer else ("rgba(230, 57, 70, 0.1)" if is_selected else "#f4f7fb")), ("2px solid #2ecc71" if is_answer else ("2px solid #e63946" if is_selected else "1px solid #d7e0ea")), ("#2ecc71" if is_answer else ("#e63946" if is_selected else "#132238"))
            st.markdown(f'<div style="background:{bg}; border:{border}; color:{color}; padding:0.8rem; border-radius:0.5rem; margin-bottom:0.5rem; font-weight:600;">{i+1}. {html.escape(text)}</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

def main():
    init_state()
    app_shell_css()
    restore_user_from_url()
    view = st.session_state.view
    if st.session_state.user and view in {"login", "register"}:
        view = "dashboard"
        st.session_state.view = view
    routes = {"login": view_login, "register": view_register, "verify": view_verify, "forgot": view_forgot, "reset": view_reset, "mail_setup": view_mail_setup, "dashboard": view_dashboard, "topics": view_topics, "exam": view_exam, "result": view_result}
    routes.get(view, view_login)()
    flush_scroll_top()

if __name__ == "__main__":
    main()
