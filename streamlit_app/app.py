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

ALLOWED_EMAIL_DOMAIN = _auth.ALLOWED_EMAIL_DOMAIN
AUTH_COOKIE_DAYS = getattr(_auth, "AUTH_COOKIE_DAYS", 30)
AUTH_COOKIE_NAME = getattr(_auth, "AUTH_COOKIE_NAME", "damoa_auth")
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
    page_title="지역 경찰 실무 역량 평가 DaMoa",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_CSS = (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")
# markdown sanitizer can leak CSS as text; st.html injects safely
st.html(f"<style>{_CSS}</style>")


def init_state():
    defaults = {
        "view": "login",
        "user": None,
        "dev_otp": None,
        "verify_email": "",
        "reset_email": "",
        "topics_mode": "immediate",
        "attempt_id": None,
        "q_index": 0,
        "feedback": None,
        "result_wrong_only": False,
        "result_show_topic_mix": False,
        "_auth_cookie_sync": None,
        "_force_logout": False,
        "_scroll_top": False,
        "_scroll_to": None,
        "_scroll_nonce": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def set_auth_cookie(token: str | None):
    """브라우저 쿠키에 로그인 토큰을 남겨 새로고침 후에도 세션을 복구한다."""
    st.session_state._auth_cookie_sync = token if token else ""


def flush_auth_cookie():
    token = st.session_state.get("_auth_cookie_sync")
    if token is None:
        return
    max_age = AUTH_COOKIE_DAYS * 24 * 60 * 60 if token else 0
    # iframe·부모 문서 모두에 써서 로그아웃 시 확실히 지운다.
    components.html(
        f"""
        <script>
        (function () {{
          const conf = "{AUTH_COOKIE_NAME}={token}; path=/; max-age={max_age}; SameSite=Lax";
          try {{ document.cookie = conf; }} catch (e) {{}}
          try {{
            if (window.parent && window.parent.document) {{
              window.parent.document.cookie = conf;
            }}
          }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )
    st.session_state._auth_cookie_sync = None


def restore_user_from_cookie():
    token = None
    try:
        token = st.context.cookies.get(AUTH_COOKIE_NAME)
    except Exception:
        token = None

    # 로그아웃 직후: 이번 요청에 남은 옛 쿠키로 다시 로그인되지 않게 막는다.
    # 브라우저가 쿠키를 지운 다음 요청부터 플래그를 해제한다.
    if st.session_state.get("_force_logout"):
        st.session_state.user = None
        if not user_id_from_auth_token(token):
            st.session_state._force_logout = False
        return

    if st.session_state.get("user"):
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
    set_auth_cookie(make_auth_token(user["id"]))
    go(view, **kwargs)


def logout():
    st.session_state.user = None
    st.session_state._force_logout = True
    set_auth_cookie(None)
    go("login")


def reset_result_filters():
    """결과 화면의 토글/패널 상태를 초기화한다."""
    st.session_state.result_wrong_only = False
    st.session_state.result_show_topic_mix = False
    # st.toggle(key=...) 위젯 상태도 함께 리셋
    st.session_state.result_wrong_toggle = False


def go(view: str, **kwargs):
    prev_view = st.session_state.get("view")
    prev_attempt = st.session_state.get("attempt_id")
    st.session_state.view = view
    for k, v in kwargs.items():
        st.session_state[k] = v
    # 결과 화면을 벗어나거나 다른 결과로 이동하면 필터 초기화
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
    """화면 전환/답안 채점 후 스크롤 위치를 맞춘다."""
    target = st.session_state.pop("_scroll_to", None)
    to_top = st.session_state.pop("_scroll_top", False)
    if not target and not to_top:
        return
    nonce = int(st.session_state.get("_scroll_nonce", 0))
    # components는 iframe이라 parent 문서를 스크롤해야 한다.
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
            <!-- scroll:{nonce} -->
            <script>
            (function () {{
              const doc = window.parent.document;
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
            """,
            height=0,
            width=0,
        )
        return

    components.html(
        f"""
        <!-- scroll-top:{nonce} -->
        <script>
        (function () {{
          const doc = window.parent.document;
          const win = window.parent;
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
              try {{
                anchor.scrollIntoView({{ behavior: 'auto', block: 'start' }});
              }} catch (e) {{}}
            }}
            win.scrollTo(0, 0);
          }}
          // Streamlit이 위젯 포커스로 스크롤을 되돌리는 경우를 잠시 덮어쓴다.
          const until = Date.now() + 900;
          function lockTop() {{
            toTop();
            if (Date.now() < until) {{
              requestAnimationFrame(lockTop);
            }}
          }}
          lockTop();
          [50, 120, 250, 450, 700].forEach(function (t) {{ setTimeout(toTop, t); }});
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def stem_html(stem: str) -> str:
    """질문 + (있으면) ㄱ/㉠ 지문 박스를 HTML로 렌더한다."""
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


def card_start():
    st.markdown('<div class="damoa-card">', unsafe_allow_html=True)


def card_end():
    st.markdown("</div>", unsafe_allow_html=True)


def brand_line(extra: str = ""):
    st.markdown(
        f'<div class="damoa-brand">지역 경찰 실무 역량 평가 DaMoa {extra}</div>',
        unsafe_allow_html=True,
    )


def auth_left_panel():
    st.markdown(
        """
        <div class="auth-left">
          <div>
            <p class="auth-eyebrow">지역경찰 역량 강화를 위한 실무 역량 평가 DaMoa</p>
            <h1 class="auth-hero">
              <span style="white-space:nowrap">지역경찰 역량 강화를 위한</span><br/>
              실무 역량 평가<br/>
              DaMoa
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
        <p class="auth-brand-link">지역경찰 역량 강화를 위한 실무 역량 평가 DaMoa</p>
        <h2 class="auth-title">{title}</h2>
        {sub}
        """,
        unsafe_allow_html=True,
    )


def email_input(label: str = "아이디", key: str = "email_local") -> str:
    st.markdown(
        f'<p style="margin:0 0 0.3rem;font-size:0.9rem;font-weight:500;color:#132238;">{label}</p>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns([6, 1.35], gap="small")
    with c1:
        local = st.text_input(
            label,
            key=key,
            placeholder="아이디",
            label_visibility="collapsed",
        )
    with c2:
        st.markdown(
            f'<div class="email-domain-mark">@{ALLOWED_EMAIL_DOMAIN}</div>',
            unsafe_allow_html=True,
        )
    return full_police_email(local or "")

def auth_layout(title: str, subtitle: str | None, body):
    left, right = st.columns([1.05, 0.95], gap="small")
    with left:
        st.markdown('<div class="auth-panel-col">', unsafe_allow_html=True)
        auth_left_panel()
        st.markdown("</div>", unsafe_allow_html=True)
    with right:
        st.markdown('<div class="auth-form-col">', unsafe_allow_html=True)
        auth_form_header(title, subtitle)
        body()
        st.markdown("</div>", unsafe_allow_html=True)


# ---------- Auth views ----------

def view_login():
    def body():
        with st.form("login_form", clear_on_submit=False, border=False):
            email = email_input(key="login_local")
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
                user, msg, needs_verify = login_user(email, password)
                if user:
                    login_success(user)
                elif needs_verify:
                    st.warning(msg)
                    go("verify", verify_email=email)
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
        name = st.text_input("닉네임", key="reg_name", placeholder="닉네임")
        organization = st.text_input("소속 (선택)", key="reg_org", placeholder="소속")
        email = email_input(key="reg_local")
        password = st.text_input(
            "비밀번호 (8자 이상)",
            type="password",
            key="reg_pw",
            placeholder="비밀번호",
        )
        if st.button("인증번호 받기", type="primary", use_container_width=True):
            ok, msg, code = register_user(name, email, password, organization)
            if ok:
                st.session_state.dev_otp = code
                st.success(msg)
                go("verify", verify_email=email)
            else:
                st.error(msg)
        if st.button("로그인으로", type="secondary"):
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
        if st.session_state.dev_otp:
            st.info(f"개발용 인증번호: **{st.session_state.dev_otp}**")
        code = st.text_input("인증번호 6자리", max_chars=6, key="verify_code", placeholder="6자리")
        if st.button("인증 완료", type="primary", use_container_width=True):
            ok, msg = verify_otp(email, code)
            if ok:
                st.session_state.dev_otp = None
                from lib.db import fetch_one

                user = fetch_one(
                    "SELECT * FROM User WHERE email = ?",
                    (email.strip().lower(),),
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
        email = email_input(key="forgot_local")
        if st.button("인증번호 받기", type="primary", use_container_width=True):
            ok, msg, code = forgot_password(email)
            if ok:
                st.session_state.dev_otp = code
                st.success(msg)
                go("reset", reset_email=email)
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
        if st.session_state.dev_otp:
            st.info(f"개발용 인증번호: **{st.session_state.dev_otp}**")
        code = st.text_input("인증번호 6자리", max_chars=6, key="reset_code", placeholder="6자리")
        pw = st.text_input("새 비밀번호 (8자 이상)", type="password", key="reset_pw")
        pw2 = st.text_input("새 비밀번호 확인", type="password", key="reset_pw2")
        if st.button("비밀번호 변경", type="primary", use_container_width=True):
            if pw != pw2:
                st.error("비밀번호가 일치하지 않습니다.")
            else:
                ok, msg = reset_password(email, code, pw)
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
            Streamlit 버전에서는 인증번호를 화면에 바로 표시합니다.  
            Next.js 버전처럼 실제 메일을 쓰려면 EmailJS/SMTP 환경변수를 설정하세요.

            - `EMAILJS_SERVICE_ID`, `EMAILJS_TEMPLATE_ID`, `EMAILJS_PUBLIC_KEY`, `EMAILJS_PRIVATE_KEY`
            - 또는 `MAIL_USER`, `MAIL_PASS`
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
          [data-testid='stMain'] {
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            width: 100% !important;
            scrollbar-width: none !important;
            -ms-overflow-style: none !important;
          }
          [data-testid='stMain']::-webkit-scrollbar {
            width: 0 !important;
            height: 0 !important;
            display: none !important;
          }
          .block-container {
            max-width: 960px !important;
            width: min(960px, calc(100vw - 1.5rem)) !important;
            background: rgba(255,255,255,0.97) !important;
            border-radius: 1.5rem !important;
            border: 1px solid rgba(255,255,255,0.15) !important;
            box-shadow: 0 30px 80px rgba(0,0,0,0.35) !important;
            padding: 2rem 2rem 2.4rem !important;
            margin-top: 1.2rem !important;
            margin-bottom: 1.2rem !important;
            margin-left: auto !important;
            margin-right: auto !important;
            box-sizing: border-box !important;
          }
          @media (max-width: 900px) {
            .block-container {
              width: calc(100vw - 1.2rem) !important;
              max-width: calc(100vw - 1.2rem) !important;
              padding: 0.9rem !important;
              margin-left: auto !important;
              margin-right: auto !important;
            }
          }
          .stButton > button[kind='secondary'],
          .stButton > button[data-testid='baseButton-secondary'] {
            background: #fff !important;
            color: #0b2a4a !important;
            border: 1px solid #0b2a4a !important;
            box-shadow: none !important;
            width: 100% !important;
            justify-content: center !important;
            padding: 0.75rem 1rem !important;
            text-decoration: none !important;
          }
          .stButton > button[kind='secondary']:hover {
            background: #f4f7fb !important;
            text-decoration: none !important;
          }
          /* 인사말 옆 로그아웃은 작게, 칸 전체 사용 안 함 */
          div[data-testid='stHorizontalBlock']:has(.greet-title) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock']:has(.greet-title) .stButton > button[data-testid='baseButton-secondary'] {
            width: auto !important;
            min-width: 0 !important;
            border: 1px solid #d7e0ea !important;
            border-radius: 0.65rem !important;
            font-size: 0.8rem !important;
            font-weight: 600 !important;
            padding: 0.45rem 0.7rem !important;
            margin-top: 0.45rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.greet-title) > div:last-child {
            flex: 0 0 auto !important;
            width: auto !important;
            max-width: none !important;
          }
          /* 이어하기 / 인사말: 모바일에서도 가로 한 줄 유지 */
          div[data-testid='stHorizontalBlock']:has(.resume-inline),
          div[data-testid='stHorizontalBlock']:has(.greet-title) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) {
            gap: 0.75rem !important;
            margin: 0.5rem 0 0.65rem !important;
            padding: 0.9rem 1rem !important;
            border: 1px solid #c9a227 !important;
            background: rgba(201,162,39,0.12) !important;
            border-radius: 1rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) > div:first-child {
            display: flex !important;
            align-items: center !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) [data-testid='stVerticalBlock'] {
            gap: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) [data-testid='stElementContainer'],
          div[data-testid='stHorizontalBlock']:has(.resume-inline) .element-container,
          div[data-testid='stHorizontalBlock']:has(.resume-inline) [data-testid='stMarkdownContainer'],
          div[data-testid='stHorizontalBlock']:has(.resume-inline) [data-testid='stMarkdownContainer'] > div {
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) [data-testid='stMarkdownContainer'] p.resume-title {
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) [data-testid='stMarkdownContainer'] p.resume-desc {
            margin: 0.4rem 0 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) .stButton {
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) > div:first-child,
          div[data-testid='stHorizontalBlock']:has(.greet-title) > div:first-child {
            flex: 1 1 auto !important;
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) > div:last-child,
          div[data-testid='stHorizontalBlock']:has(.greet-title) > div:last-child {
            flex: 0 0 auto !important;
            width: auto !important;
            max-width: none !important;
            display: flex !important;
            align-items: center !important;
            justify-content: flex-end !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) .stButton,
          div[data-testid='stHorizontalBlock']:has(.resume-inline) .stButton > button {
            width: auto !important;
            min-width: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) .stButton > button {
            font-size: 0.8rem !important;
            font-weight: 600 !important;
            padding: 0.5rem 0.85rem !important;
            border-radius: 0.65rem !important;
            white-space: nowrap !important;
          }
          div[data-testid='stHorizontalBlock']:has(.resume-inline) .resume-inline {
            margin: 0 !important;
            padding: 0 !important;
            border: none !important;
            background: transparent !important;
          }
          .resume-inline {
            display: flex !important;
            flex-direction: column !important;
            justify-content: center !important;
          }
          .resume-title {
            margin: 0 !important;
            font-weight: 700 !important;
            color: #0b2a4a !important;
            font-size: 0.95rem !important;
            line-height: 1.35 !important;
          }
          .resume-desc {
            margin: 0.4rem 0 0 !important;
            color: #5b6b7c !important;
            font-size: 0.85rem !important;
            line-height: 1.4 !important;
          }
          .greet-title, .damoa-title, .user-email {
            writing-mode: horizontal-tb !important;
          }
          /* 주제별 실무 역량 임팩트 패널 */
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) {
            border: 1px solid rgba(201, 162, 39, 0.38) !important;
            background:
              radial-gradient(circle at top right, rgba(201, 162, 39, 0.22), transparent 42%),
              linear-gradient(145deg, #071c33 0%, #0b2a4a 52%, #123b63 100%) !important;
            border-radius: 1.15rem !important;
            padding: 1.25rem 1.2rem 1.05rem !important;
            margin: 0.75rem 0 1rem !important;
            box-sizing: border-box !important;
            box-shadow: 0 16px 40px rgba(7, 28, 51, 0.22) !important;
          }
          .topics-panel-inner { margin: 0 0 0.75rem !important; }
          .topics-kicker {
            margin: 0 !important;
            color: #c9a227 !important;
            font-size: 0.82rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.04em !important;
          }
          .topics-hero {
            margin: 0.45rem 0 0 !important;
            color: #fff !important;
            font-size: clamp(1.2rem, 2.8vw, 1.65rem) !important;
            font-weight: 800 !important;
            line-height: 1.3 !important;
            letter-spacing: -0.02em !important;
            white-space: nowrap !important;
          }
          .topics-meta {
            margin: 0.55rem 0 0 !important;
            color: rgba(255,255,255,0.78) !important;
            font-size: 0.92rem !important;
          }
          .topics-meta span { color: #fff !important; font-weight: 700 !important; }
          .topics-mode-hints {
            display: grid !important;
            grid-template-columns: 1fr 1fr !important;
            gap: 0.45rem !important;
            margin-top: 0.85rem !important;
          }
          .topics-mode-hints p {
            margin: 0 !important;
            padding: 0.45rem 0.55rem !important;
            border-radius: 0.55rem !important;
            background: rgba(255,255,255,0.08) !important;
            color: rgba(255,255,255,0.88) !important;
            font-size: 0.8rem !important;
            line-height: 1.35 !important;
          }
          .topics-mode-hints strong {
            display: block !important;
            margin-bottom: 0.1rem !important;
            color: #c9a227 !important;
            font-size: 0.78rem !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.5rem !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) > div {
            flex: 1 1 0 !important;
            width: 50% !important;
            min-width: 0 !important;
            max-width: 50% !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .element-container:has(.mode-btns-mark),
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) [data-testid='stElementContainer']:has(.mode-btns-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton {
            width: 100% !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[data-testid='baseButton-primary'],
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[kind='primary'] *,
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[data-testid='baseButton-primary'] * {
            font-size: 0.95rem !important;
            font-weight: 800 !important;
            font-family: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
            letter-spacing: -0.01em !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[data-testid='baseButton-primary'] {
            padding: 0.65rem 0.7rem !important;
            border-radius: 0.7rem !important;
            height: 2.75rem !important;
            min-height: 2.75rem !important;
            width: 100% !important;
            background: #c9a227 !important;
            color: #071c33 !important;
            border: none !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[data-testid='baseButton-secondary'],
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[kind='secondary'] *,
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[data-testid='baseButton-secondary'] * {
            font-size: 0.95rem !important;
            font-weight: 800 !important;
            font-family: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
            letter-spacing: -0.01em !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock'] > div:has(.topics-panel-inner) div[data-testid='stHorizontalBlock']:has(.mode-btns-mark) .stButton > button[data-testid='baseButton-secondary'] {
            padding: 0.65rem 0.7rem !important;
            border-radius: 0.7rem !important;
            height: 2.75rem !important;
            min-height: 2.75rem !important;
            width: 100% !important;
            background: rgba(255,255,255,0.96) !important;
            color: #0b2a4a !important;
            border: 1px solid rgba(255,255,255,0.7) !important;
          }
          /* 실전 모의고사: 주제별보다 밝은 스틸 네이비 + 동일 골드 포인트 */
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) {
            border: 1px solid rgba(201, 162, 39, 0.32) !important;
            background:
              radial-gradient(circle at 90% 10%, rgba(201, 162, 39, 0.16), transparent 48%),
              linear-gradient(155deg, #0e3358 0%, #1f4e79 52%, #2d6494 100%) !important;
            border-radius: 1.15rem !important;
            padding: 1.25rem 1.2rem 1.05rem !important;
            margin: 0 0 1rem !important;
            box-sizing: border-box !important;
            box-shadow: 0 16px 40px rgba(7, 28, 51, 0.18) !important;
          }
          .mock-panel-inner { margin: 0 0 0.75rem !important; }
          .mock-kicker {
            margin: 0 !important;
            color: #c9a227 !important;
            font-size: 0.82rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.04em !important;
          }
          .mock-hero {
            margin: 0.45rem 0 0 !important;
            color: #fff !important;
            font-size: clamp(1.2rem, 2.8vw, 1.65rem) !important;
            font-weight: 800 !important;
            line-height: 1.3 !important;
            letter-spacing: -0.02em !important;
            white-space: nowrap !important;
          }
          .mock-meta {
            margin: 0.55rem 0 0 !important;
            color: rgba(255,255,255,0.78) !important;
            font-size: 0.92rem !important;
          }
          .mock-meta span { color: #fff !important; font-weight: 700 !important; }
          .mock-desc {
            margin: 0.45rem 0 0 !important;
            color: rgba(255,255,255,0.72) !important;
            font-size: 0.86rem !important;
            line-height: 1.5 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .element-container:has(.mock-btn-mark),
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) [data-testid='stElementContainer']:has(.mock-btn-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton {
            width: 100% !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button,
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button[data-testid='baseButton-primary'],
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button *,
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button[kind='primary'] *,
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button[data-testid='baseButton-primary'] * {
            font-size: 0.95rem !important;
            font-weight: 800 !important;
            font-family: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
            letter-spacing: -0.01em !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button,
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock'] > div:has(.mock-panel-inner) .stButton > button[data-testid='baseButton-primary'] {
            padding: 0.65rem 0.7rem !important;
            border-radius: 0.7rem !important;
            height: 2.75rem !important;
            min-height: 2.75rem !important;
            width: 100% !important;
            background: #ffffff !important;
            color: #0b2a4a !important;
            border: 1px solid rgba(255,255,255,0.85) !important;
          }
          /* 주제 목록: 배너 안 문구+버튼 한 줄, 상하 여백 균일 */
          .card-banner-inner {
            margin: 0 !important;
            padding: 0 !important;
            border: none !important;
            background: transparent !important;
            display: flex !important;
            flex-direction: column !important;
            justify-content: center !important;
          }
          .card-banner-inner .section-title {
            margin: 0 !important;
            font-size: 1.05rem !important;
            line-height: 1.35 !important;
          }
          .card-banner-inner .section-desc {
            margin: 0.2rem 0 0 !important;
            font-size: 0.8rem !important;
            line-height: 1.4 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-inner):not(:has(.topics-panel-inner)) {
            border: 1px solid #d7e0ea !important;
            background: #f4f7fb !important;
            border-radius: 1rem !important;
            padding: 0.85rem 0.9rem !important;
            margin: 0.35rem 0 !important;
            box-sizing: border-box !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-inner):not(:has(.topics-panel-inner)) [data-testid='stVerticalBlock'] {
            gap: 0 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-inner):not(:has(.topics-panel-inner)) [data-testid='stElementContainer'],
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-inner):not(:has(.topics-panel-inner)) .element-container,
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-inner):not(:has(.topics-panel-inner)) [data-testid='stMarkdownContainer'],
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-inner):not(:has(.topics-panel-inner)) [data-testid='stMarkdownContainer'] p {
            margin: 0 !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-navy) {
            border: 1px solid rgba(11,42,74,0.2) !important;
            background: rgba(11,42,74,0.05) !important;
          }
          div[data-testid='stHorizontalBlock'] > div:has(.card-banner-gold) {
            border: 1px solid #c9a227 !important;
            background: rgba(201,162,39,0.12) !important;
          }
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 0.5rem !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) > div:first-child {
            flex: 1 1 auto !important;
            min-width: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) > div:last-child {
            flex: 0 0 auto !important;
            width: auto !important;
            min-width: 4.8rem !important;
            max-width: 7.5rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) .element-container:has(.card-banner-btn-mark),
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) [data-testid='stElementContainer']:has(.card-banner-btn-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) .stButton > button,
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock']:has(.card-banner-btn-mark):not(:has(div[data-testid='stHorizontalBlock'])) .stButton > button[data-testid='baseButton-primary'] {
            font-size: 0.72rem !important;
            font-weight: 600 !important;
            padding: 0.4rem 0.45rem !important;
            min-height: 0 !important;
            height: 2.1rem !important;
            border-radius: 0.55rem !important;
            white-space: nowrap !important;
            width: 100% !important;
            box-sizing: border-box !important;
            line-height: 1.15 !important;
          }
          div[data-testid='stRadio'] label {
            background: #f4f7fb;
            border: 1px solid #d7e0ea;
            border-radius: 0.75rem;
            padding: 0.7rem 0.9rem !important;
            margin-bottom: 0.4rem;
          }
          /* 최근 학습 현황: 제목 / 점수 / 결과 한 줄 */
          div[data-testid='stHorizontalBlock']:has(.recent-inline) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 0.45rem !important;
            margin: 0.35rem 0 !important;
            padding: 0.7rem 0.85rem !important;
            border: 1px solid #d7e0ea !important;
            border-radius: 0.85rem !important;
            background: #fff !important;
          }
          div[data-testid='stHorizontalBlock']:has(.recent-inline) > div:first-child {
            flex: 1 1 auto !important;
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
          }
          div[data-testid='stHorizontalBlock']:has(.recent-inline) > div:nth-child(2),
          div[data-testid='stHorizontalBlock']:has(.recent-inline) > div:last-child {
            flex: 0 0 auto !important;
            width: auto !important;
            max-width: none !important;
          }
          div[data-testid='stHorizontalBlock']:has(.recent-inline) .recent-score {
            margin: 0 !important;
            text-align: right !important;
            white-space: nowrap !important;
            font-weight: 700 !important;
            color: #0b2a4a !important;
          }
          div[data-testid='stHorizontalBlock']:has(.recent-inline) .stButton > button,
          div[data-testid='stHorizontalBlock']:has(.recent-inline) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock']:has(.recent-inline) .stButton > button[data-testid='baseButton-secondary'] {
            font-size: 0.78rem !important;
            font-weight: 600 !important;
            padding: 0.4rem 0.7rem !important;
            min-height: 0 !important;
            height: 2rem !important;
            border-radius: 0.55rem !important;
            white-space: nowrap !important;
            width: auto !important;
          }
          /* 틀린문제 토글 + 출제현황 버튼 한 줄 */
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 0.55rem !important;
            margin: 0.55rem 0 0.35rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) > div {
            flex: 1 1 0 !important;
            width: 50% !important;
            min-width: 0 !important;
            max-width: 50% !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) .element-container:has(.result-filter-row),
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) [data-testid='stElementContainer']:has(.result-filter-row) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) .stButton > button,
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock']:has(.result-filter-row) .stButton > button[data-testid='baseButton-secondary'] {
            font-size: 0.82rem !important;
            font-weight: 600 !important;
            padding: 0.45rem 0.55rem !important;
            min-height: 0 !important;
            height: 2.35rem !important;
            border-radius: 0.65rem !important;
            white-space: nowrap !important;
          }
          /* 채점 결과 통계 칸 축소 */
          div[data-testid='stHorizontalBlock']:has(.result-stat) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.4rem !important;
            margin: 0.55rem 0 0.75rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-stat) > div {
            flex: 1 1 0 !important;
            min-width: 0 !important;
          }
          .result-stat {
            padding: 0.55rem 0.5rem !important;
            border-radius: 0.7rem !important;
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            justify-content: center !important;
            gap: 0.35rem !important;
            white-space: nowrap !important;
          }
          .result-stat .num {
            font-size: 0.95rem !important;
            margin: 0 !important;
            line-height: 1.2 !important;
            white-space: nowrap !important;
          }
          .result-stat .lbl {
            font-size: 0.72rem !important;
            margin: 0 !important;
            white-space: nowrap !important;
          }
          /* 결과 하단 액션: 홈/틀린문제/다시응시 한 줄 */
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.4rem !important;
            align-items: stretch !important;
            margin: 0.75rem 0 0.35rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) > div {
            flex: 1 1 0 !important;
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .element-container:has(.result-actions-mark),
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) [data-testid='stElementContainer']:has(.result-actions-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .stButton {
            width: 100% !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .stButton > button,
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .stButton > button[data-testid='baseButton-secondary'],
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock']:has(.result-actions-mark) .stButton > button[data-testid='baseButton-primary'] {
            font-size: 0.72rem !important;
            font-weight: 600 !important;
            padding: 0.45rem 0.35rem !important;
            min-height: 0 !important;
            height: 2.35rem !important;
            border-radius: 0.6rem !important;
            white-space: nowrap !important;
            width: 100% !important;
            line-height: 1.15 !important;
          }
          /* 시험 하단: 다음(위) / 이전·홈으로(아래 한 칸) */
          .element-container:has(.exam-nav-next-mark),
          [data-testid='stElementContainer']:has(.exam-nav-next-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          .element-container:has(.exam-nav-next-mark) + div .stButton > button,
          [data-testid='stElementContainer']:has(.exam-nav-next-mark) + div .stButton > button,
          .element-container:has(.exam-nav-next-mark) + [data-testid='stElementContainer'] .stButton > button,
          [data-testid='stElementContainer']:has(.exam-nav-next-mark) + [data-testid='stElementContainer'] .stButton > button {
            margin-top: 0.75rem !important;
            font-size: 0.9rem !important;
            font-weight: 700 !important;
            height: 2.55rem !important;
            border-radius: 0.7rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.35rem !important;
            align-items: stretch !important;
            margin: 0.35rem 0 0.15rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) > div {
            flex: 1 1 0 !important;
            width: 50% !important;
            min-width: 0 !important;
            max-width: 50% !important;
          }
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) .element-container:has(.exam-nav-side-mark),
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) [data-testid='stElementContainer']:has(.exam-nav-side-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) .stButton {
            width: 100% !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) .stButton > button,
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock']:has(.exam-nav-side-mark) .stButton > button[data-testid='baseButton-secondary'] {
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            padding: 0.35rem 0.5rem !important;
            min-height: 0 !important;
            height: 2rem !important;
            border-radius: 0.55rem !important;
            white-space: nowrap !important;
            width: 100% !important;
            box-sizing: border-box !important;
            line-height: 1.2 !important;
          }
          /* 주제 선택: 학습/시험/홈 한 줄 작은 칩 */
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.35rem !important;
            align-items: stretch !important;
            margin: 0.55rem 0 0.75rem !important;
          }
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) > div {
            flex: 1 1 0 !important;
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
          }
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .element-container:has(.topics-chips-mark),
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) [data-testid='stElementContainer']:has(.topics-chips-mark) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .stButton {
            width: 100% !important;
            margin: 0 !important;
          }
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .stButton > button,
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .stButton > button[kind='secondary'],
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .stButton > button[data-testid='baseButton-secondary'],
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .stButton > button[kind='primary'],
          div[data-testid='stHorizontalBlock']:has(.topics-chips-mark) .stButton > button[data-testid='baseButton-primary'] {
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            padding: 0.4rem 0.35rem !important;
            min-height: 0 !important;
            height: 2.15rem !important;
            border-radius: 0.55rem !important;
            white-space: nowrap !important;
            width: 100% !important;
            box-sizing: border-box !important;
            line-height: 1.2 !important;
          }
        </style>
        """
    )


def sort_topics(cats):
    def key_fn(c):
        import re

        m = re.match(r"^(\d+)", c["name"] or "")
        num = int(m.group(1)) if m else 10**9
        return (num, c["name"])

    return sorted(cats, key=key_fn)


def topic_mix_rows(questions: list) -> list[dict]:
    """모의고사 문항을 주제별로 집계한다."""
    import re
    from collections import Counter, defaultdict

    counts: Counter[str] = Counter()
    correct: dict[str, int] = defaultdict(int)
    for q in questions:
        name = q.get("categoryName") or "기타"
        counts[name] += 1
        if q.get("isCorrect"):
            correct[name] += 1

    def key_fn(name: str):
        m = re.match(r"^(\d+)", name or "")
        num = int(m.group(1)) if m else 10**9
        return (num, name)

    rows = []
    for name in sorted(counts.keys(), key=key_fn):
        rows.append(
            {
                "name": name,
                "count": counts[name],
                "correct": correct[name],
            }
        )
    return rows


def view_dashboard():
    user = require_user()
    app_shell_css()
    count = topic_count()

    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:0.5rem;">
          <p class="damoa-brand" style="margin:0;">지역 경찰 실무 역량 평가 DaMoa</p>
          <span class="damoa-badge">인증됨</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    greet_l, greet_r = st.columns([8, 1], gap="small")
    with greet_l:
        st.markdown(
            f"""
            <p class="damoa-title greet-title">안녕하세요, {html.escape(user["name"])}님</p>
            <p class="damoa-muted user-email">{html.escape(user["email"])}</p>
            """,
            unsafe_allow_html=True,
        )
    with greet_r:
        if st.button("로그아웃", type="secondary", key="dash_logout"):
            logout()
    active = get_active_attempt(user["id"])
    if active:
        res_l, res_r = st.columns([1, 0.2], gap="small")
        with res_l:
            st.markdown(
                """
                <div class="resume-inline">
                  <p class="resume-title">풀고 있던 문제가 있습니다.</p>
                  <p class="resume-desc">저장해 둔 답안부터 이어서 풀 수 있습니다.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with res_r:
            if st.button("이어하기", type="primary", key="dash_resume"):
                go("exam", attempt_id=active["id"], q_index=0, feedback=None)

    topics_panel = st.columns(1)[0]
    with topics_panel:
        st.markdown(
            f"""
            <div class="topics-panel-inner">
              <p class="topics-kicker">실무 역량 학습</p>
              <p class="topics-hero">주제별 실무 역량 문제 풀기</p>
              <p class="topics-meta"><span>{count}개 주제</span> · 현장 대응 전 범위</p>
              <div class="topics-mode-hints">
                <p><strong>학습</strong> 문항마다 바로 해설</p>
                <p><strong>시험</strong> 다 풀고 난 뒤 해설</p>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        m1, m2 = st.columns(2, gap="small")
        with m1:
            st.markdown('<div class="mode-btns-mark"></div>', unsafe_allow_html=True)
            if st.button("학습 모드 시작", type="primary", use_container_width=True, key="dash_learn"):
                go("topics", topics_mode="immediate")
        with m2:
            st.markdown('<div class="mode-btns-mark"></div>', unsafe_allow_html=True)
            if st.button("시험 모드 시작", type="secondary", use_container_width=True, key="dash_exam"):
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
        st.markdown('<div class="mock-btn-mark"></div>', unsafe_allow_html=True)
        if st.button("실전 모의고사 풀기", type="primary", use_container_width=True, key="dash_mock"):
            aid, err = start_exam(user["id"], kind="mock", reveal_mode="end", force_new=True)
            if err:
                st.error(err)
            else:
                go("exam", attempt_id=aid, q_index=0, feedback=None)

    recent = list(recent_attempts(user["id"], limit=3))[:3]
    st.markdown(
        '<p class="recent-heading">최근 학습 현황</p>',
        unsafe_allow_html=True,
    )
    if not recent:
        st.markdown(
            '<p class="recent-empty">학습 기록이 없습니다. 학습·시험·모의고사를 완료하면 여기에 표시됩니다.</p>',
            unsafe_allow_html=True,
        )
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
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                    submitted = dt.astimezone(ZoneInfo("Asia/Seoul")).strftime(
                        "%Y.%m.%d %H:%M"
                    )
                except Exception:
                    pass
            r1, r2, r3 = st.columns([4.2, 1.1, 0.9], gap="small")
            with r1:
                st.markdown(
                    f"""
                    <div class="recent-inline">
                      <p class="recent-title">{html.escape(title)}</p>
                      <p class="recent-meta">{html.escape(str(submitted))}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with r2:
                st.markdown(
                    f'<p class="recent-score">{score}/{item["totalCount"]}점</p>',
                    unsafe_allow_html=True,
                )
            with r3:
                if st.button("결과", key=f"recent_{item['id']}", use_container_width=True):
                    go("result", attempt_id=item["id"])


def view_topics():
    user = require_user()
    app_shell_css()
    mode = st.session_state.topics_mode
    is_learn = mode == "immediate"

    st.markdown(
        f"""
        <p class="damoa-brand">지역 경찰 실무 역량 평가 DaMoa</p>
        <p class="damoa-title">주제별 실무 역량 문제 풀기</p>
        <p class="damoa-muted" style="margin-top:0.45rem;">
          {"학습 모드: 문항마다 해설을 제공합니다." if is_learn else "시험 모드: 문항을 다 풀고 난 뒤에 해설을 제공합니다."}
        </p>
        """,
        unsafe_allow_html=True,
    )

    chip1, chip2, chip3 = st.columns(3, gap="small")
    with chip1:
        st.markdown('<div class="topics-chips-mark"></div>', unsafe_allow_html=True)
        if st.button(
            "학습 모드",
            type="primary" if is_learn else "secondary",
            use_container_width=True,
            key="topics_chip_learn",
        ):
            go("topics", topics_mode="immediate")
    with chip2:
        if st.button(
            "시험 모드",
            type="primary" if not is_learn else "secondary",
            use_container_width=True,
            key="topics_chip_exam",
        ):
            go("topics", topics_mode="end")
    with chip3:
        if st.button("홈으로", type="secondary", use_container_width=True, key="topics_home"):
            go("dashboard")

    active = get_active_attempt(user["id"])
    if active:
        res_l, res_r = st.columns([1, 0.22], gap="small")
        with res_l:
            st.markdown(
                """
                <div class="resume-inline">
                  <p class="resume-title">진행 중인 시험이 있습니다.</p>
                  <p class="resume-desc">아래에서 새 주제를 시작하면 이전 진행은 자동 제출됩니다.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with res_r:
            if st.button("이어하기", type="primary", key="topics_resume"):
                go("exam", attempt_id=active["id"], q_index=0, feedback=None)

    cats = sort_topics(topic_categories())
    total_all = sum(int(c["questionCount"]) for c in cats)
    all_label = "전체 학습하기" if is_learn else "전체 시험 보기"

    all_card = st.columns(1)[0]
    with all_card:
        a_txt, a_btn = st.columns([1, 0.32], gap="small")
        with a_txt:
            st.markdown(
                f"""
                <div class="card-banner-inner card-banner-navy">
                  <p class="section-label">전체 풀기</p>
                  <p class="section-title">14개 주제 전 문항</p>
                  <p class="section-desc">{total_all}문항 · 랜덤 출제 · 제한시간 {total_all}분 (문항당 1분)</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with a_btn:
            st.markdown('<div class="card-banner-btn-mark"></div>', unsafe_allow_html=True)
            if st.button(all_label, type="primary", use_container_width=True, key="topics_all"):
                aid, err = start_exam(user["id"], kind="all", reveal_mode=mode, force_new=True)
                if err:
                    st.error(err)
                else:
                    go("exam", attempt_id=aid, q_index=0, feedback=None)

    # 2-column topic cards (문구 + 버튼 한 줄)
    for i in range(0, len(cats), 2):
        cols = st.columns(2, gap="small")
        for col, cat in zip(cols, cats[i : i + 2]):
            n = int(cat["questionCount"])
            order = "원본 순서" if is_learn else "랜덤 출제"
            btn = "학습하기" if is_learn else "시험 보기"
            with col:
                t_txt, t_btn = st.columns([1, 0.38], gap="small")
                with t_txt:
                    st.markdown(
                        f"""
                        <div class="card-banner-inner">
                          <p class="section-title">{html.escape(cat["name"] or "")}</p>
                          <p class="section-desc">{n}문항 · {order} · 제한시간 {n}분</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with t_btn:
                    st.markdown('<div class="card-banner-btn-mark"></div>', unsafe_allow_html=True)
                    if st.button(btn, key=f"cat_{cat['id']}", type="primary", use_container_width=True):
                        aid, err = start_exam(
                            user["id"],
                            kind="topic",
                            category_id=cat["id"],
                            reveal_mode=mode,
                            force_new=True,
                        )
                        if err:
                            st.error(err)
                        else:
                            go("exam", attempt_id=aid, q_index=0, feedback=None)


def view_exam():
    from datetime import datetime, timezone

    user = require_user()
    app_shell_css()
    attempt_id = st.session_state.attempt_id
    attempt, questions = load_exam(attempt_id, user["id"])
    if not attempt:
        st.error("시험을 찾을 수 없습니다.")
        if st.button("홈으로"):
            go("dashboard")
        return

    if attempt["status"] == "submitted":
        go("result", attempt_id=attempt_id)

    if is_time_expired(attempt):
        submit_exam(attempt_id, user["id"])
        st.warning("제한 시간이 종료되어 자동 제출되었습니다.")
        go("result", attempt_id=attempt_id)

    if st.session_state.q_index == 0 and st.session_state.feedback is None:
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
    is_learn_mode = attempt["revealMode"] == "immediate"
    if attempt["kind"] == "mock":
        mode_label = "모의고사"
        mode_cls = "is-mock"
    elif is_learn_mode:
        mode_label = "학습 모드"
        mode_cls = "is-learn"
    else:
        mode_label = "시험 모드"
        mode_cls = "is-exam"

    cat_line = (
        f'<p class="damoa-muted" style="margin:0.2rem 0 0;">{q["categoryName"]}</p>'
        if attempt["kind"] != "mock"
        else ""
    )
    st.markdown(
        f"""
        <div class="exam-page-top exam-top" id="exam-page-top">
          <div>
            <p class="damoa-brand" style="margin:0;">
              지역 경찰 실무 역량 평가 DaMoa
              <span class="exam-mode-tag {mode_cls}">· {mode_label}</span>
            </p>
            <p style="margin:0.4rem 0 0;color:#0b2a4a;font-weight:700;">진행 {answered}/{attempt["totalCount"]}</p>
            {cat_line}
          </div>
          <div class="timer-pill">남은 시간 {mm:02d}:{ss:02d}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div class="exam-question-anchor" id="exam-question">'
        f'<p style="margin:0.6rem 0 0;color:#0b2a4a;font-size:1.35rem;font-weight:800;">문제 {idx + 1}</p>'
        f'{stem_html(q["stem"] or "")}'
        f"</div>",
        unsafe_allow_html=True,
    )

    img = image_path_for(q["imagePath"])
    if img:
        st.image(str(img), use_container_width=True)

    choices = parse_choices(q["choicesJson"])
    is_learn = attempt["revealMode"] == "immediate"
    is_last = idx >= len(questions) - 1
    locked = is_learn and q["userAnswer"] is not None
    current = int(q["userAnswer"]) if q["userAnswer"] is not None else None

    selected = st.radio(
        "보기",
        options=list(range(len(choices))),
        format_func=lambda i: f"{i+1}. {choices[i]}",
        index=current if current is not None else None,
        disabled=locked,
        key=f"radio_{q['id']}_{idx}",
        label_visibility="collapsed",
    )

    if selected is not None and not locked and selected != current:
        ok, msg, feedback = save_answer(attempt_id, user["id"], q["id"], selected)
        if ok:
            st.session_state.feedback = feedback
            if is_learn:
                request_scroll_to(".exam-feedback-anchor", block="center")
            else:
                # 시험보기/모의고사: 보기 클릭 시 다음 문제 상단으로
                if not is_last:
                    st.session_state.q_index = idx + 1
                    st.session_state.feedback = None
                request_scroll_top()
            st.rerun()
        else:
            st.error(msg)

    feedback = st.session_state.feedback
    if is_learn and (feedback or (locked and q["userAnswer"] is not None)):
        if not feedback and locked:
            feedback = {
                "isCorrect": int(q["userAnswer"]) == int(q["answerIndex"]),
                "correctIndex": int(q["answerIndex"]),
                "explanation": q["explanation"],
                "source": q["source"],
            }
        if feedback:
            if feedback["isCorrect"]:
                st.markdown(
                    '<div id="exam-feedback-result" class="choice-ok exam-feedback-anchor" style="border-left-width:6px;font-weight:700;">정답입니다.</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div id="exam-feedback-result" class="choice-bad exam-feedback-anchor" style="border-left-width:6px;font-weight:700;">오답입니다. 정답은 {feedback["correctIndex"] + 1}번입니다.</div>',
                    unsafe_allow_html=True,
                )
            if feedback.get("explanation"):
                st.markdown(
                    f'<div class="panel"><p class="section-label">해설</p><p class="section-desc" style="margin-top:0.35rem;">{html.escape(feedback["explanation"])}</p></div>',
                    unsafe_allow_html=True,
                )
            if feedback.get("source"):
                st.caption(f"출처: {feedback['source']}")

    # 학습 모드: 다음/학습 종료 / 시험 모드: 마지막 문항만 제출하기 (다음 버튼 없음)
    show_primary = is_learn or is_last
    if show_primary:
        next_label = (
            ("학습 종료" if is_learn else "제출하기")
            if is_last
            else "다음"
        )
        st.markdown('<div class="exam-nav-next-mark"></div>', unsafe_allow_html=True)
        if st.button(next_label, type="primary", use_container_width=True, key="exam_next"):
            if is_last:
                _, qs2 = load_exam(attempt_id, user["id"])
                unanswered = sum(1 for x in qs2 if x["userAnswer"] is None)
                if unanswered and not st.session_state.get("confirm_submit"):
                    st.session_state.confirm_submit = True
                    st.warning(f"미완료 {unanswered}문항이 있습니다. 다시 누르면 제출합니다.")
                else:
                    st.session_state.confirm_submit = False
                    submit_exam(attempt_id, user["id"])
                    go("result", attempt_id=attempt_id)
            else:
                st.session_state.q_index = idx + 1
                st.session_state.feedback = None
                request_scroll_top()
                st.rerun()

    side_l, side_r = st.columns(2, gap="small")
    with side_l:
        st.markdown('<div class="exam-nav-side-mark"></div>', unsafe_allow_html=True)
        if st.button("이전", disabled=idx <= 0, use_container_width=True, type="secondary", key="exam_prev"):
            st.session_state.q_index = idx - 1
            st.session_state.feedback = None
            request_scroll_top()
            st.rerun()
    with side_r:
        if st.button("홈으로", use_container_width=True, type="secondary", key="exam_home"):
            go("dashboard")


def view_result():
    user = require_user()
    app_shell_css()
    attempt_id = st.session_state.attempt_id
    attempt, questions = load_exam(attempt_id, user["id"])
    if not attempt:
        st.error("결과를 찾을 수 없습니다.")
        if st.button("홈으로"):
            go("dashboard")
        return

    if attempt["status"] != "submitted":
        go("exam", attempt_id=attempt_id)

    # 다른 결과로 들어오면 필터/출제 현황 초기화
    if st.session_state.get("_result_filter_attempt") != attempt_id:
        st.session_state._result_filter_attempt = attempt_id
        reset_result_filters()

    score = attempt["score"] or 0
    total = attempt["totalCount"]
    pct = round(score / total * 100) if total else 0
    wrongs = [q for q in questions if not q["isCorrect"]]

    st.markdown(
        '<p class="damoa-brand">지역 경찰 실무 역량 평가 DaMoa</p>',
        unsafe_allow_html=True,
    )
    title_l, title_r = st.columns([5, 1.1], gap="small")
    with title_l:
        st.markdown(
            '<p class="damoa-title greet-title">채점 결과</p>',
            unsafe_allow_html=True,
        )
    with title_r:
        if st.button("홈으로", type="secondary", key="result_home_top"):
            go("dashboard")

    m1, m2, m3 = st.columns(3, gap="small")
    with m1:
        st.markdown(
            f'<div class="result-stat"><span class="lbl">점수</span><span class="num">{score}/{total}</span></div>',
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f'<div class="result-stat"><span class="lbl">정답률</span><span class="num">{pct}%</span></div>',
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f'<div class="result-stat"><span class="lbl">틀린 문제</span><span class="num">{len(wrongs)}</span></div>',
            unsafe_allow_html=True,
        )

    show_filter = attempt["kind"] == "mock" or attempt["revealMode"] == "end"
    is_mock = attempt["kind"] == "mock"
    if show_filter and is_mock:
        filt_l, filt_r = st.columns(2, gap="small")
        with filt_l:
            st.markdown('<div class="result-filter-row"></div>', unsafe_allow_html=True)
            st.session_state.result_wrong_only = st.toggle(
                "틀린 문제만 보기",
                value=st.session_state.result_wrong_only,
                key="result_wrong_toggle",
            )
        with filt_r:
            mix_label = (
                "출제 현황 닫기"
                if st.session_state.result_show_topic_mix
                else "주제별 출제 현황"
            )
            if st.button(
                mix_label,
                use_container_width=True,
                type="secondary",
                key="result_topic_mix",
            ):
                st.session_state.result_show_topic_mix = (
                    not st.session_state.result_show_topic_mix
                )
                st.rerun()
        if st.session_state.result_show_topic_mix:
            rows = topic_mix_rows(questions)
            items_html = "".join(
                (
                    "<li>"
                    f'<span class="mix-name">{html.escape(r["name"])}</span>'
                    f'<span class="mix-count">{r["count"]}문항'
                    f' · 정답 {r["correct"]}</span>'
                    "</li>"
                )
                for r in rows
            )
            st.markdown(
                f"""
                <div class="topic-mix-panel">
                  <p class="section-label">실전 모의고사 출제 현황</p>
                  <p class="section-desc" style="margin-top:0.25rem;">
                    총 {total}문항 · 주제 {len(rows)}개
                  </p>
                  <ul class="topic-mix-list">{items_html}</ul>
                </div>
                """,
                unsafe_allow_html=True,
            )
    elif show_filter:
        st.session_state.result_wrong_only = st.toggle(
            "틀린 문제만 보기",
            value=st.session_state.result_wrong_only,
            key="result_wrong_toggle",
        )

    review = (
        wrongs
        if show_filter and st.session_state.result_wrong_only
        else questions
    )

    for q in review:
        choices = parse_choices(q["choicesJson"])
        cat = (
            f'<p class="section-label">{q["categoryName"]}</p>'
            if attempt["kind"] != "mock"
            else ""
        )
        st.markdown(
            f"""
            <div class="review-card">
              {cat}
              <p class="section-title">문제 {q["orderIndex"]}</p>
              {stem_html(q["stem"] or "")}
            </div>
            """,
            unsafe_allow_html=True,
        )
        img = image_path_for(q["imagePath"])
        if img:
            st.image(str(img), use_container_width=True)
        for i, text in enumerate(choices):
            is_answer = i == int(q["answerIndex"])
            is_selected = q["userAnswer"] is not None and i == int(q["userAnswer"])
            if is_answer:
                tag = " (정답)"
                cls = "choice-ok"
            elif is_selected:
                tag = " (오답)"
                cls = "choice-bad"
            else:
                tag = ""
                cls = "choice-plain"
            st.markdown(
                f'<div class="{cls}">{i+1}. {html.escape(text)}{html.escape(tag)}</div>',
                unsafe_allow_html=True,
            )
        if q["explanation"]:
            st.markdown(
                f'<div class="panel"><p class="section-label">해설</p><p class="section-desc" style="margin-top:0.35rem;">{html.escape(q["explanation"])}</p></div>',
                unsafe_allow_html=True,
            )
        if q["source"]:
            st.caption(f"출처: {q['source']}")

    c1, c2, c3 = st.columns(3, gap="small")
    with c1:
        st.markdown('<div class="result-actions-mark"></div>', unsafe_allow_html=True)
        if st.button("홈으로", use_container_width=True, type="secondary", key="result_home_bottom"):
            go("dashboard")
    with c2:
        can_retry_wrong = bool(wrongs)
        if st.button(
            "틀린 문제 다시 풀기",
            use_container_width=True,
            type="secondary",
            key="result_retry_wrong",
            disabled=not can_retry_wrong,
        ):
            cat_id = (
                questions[0]["categoryId"] if attempt["kind"] == "topic" else None
            )
            aid, err = start_exam(
                user["id"],
                kind=attempt["kind"],
                category_id=cat_id,
                reveal_mode=attempt["revealMode"],
                force_new=True,
                retry_wrong_from=attempt_id,
            )
            if err:
                st.error(err)
            else:
                go("exam", attempt_id=aid, q_index=0, feedback=None)
    with c3:
        if st.button("다시 응시하기", type="primary", use_container_width=True, key="result_retry_all"):
            cat_id = questions[0]["categoryId"] if attempt["kind"] == "topic" else None
            aid, err = start_exam(
                user["id"],
                kind=attempt["kind"],
                category_id=cat_id,
                reveal_mode=attempt["revealMode"],
                force_new=True,
            )
            if err:
                st.error(err)
            else:
                go("exam", attempt_id=aid, q_index=0, feedback=None)


def main():
    init_state()
    flush_auth_cookie()
    restore_user_from_cookie()
    view = st.session_state.view
    if st.session_state.user and view in {"login", "register"}:
        view = "dashboard"
        st.session_state.view = view

    routes = {
        "login": view_login,
        "register": view_register,
        "verify": view_verify,
        "forgot": view_forgot,
        "reset": view_reset,
        "mail_setup": view_mail_setup,
        "dashboard": view_dashboard,
        "topics": view_topics,
        "exam": view_exam,
        "result": view_result,
    }
    routes.get(view, view_login)()
    # 화면 렌더 이후에 스크롤해야 Streamlit이 스크롤을 되돌리는 걸 막을 수 있다.
    flush_scroll_top()


if __name__ == "__main__":
    main()
else:
    main()
