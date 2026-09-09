"""Streamlit Community Cloud 잠김(슬립)을 깨운다.

PC를 켜 둘 필요 없다. GitHub Actions가 6시간마다 이 스크립트를 실행한다.
단순 HTTP GET은 정적 HTML만 받아 앱을 깨우지 못하므로,
브라우저로 접속해 WebSocket을 열고 잠김 버튼이 있으면 누른다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_URL = os.environ.get(
    "DAMOA_STREAMLIT_URL",
    "https://police-exam.streamlit.app/?embed=true&keepalive=1",
)
WAKE_LABELS = (
    "Yes, get this app back up!",
    "Yes, get this app back up",
    "Get this app back up",
)


def _log(message: str) -> None:
    print(message, flush=True)


def wake_with_playwright(url: str) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    _log("Playwright로 앱을 깨우는 중...")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(4_000)
        clicked = False
        for label in WAKE_LABELS:
            button = page.get_by_role("button", name=label)
            if button.count() > 0:
                _log(f"잠김 버튼 클릭: {label}")
                button.first.click()
                clicked = True
                page.wait_for_timeout(45_000)
                break
        if not clicked:
            text_btn = page.locator("button", has_text="get this app back up")
            if text_btn.count() > 0:
                _log("잠김 버튼 클릭 (텍스트 검색)")
                text_btn.first.click()
                clicked = True
                page.wait_for_timeout(45_000)
        if not clicked:
            page.wait_for_timeout(20_000)
            _log("잠김 버튼 없음. 접속만으로 타이머를 갱신했습니다.")
        browser.close()
    return True


def _edge_path() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return shutil.which("msedge") or shutil.which("chrome")


def wake_with_edge(url: str) -> bool:
    browser = _edge_path()
    if not browser:
        return False
    profile = Path(os.environ.get("TEMP", ".")) / "damoa-keepalive-profile"
    profile.mkdir(parents=True, exist_ok=True)
    _log(f"브라우저로 앱을 여는 중: {browser}")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            f"--user-data-dir={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(70)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    _log("브라우저 접속을 마쳤습니다. 이미 깨어 있으면 잠김 타이머가 갱신됩니다.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="다통과 Streamlit 잠김 방지")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    url = args.url
    _log(f"대상: {url}")

    in_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"

    if wake_with_playwright(url):
        _log("완료 (Playwright)")
        return 0
    if in_ci:
        _log("CI에서 Playwright를 쓰지 못했습니다. Actions 로그를 확인하세요.")
        return 1
    if wake_with_edge(url):
        _log("완료 (Edge/Chrome). 잠김 버튼 자동 클릭이 필요하면 playwright를 설치하세요.")
        return 0

    _log("브라우저를 찾지 못했습니다. Edge 설치 또는 pip install playwright 후 playwright install chromium")
    return 1


if __name__ == "__main__":
    sys.exit(main())
