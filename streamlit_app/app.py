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
# 앱 구동 필수 변수 
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

import lib.exam as _lib_exam   # noqa: E402

# =====================================================================
# [안전한 백엔드 패치] 튜플 에러 완벽 차단 방어막
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
# =====================================================================

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

def init_state():
    defaults = {
        "view": "login",
        "user": None,
        "dev_otp": None,
        "verify_email": "",
        "reset_email": "",
        "topics_mode": "end",  # 시험 모드로 고정
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

    # 새로고침 시 로그아웃 방지 (토큰 유지)
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
    components.html(f"<script>try{{window.top.postMessage({{type:'DAMOA_LOGIN', token:'{token}'}}, '*');}}catch(e){{}}</script>", height=0, width=0)
    go(view, **kwargs)

def logout():
    st.session_state.user = None
    st.session_state._force_logout = True
    if "auth" in st.query_params:
        del st.query_params["auth"]
        
    components.html("<script>try{{window.top.postMessage({{type:'DAMOA_LOGOUT'}}, '*');}}catch(e){{}}</script>", height=0, width=0)
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
            f"""<script>(function(){{ let d=document,w=window; try{{if(w.parent&&w.parent.document){{d=w.parent.document;w=w.parent;}}}}catch(e){{}} const s={sel_js},b={block_js}; function t(){{const el=d.querySelector(s); if(!el)return false; el.scrollIntoView({{behavior:"auto",block:b}}); return true;}} t(); requestAnimationFrame(t); setTimeout(t,50); setTimeout(t,150); setTimeout(t,350);}})();</script>""", height=0, width=0
        )
        return

    components.html(
        f"""<script>(function(){{ let d=document,w=window; try{{if(w.parent&&w.parent.document){{d=w.parent.document;w=w.parent;}}}}catch(e){{}} function tTop(){{ const seen=new Set(); function z(el){{if(!el||seen.has(el))return; seen.add(el); try{{el.scrollTop=0;}}catch(e){{}} try{{el.scrollLeft=0;}}catch(e){{}} try{{el.scrollTo&&el.scrollTo(0,0);}}catch(e){{}} }} z(d.scrollingElement); z(d.documentElement); z(d.body); d.querySelectorAll('[data-testid="stMain"], [data-testid="stAppViewContainer"], [data-testid="stMainBlockContainer"], section.main, .main, .stApp, .block-container').forEach(z); const a=d.querySelector('.exam-page-top')||d.querySelector('.exam-top')||d.querySelector('.exam-question-anchor')||d.querySelector('.block-container'); let c=a; while(c&&c!==d.body&&c!==d.documentElement){{ const sty=w.getComputedStyle(c); const oy=sty.overflowY; if(oy==='auto'||oy==='scroll'||oy==='overlay'||c.scrollTop>0) z(c); c=c.parentElement; }} if(a)try{{a.scrollIntoView({{behavior:'auto',block:'start'}});}}catch(e){{}} w.scrollTo(0,0); }} const u=Date.now()+900; function lTop(){{tTop(); if(Date.now()<u)requestAnimationFrame(lTop);}} lTop(); [50,120,250,450,700].forEach(function(t){{setTimeout(tTop,t);}});}})();</script>""", height=0, width=0
    )

def stem_html(stem: str) -> str:
    stem = strip_difficulty_marker(stem or "")
    prompt, items = split_boxed_stem(stem)
    if not items:
        return f'<p class="q-stem">{html.escape(stem)}</p>'
    items_html = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f'<div class="q-stem-wrap"><p class="q-stem">{html.escape(prompt)}</p><div class="q-stem-box"><ul>{items_html}</ul></div></div>'

def require_user():
    user = st.session_state.user
    if not user or not user.get("isVerified"):
        go("login")
    return user

def email_input(label: str = "경찰웹메일 ID", key: str = "email_local", value: str = "trustkimjs") -> str:
    st.markdown(f'<p style="margin:0 0 0.3rem;font-size:0.9rem;font-weight:500;color:#132238;">{label}</p>', unsafe_allow_html=True)
    c1, c2 = st.columns([6, 1.35], gap="small")
    with c1:
        local = st.text_input(label, value=value, key=key, placeholder="경찰웹메일 ID", label_visibility="collapsed")
    with c2:
        st.markdown(f'<div class="email-domain-mark">@{ALLOWED_EMAIL_DOMAIN}</div>', unsafe_allow_html=True)
    return full_police_email(local or "")

def auth_layout(title: str, subtitle: str | None, body):
    st.markdown('<div class="auth-form-col">', unsafe_allow_html=True)
    sub = f'<p class="auth-sub">{subtitle}</p>' if subtitle else ""
    st.markdown(f'<p class="auth-brand-link">지역경찰 역량 강화를 위한 실무 역량 평가 DaMoa</p><h2 class="auth-title">{title}</h2>{sub}', unsafe_allow_html=True)
    body()
    st.markdown("</div>", unsafe_allow_html=True)

# ---------- Auth views ----------
def view_login():
    def body():
        try:
            _login_form = st.form("login_form", clear_on_submit=False, border=False, enter_to_submit=False)
        except TypeError:
            _login_form = st.form("login_form", clear_on_submit=False, border=False)
            
        with _login_form:
            email = email_input(key="login_local", value="trustkimjs")
            password = st.text_input("비밀번호", type="password", key="login_pw", placeholder="비밀번호")
            submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
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
            st.markdown('<p class="auth-link-label">계정이 없으신가요?&nbsp;</p>', unsafe_allow_html=True)
        with r1b:
            if st.button("회원가입", type="secondary", key="login_to_register"):
                go("register")
        r2a, r2b = st.columns([1.9, 1.2], gap="small")
        with r2a:
            st.markdown('<p class="auth-link-label">비밀번호를 잊어버렸다면?&nbsp;</p>', unsafe_allow_html=True)
        with r2b:
            if st.button("비밀번호 재설정", type="secondary", key="login_to_forgot"):
                go("forgot")

    auth_layout("로그인", "회원가입을 눌러 경찰 웹메일로 경찰 인증 후 사용하세요.", body)

def view_register():
    def body():
        try:
            _reg_form = st.form("register_form", clear_on_submit=False, border=False, enter_to_submit=False)
        except TypeError:
            _reg_form = st.form("register_form", clear_on_submit=False, border=False)
        with _reg_form:
            name = st.text_input("닉네임", key="reg_name", placeholder="닉네임")
            organization = st.text_input("소속", key="reg_org", placeholder="소속")
            email = email_input(key="reg_local", value="")
            password = st.text_input("비밀번호 (8자 이상)", type="password", key="reg_pw", placeholder="비밀번호")
            submitted = st.form_submit_button("인증번호 받기", type="primary", use_container_width=True)
            if submitted:
                email_safe = email.strip().lower()
                ok, msg, code = register_user(name, email_safe, password, organization)
                if ok and code:
                    try:
                        from lib.mail import send_otp_email
                        send_otp_email(email_safe, code)
                        st.session_state.dev_otp = None
                        st.success("인증번호를 이메일로 발송했습니다. 메일함을 확인해 주세요.")
                        go("verify", verify_email=email_safe)
                    except Exception as e:
                        st.error("인증번호 발송에 실패했습니다.")
                elif ok:
                    st.error("인증번호 발급에 실패했습니다.")
                else:
                    st.error(msg)
        if st.button("로그인으로", type="secondary", key="reg_to_login"):
            go("login")
    auth_layout("회원가입", "경찰청 웹메일(@police.go.kr)로 가입 후 인증번호를 받아 주세요.", body)

def view_verify():
    def body():
        email = st.text_input("이메일", value=st.session_state.verify_email, key="verify_email_input")
        code = st.text_input("인증번호 6자리", max_chars=6, key="verify_code", placeholder="6자리")
        if st.button("인증 완료", type="primary", use_container_width=True):
            email_safe = email.strip().lower()
            ok, msg = verify_otp(email_safe, code)
            if ok:
                st.session_state.dev_otp = None
                from lib.db import fetch_one
                user = fetch_one("SELECT * FROM User WHERE email = ?", (email_safe,))
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
                    from lib.mail import send_otp_email
                    send_otp_email(email_safe, code)
                    st.session_state.dev_otp = None
                    st.success("인증번호를 발송했습니다.")
                    go("reset", reset_email=email_safe)
                except Exception as e:
                    st.error("메일 발송에 실패했습니다.")
            else:
                st.error(msg)
        if st.button("로그인으로", type="secondary"):
            go("login")
    auth_layout("비밀번호 재설정", "가입한 웹메일로 인증번호를 받아 새 비밀번호를 설정하세요.", body)

def view_reset():
    def body():
        email = st.text_input("이메일", value=st.session_state.reset_email, key="reset_email_input")
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
        st.markdown("메일 발송 설정이 필요할 때 참고하세요.")
        if st.button("로그인으로", type="secondary"):
            go("login")
    auth_layout("메일 설정 안내", "", body)


# ---------- App views ----------

def app_shell_css():
    st.html(
        """
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap">
        <style>
          /* =========================================================
             [완벽 수정본 CSS] - 텍스트 보호색 차단 및 명확한 버튼 디자인
             ========================================================= */
          
          /* 1. 글로벌 테마 리셋 (다크모드에서도 배경은 밝게) */
          .stApp, .main, .block-container, [data-testid="stAppViewContainer"], header {
              background-color: #f4f7fb !important;
          }

          /* 2. 일반 텍스트는 진한 남색으로 덮기 (글자 실종 방지) */
          p, span, div, label, h1, h2, h3, li, .q-stem, .auth-title {
              color: #132238 !important;
          }

          /* 3. [핵심] 버튼과 배너 안의 텍스트는 글로벌 리셋에서 예외 처리하여 보호!! */
          button p, button span, button div {
              color: inherit !important; /* 부모(버튼)의 색상을 따라가라 */
          }
          
          .topics-panel-inner *, .mock-panel-inner * {
              color: #ffffff !important; /* 배너 안쪽은 무조건 하얀 글씨 */
          }
          .topics-panel-inner .topics-kicker, .mock-panel-inner .mock-kicker, .topics-panel-inner strong {
              color: #c9a227 !important; /* 포인트 문구는 금색 */
          }
          .topics-panel-inner .topics-meta, .mock-panel-inner .mock-desc {
              color: rgba(255,255,255,0.78) !important;
          }

          /* =========================================================
             컴포넌트 세부 디자인
             ========================================================= */
          
          /* 공통 버튼 베이스 설정 */
          button[kind="primary"] {
              background-color: #0b2a4a !important;
              color: #ffffff !important;
              border: none !important;
              border-radius: 0.55rem !important;
          }
          button[kind="secondary"] {
              background-color: #ffffff !important;
              color: #132238 !important;
              border: 1px solid #d7e0ea !important;
              border-radius: 0.55rem !important;
          }

          /* 파란색 배너 디자인 */
          .topics-panel-inner {
              background: linear-gradient(145deg, #071c33 0%, #0b2a4a 52%, #123b63 100%) !important;
              border-radius: 1.15rem !important; padding: 1.25rem !important; margin-bottom: 1rem !important;
              box-shadow: 0 16px 40px rgba(7, 28, 51, 0.22) !important;
          }
          .mock-panel-inner {
              background: linear-gradient(155deg, #0e3358 0%, #1f4e79 52%, #2d6494 100%) !important;
              border-radius: 1.15rem !important; padding: 1.25rem !important; margin-bottom: 1rem !important;
              box-shadow: 0 16px 40px rgba(7, 28, 51, 0.18) !important;
          }
          .topics-hero, .mock-hero { font-size: 1.5rem !important; font-weight: 800 !important; margin: 0.5rem 0 !important; }
          .topics-mode-hints p { background: rgba(255,255,255,0.1) !important; padding: 0.5rem !important; border-radius: 0.5rem !important; font-size: 0.85rem !important; }

          /* 대시보드 커스텀 버튼 (황금색 / 빨간색) */
          div[data-testid="column"]:has(.topics-panel-inner) button[kind="primary"] {
              background-color: #c9a227 !important; color: #ffffff !important; height: 3.2rem !important; border-radius: 0.8rem !important; font-weight: 800 !important; font-size: 1.1rem !important; width: 100% !important;
          }
          div[data-testid="column"]:has(.mock-panel-inner) button[kind="primary"] {
              background-color: #ffffff !important; color: #e63946 !important; border: 2px solid #e63946 !important; height: 3.2rem !important; border-radius: 0.8rem !important; font-weight: 800 !important; font-size: 1.1rem !important; width: 100% !important;
          }

          /* 타이머 디자인 (노란 바탕, 굵은 빨간 글씨) */
          #realtime-timer, .timer-pill {
              background-color: #fff3cd !important; color: #d90429 !important; border: 2px solid #d90429 !important;
              padding: 0.35rem 0.85rem !important; border-radius: 20px !important; font-weight: 900 !important;
              font-size: 0.95rem !important; display: inline-block !important; margin: 0 !important; text-align: center !important;
          }

          /* 이어하기 배너 */
          .resume-inline {
              background: rgba(201,162,39,0.1) !important; border: 1px solid #c9a227 !important; border-radius: 0.8rem !important; padding: 1rem !important; margin-bottom: 1rem !important; display: flex !important; flex-direction: column !important; justify-content: center !important;
          }
          .resume-title { font-weight: 700 !important; font-size: 0.95rem !important; margin:0 !important; }
          .resume-desc { font-size: 0.85rem !important; color: #5b6b7c !important; margin-top: 0.2rem !important; }
          
          div[data-testid="stHorizontalBlock"]:has(.resume-inline) { align-items: center !important; }
          div[data-testid="stHorizontalBlock"]:has(.resume-inline) > div:last-child { flex: 0 0 auto !important; }
          
          /* 하단 네비게이션 3버튼 1줄 고정 (마커 없이 순수 CSS로 구현하여 에러 0%) */
          div[data-testid="stHorizontalBlock"]:has(> div > .element-container > .stButton > button[key^="exam_"]) {
              display: flex !important; flex-direction: row !important; gap: 0.35rem !important; align-items: stretch !important; margin: 0.35rem 0 0.15rem !important;
          }
          div[data-testid="stHorizontalBlock"]:has(> div > .element-container > .stButton > button[key^="exam_"]) > div[data-testid="column"] {
              flex: 1 1 0 !important; width: 33.3% !important; min-width: 0 !important; display: block !important;
          }
          div[data-testid="stHorizontalBlock"]:has(> div > .element-container > .stButton > button[key^="exam_"]) button {
              width: 100% !important; height: 2.2rem !important; padding: 0.35rem 0.5rem !important; font-size: 0.8rem !important;
          }

          /* 카드 배너 (주제별 리스트 등) */
          .card-banner-inner { background: #ffffff !important; border: 1px solid #d7e0ea !important; border-radius: 0.8rem !important; padding: 1rem !important; margin-bottom: 0.5rem !important; }
          .card-banner-navy { background: rgba(11,42,74,0.05) !important; border-color: rgba(11,42,74,0.2) !important; }
          .card-banner-inner .section-title { font-weight: 700 !important; margin: 0 !important; color: #132238 !important; }
          .card-banner-inner .section-desc { font-size: 0.8rem !important; color: #5b6b7c !important; margin-top: 0.2rem !important; }
          
          div[data-testid="stHorizontalBlock"]:has(.card-banner-inner) { align-items: center !important; }
          div[data-testid="stHorizontalBlock"]:has(.card-banner-inner) > div:last-child { flex: 0 0 auto !important; min-width: 5.5rem !important; }
          div[data-testid="stHorizontalBlock"]:has(.card-banner-inner) button { height: 2.2rem !important; border-radius: 0.5rem !important; }

          /* 불필요한 공백 제거 */
          .block-container {
              max-width: 960px !important; padding: 2rem !important;
          }
          @media (max-width: 900px) {
              .block-container { padding: 1rem !important; }
          }
        </style>
        """
    )
    
    # --------- 오디오 버튼음 (디자인 자바스크립트는 모두 삭제함) ---------
    components.html(
        """
        <script>
        (function() {
            let doc = document;
            let win = window;
            try { if (window.parent && window.parent.document) { doc = window.parent.document; win = window.parent; } } catch(e) {}
            if (!win.__audio_click_injected) {
                win.__audio_click_injected = true;
                let actx = null;
                function initAudio() {
                    if (!actx) { let AudioCtx = win.AudioContext || win.webkitAudioContext; if (AudioCtx) actx = new AudioCtx(); }
                    if (actx && actx.state === 'suspended') actx.resume();
                }
                doc.addEventListener('touchstart', initAudio, { once: true, capture: true });
                doc.addEventListener('click', initAudio, { once: true, capture: true });
                function playClick() {
                    try {
                        initAudio(); if (!actx) return;
                        const osc = actx.createOscillator(); const gain = actx.createGain();
                        osc.connect(gain); gain.connect(actx.destination);
                        osc.type = 'sine'; osc.frequency.setValueAtTime(900, actx.currentTime);
                        osc.frequency.exponentialRampToValueAtTime(300, actx.currentTime + 0.08);
                        gain.gain.setValueAtTime(0.8, actx.currentTime);
                        gain.gain.exponentialRampToValueAtTime(0.01, actx.currentTime + 0.08);
                        osc.start(actx.currentTime); osc.stop(actx.currentTime + 0.08);
                    } catch(e) {}
                }
                doc.addEventListener('click', function(e) {
                    let target = e.target;
                    let isButton = target.closest('button') || target.closest('[data-testid="stRadio"] label') || (target.tagName === 'INPUT' && target.type === 'radio');
                    if (isButton) playClick();
                }, true);
            }
        })();
        </script>
        """,
        height=0, width=0
    )


def sort_topics(cats):
    def key_fn(c):
        import re
        m = re.match(r"^(\d+)", c["name"] or "")
        num = int(m.group(1)) if m else 10**9
        return (num, c["name"])
    return sorted(cats, key=key_fn)


def topic_mix_rows(questions: list) -> list[dict]:
    import re
    from collections import Counter, defaultdict
    counts: Counter[str] = Counter()
    correct: dict[str, int] = defaultdict(int)
    for q in questions:
        name = q.get("categoryName") or "기타"
        counts[name] += 1
        if q.get("isCorrect"): correct[name] += 1

    def key_fn(name: str):
        m = re.match(r"^(\d+)", name or "")
        num = int(m.group(1)) if m else 10**9
        return (num, name)

    rows = []
    for name in sorted(counts.keys(), key=key_fn):
        rows.append({"name": name, "count": counts[name], "correct": correct[name]})
    return rows


def view_dashboard():
    user = require_user()
    count = topic_count()

    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">
          <p class="damoa-brand" style="margin:0;font-weight:700;">지역 경찰 실무 역량 평가 DaMoa</p>
          <span style="background-color:#d7e0ea;color:#132238;padding:0.1rem 0.4rem;border-radius:0.3rem;font-size:0.7rem;font-weight:700;">인증됨</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    greet_l, greet_r = st.columns([8, 2], gap="small")
    with greet_l:
        st.markdown(
            f"""
            <p class="damoa-title greet-title" style="font-size:1.4rem;font-weight:800;margin:0;">안녕하세요, {html.escape(user["name"])}님</p>
            <p class="user-email" style="font-size:0.85rem;color:#5b6b7c;margin:0;">{html.escape(user["email"])}</p>
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
                <div class="resume-inline">
                  <p class="resume-title">풀고 있던 문제가 있습니다.</p>
                  <p class="resume-desc">저장해 둔 답안부터 이어서 풀 수 있습니다.</p>
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
              <p class="topics-kicker">실무 역량 학습</p>
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
        st.markdown('<p style="font-size:0.9rem;color:#5b6b7c;">학습 기록이 없습니다. 시험이나 모의고사를 완료하면 여기에 표시됩니다.</p>', unsafe_allow_html=True)
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
    mode = "end"

    st.markdown(
        f"""
        <p style="font-size:0.8rem;color:#5b6b7c;margin:0;">지역 경찰 실무 역량 평가 DaMoa</p>
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
                <div class="resume-inline">
                  <p class="resume-title">진행 중인 시험이 있습니다.</p>
                  <p class="resume-desc">아래에서 새 주제를 시작하면 이전 진행은 자동 제출됩니다.</p>
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
    attempt_id = st.session_state.attempt_id
    attempt, questions = load_exam(attempt_id, user["id"])
    if not attempt:
        st.error("시험을 찾을 수 없습니다.")
        if st.button("홈으로"): go("dashboard")
        return

    if attempt["status"] == "submitted": go("result", attempt_id=attempt_id)

    is_learn_mode = attempt["revealMode"] == "immediate"

    if not is_learn_mode and is_time_expired(attempt):
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
        <div class="exam-page-top exam-top" id="exam-page-top" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
          <div>
            <p style="font-size:0.8rem;color:#5b6b7c;margin:0;font-weight:600;">
              DaMoa <span style="color:#132238;">· {mode_label}</span>
            </p>
            <p style="margin:0.4rem 0 0;color:#0b2a4a;font-weight:800;font-size:1.1rem;">진행 {answered}/{attempt["totalCount"]}</p>
            {cat_line}
          </div>
          <div id="realtime-timer" class="timer-pill">남은 시간 {mm:02d}:{ss:02d}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div class="exam-question-anchor" id="exam-question">'
        f'<p style="margin:0.6rem 0 1rem;color:#0b2a4a;font-size:1.35rem;font-weight:800;">문제 {idx + 1}</p>'
        f'{stem_html(q["stem"] or "")}'
        f"</div>",
        unsafe_allow_html=True,
    )

    img = image_path_for(q["imagePath"])
    if img: st.image(str(img), use_container_width=True)

    choices = parse_choices(q["choicesJson"])
    is_last = idx >= len(questions) - 1
    locked = is_learn_mode and q["userAnswer"] is not None
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
            if not is_last:
                st.session_state.q_index = idx + 1
                st.session_state.feedback = None
            request_scroll_top()
            st.rerun()
        else:
            st.error(msg)

    st.markdown("<div style='height:2rem;'></div>", unsafe_allow_html=True)
    
    # [네비게이션 버튼 3종] CSS의 :has()로 강제 가로 1줄 정렬됩니다.
    nav_l, nav_m, nav_r = st.columns(3, gap="small")
    with nav_l:
        if st.button("이전", disabled=idx <= 0, use_container_width=True, type="secondary", key="exam_prev"):
            st.session_state.q_index = idx - 1
            request_scroll_top()
            st.rerun()
    with nav_m:
        next_label = "제출하기" if is_last else "다음"
        if st.button(next_label, type="primary", use_container_width=True, key="exam_next_mid"):
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
                request_scroll_top()
                st.rerun()
    with nav_r:
        if st.button("홈으로", use_container_width=True, type="secondary", key="exam_home"):
            go("dashboard")

    if not is_learn_mode:
        components.html(
            f"""
            <script>
            (function() {{
                let doc = document; let win = window;
                try {{ if (window.parent && window.parent.document) {{ doc = window.parent.document; win = window.parent; }} }} catch (e) {{}} 
                if (win.examTimerInterval) clearInterval(win.examTimerInterval);
                const endsAt = {ends.timestamp()} * 1000;
                win.examTimerInterval = setInterval(function() {{
                    const el = doc.getElementById('realtime-timer');
                    if (!el) return;
                    let remain = Math.floor((endsAt - Date.now()) / 1000);
                    if (remain < 0) remain = 0;
                    let m = String(Math.floor(remain / 60)).padStart(2, '0');
                    let s = String(remain % 60).padStart(2, '0');
                    el.innerText = "남은 시간 " + m + ":" + s;
                    if (remain <= 0) {{ el.style.backgroundColor = "#e63946"; el.style.color = "white"; clearInterval(win.examTimerInterval); }}
                }}, 1000);
            }})();
            </script>
            """, height=0, width=0
        )


def view_result():
    user = require_user()
    attempt_id = st.session_state.attempt_id
    attempt, questions = load_exam(attempt_id, user["id"])
    if not attempt:
        st.error("결과를 찾을 수 없습니다.")
        if st.button("홈으로"): go("dashboard")
        return

    if attempt["status"] != "submitted": go("exam", attempt_id=attempt_id)

    if st.session_state.get("_result_filter_attempt") != attempt_id:
        st.session_state._result_filter_attempt = attempt_id
        reset_result_filters()

    score = attempt["score"] or 0
    total = attempt["totalCount"]
    pct = round(score / total * 100) if total else 0
    wrongs = [q for q in questions if not q["isCorrect"]]

    st.markdown('<p style="font-size:0.8rem;color:#5b6b7c;margin:0;">지역 경찰 실무 역량 평가 DaMoa</p>', unsafe_allow_html=True)
    st.markdown('<p style="font-size:1.5rem;font-weight:800;margin:0 0 1rem;">채점 결과</p>', unsafe_allow_html=True)

    def result_action_row(key_prefix: str) -> None:
        cat_id = questions[0]["categoryId"] if attempt["kind"] == "topic" else None
        c1, c2, c3 = st.columns(3, gap="small")
        with c1:
            if st.button("홈으로", use_container_width=True, type="secondary", key=f"{key_prefix}_home"): go("dashboard")
        with c2:
            if st.button("틀린 문제 다시 풀기", use_container_width=True, type="secondary", key=f"{key_prefix}_retry_wrong", disabled=not bool(wrongs)):
                aid, err = start_exam(user["id"], kind=attempt["kind"], category_id=cat_id, reveal_mode=attempt["revealMode"], force_new=True, retry_wrong_from=attempt_id)
                if err: st.error(err)
                else: go("exam", attempt_id=aid, q_index=-1, feedback=None)
        with c3:
            if st.button("다시 응시하기", use_container_width=True, type="primary", key=f"{key_prefix}_retry_all"):
                aid, err = start_exam(user["id"], kind=attempt["kind"], category_id=cat_id, reveal_mode=attempt["revealMode"], force_new=True)
                if err: st.error(err)
                else: go("exam", attempt_id=aid, q_index=-1, feedback=None)

    result_action_row("result_top")

    m1, m2, m3 = st.columns(3, gap="small")
    with m1: st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.8rem;text-align:center;"><p style="font-size:0.8rem;color:#5b6b7c;margin:0;">점수</p><p style="font-size:1.2rem;font-weight:800;color:#132238;margin:0;">{score}/{total}</p></div>', unsafe_allow_html=True)
    with m2: st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.8rem;text-align:center;"><p style="font-size:0.8rem;color:#5b6b7c;margin:0;">정답률</p><p style="font-size:1.2rem;font-weight:800;color:#132238;margin:0;">{pct}%</p></div>', unsafe_allow_html=True)
    with m3: st.markdown(f'<div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.6rem;padding:0.8rem;text-align:center;"><p style="font-size:0.8rem;color:#5b6b7c;margin:0;">틀린 문제</p><p style="font-size:1.2rem;font-weight:800;color:#e63946;margin:0;">{len(wrongs)}</p></div>', unsafe_allow_html=True)

    st.markdown("<div style='height:1.5rem;'></div>", unsafe_allow_html=True)

    show_filter = attempt["kind"] == "mock" or attempt["revealMode"] == "end"
    if show_filter:
        st.session_state.result_wrong_only = st.toggle("틀린 문제만 보기", value=st.session_state.result_wrong_only, key="result_wrong_toggle")

    review = wrongs if show_filter and st.session_state.result_wrong_only else questions

    for q in review:
        choices = parse_choices(q["choicesJson"])
        cat = f'<p style="font-size:0.8rem;color:#5b6b7c;font-weight:600;margin:0;">{q["categoryName"]}</p>' if attempt["kind"] != "mock" else ""
        
        st.markdown(
            f"""
            <div style="background:#fff;border:1px solid #d7e0ea;border-radius:0.8rem;padding:1.2rem;margin-top:1rem;">
              {cat}
              <p style="font-size:1.1rem;font-weight:800;color:#132238;margin:0.2rem 0 0.8rem;">문제 {q["orderIndex"]}</p>
              {stem_html(q["stem"] or "")}
            """,
            unsafe_allow_html=True,
        )
        img = image_path_for(q["imagePath"])
        if img: st.image(str(img), use_container_width=True)
        
        for i, text in enumerate(choices):
            is_answer = i == int(q["answerIndex"])
            is_selected = q["userAnswer"] is not None and i == int(q["userAnswer"])
            
            if is_answer:
                tag = " (정답)"
                bg, border, color = "rgba(46, 204, 113, 0.1)", "2px solid #2ecc71", "#2ecc71"
            elif is_selected:
                tag = " (오답)"
                bg, border, color = "rgba(230, 57, 70, 0.1)", "2px solid #e63946", "#e63946"
            else:
                tag = ""
                bg, border, color = "#f4f7fb", "1px solid #d7e0ea", "#132238"
                
            st.markdown(f'<div style="background:{bg}; border:{border}; color:{color}; padding:0.8rem; border-radius:0.5rem; margin-bottom:0.5rem; font-weight:600;">{i+1}. {html.escape(text)}{html.escape(tag)}</div>', unsafe_allow_html=True)
            
        if q["explanation"]:
            st.markdown(f'<div style="background:#f9f9f9;border-left:4px solid #c9a227;padding:1rem;margin-top:1rem;"><p style="font-weight:700;color:#132238;margin:0 0 0.4rem;">해설</p><p style="color:#5b6b7c;margin:0;font-size:0.9rem;">{html.escape(q["explanation"])}</p></div>', unsafe_allow_html=True)
        if q["source"]:
            st.markdown(f'<p style="font-size:0.75rem;color:#a0aab5;margin-top:0.5rem;">출처: {html.escape(q["source"])}</p>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:2rem;'></div>", unsafe_allow_html=True)
    result_action_row("result_bottom")


def main():
    init_state()
    
    # 디자인(CSS) 강제 주입
    app_shell_css()
    
    restore_user_from_url()
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
        
    flush_scroll_top()

if __name__ == "__main__":
    main()
else:
    main()
