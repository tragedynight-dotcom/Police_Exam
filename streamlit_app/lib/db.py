from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "dev.db"
STORAGE_IMAGES = ROOT / "storage" / "question-images"

_conn: sqlite3.Connection | None = None
_schema_ready = False
_pulled = False
_push_timer: threading.Timer | None = None
_push_lock = threading.Lock()

_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS "User" (
        "id" TEXT NOT NULL PRIMARY KEY,
        "email" TEXT NOT NULL,
        "passwordHash" TEXT NOT NULL,
        "name" TEXT NOT NULL,
        "organization" TEXT,
        "role" TEXT NOT NULL DEFAULT 'user',
        "isVerified" BOOLEAN NOT NULL DEFAULT false,
        "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "updatedAt" DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS "EmailVerification" (
        "id" TEXT NOT NULL PRIMARY KEY,
        "userId" TEXT NOT NULL,
        "codeHash" TEXT NOT NULL,
        "expiresAt" DATETIME NOT NULL,
        "attempts" INTEGER NOT NULL DEFAULT 0,
        "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS "QuestionCategory" (
        "id" TEXT NOT NULL PRIMARY KEY,
        "name" TEXT NOT NULL,
        "description" TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS "Question" (
        "id" TEXT NOT NULL PRIMARY KEY,
        "categoryId" TEXT NOT NULL,
        "difficulty" TEXT NOT NULL DEFAULT 'normal',
        "stem" TEXT NOT NULL,
        "choicesJson" TEXT NOT NULL,
        "answerIndex" INTEGER NOT NULL,
        "explanation" TEXT,
        "source" TEXT,
        "imagePath" TEXT,
        "sourceOrder" INTEGER NOT NULL DEFAULT 0,
        "isActive" BOOLEAN NOT NULL DEFAULT true,
        "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS "Attempt" (
        "id" TEXT NOT NULL PRIMARY KEY,
        "userId" TEXT NOT NULL,
        "status" TEXT NOT NULL DEFAULT 'in_progress',
        "revealMode" TEXT NOT NULL DEFAULT 'end',
        "kind" TEXT NOT NULL DEFAULT 'mock',
        "score" INTEGER,
        "totalCount" INTEGER NOT NULL,
        "startedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "submittedAt" DATETIME,
        "timeLimitMinutes" INTEGER NOT NULL DEFAULT 60
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS "AttemptQuestion" (
        "id" TEXT NOT NULL PRIMARY KEY,
        "attemptId" TEXT NOT NULL,
        "questionId" TEXT NOT NULL,
        "orderIndex" INTEGER NOT NULL,
        "userAnswer" INTEGER,
        "isCorrect" BOOLEAN
    )
    """,
    'CREATE UNIQUE INDEX IF NOT EXISTS "User_email_key" ON "User"("email")',
    'CREATE INDEX IF NOT EXISTS "EmailVerification_userId_idx" ON "EmailVerification"("userId")',
    'CREATE UNIQUE INDEX IF NOT EXISTS "QuestionCategory_name_key" ON "QuestionCategory"("name")',
    'CREATE INDEX IF NOT EXISTS "Question_categoryId_idx" ON "Question"("categoryId")',
    'CREATE INDEX IF NOT EXISTS "Question_isActive_idx" ON "Question"("isActive")',
    'CREATE INDEX IF NOT EXISTS "Question_categoryId_sourceOrder_idx" ON "Question"("categoryId", "sourceOrder")',
    'CREATE INDEX IF NOT EXISTS "Attempt_userId_idx" ON "Attempt"("userId")',
    'CREATE INDEX IF NOT EXISTS "AttemptQuestion_attemptId_idx" ON "AttemptQuestion"("attemptId")',
    'CREATE UNIQUE INDEX IF NOT EXISTS "AttemptQuestion_attemptId_questionId_key" ON "AttemptQuestion"("attemptId", "questionId")',
]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _secret(name: str, default: str = "") -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        import streamlit as st

        secrets = st.secrets
        if name in secrets:
            return str(secrets[name]).strip()
        github = secrets.get("github")
        if github:
            short = name.removeprefix("GITHUB_").lower()
            if short in github:
                return str(github[short]).strip()
    except Exception:
        pass
    return default


def github_sync_config() -> dict[str, str] | None:
    """Streamlit이 배포하는 main 이 아닌 데이터 전용 브랜치."""
    _load_dotenv()
    token = _secret("GITHUB_TOKEN")
    repo = _secret("GITHUB_REPO")
    if not token or not repo or "/" not in repo:
        return None
    return {
        "token": token,
        "repo": repo,
        "branch": _secret("GITHUB_DATA_BRANCH", "damoa-data"),
        "path": _secret("GITHUB_DATA_PATH", "dev.db"),
    }


def _gh_request(method: str, api_path: str, token: str, payload: dict | None = None):
    url = f"https://api.github.com{api_path}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "damoa-db-sync")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub {method} {api_path} -> {exc.code} {detail}") from exc


def _ensure_data_branch(cfg: dict[str, str]) -> None:
    repo = cfg["repo"]
    branch = cfg["branch"]
    token = cfg["token"]
    ref = _gh_request("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    if ref:
        return
    base = None
    for name in ("main", "master"):
        base = _gh_request("GET", f"/repos/{repo}/git/ref/heads/{name}", token)
        if base:
            break
    if not base:
        raise RuntimeError("GitHub main/master 브랜치를 찾지 못했습니다.")
    sha = base["object"]["sha"]
    created = _gh_request(
        "POST",
        f"/repos/{repo}/git/refs",
        token,
        {"ref": f"refs/heads/{branch}", "sha": sha},
    )
    if not created:
        raise RuntimeError(f"데이터 브랜치 {branch} 를 만들지 못했습니다.")


def pull_db_from_github() -> bool:
    cfg = github_sync_config()
    if not cfg:
        return False
    _ensure_data_branch(cfg)
    info = _gh_request(
        "GET",
        f"/repos/{cfg['repo']}/contents/{cfg['path']}?ref={cfg['branch']}",
        cfg["token"],
    )
    if not info or info.get("type") != "file":
        return False
    raw = base64.b64decode(info["content"].replace("\n", ""))
    if not raw:
        return False
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(raw)
    return True


def push_db_to_github() -> bool:
    cfg = github_sync_config()
    if not cfg or not DB_PATH.exists():
        return False
    conn = _conn
    if conn is not None:
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.commit()
        except Exception:
            pass
    _ensure_data_branch(cfg)
    content = base64.b64encode(DB_PATH.read_bytes()).decode("ascii")
    current = _gh_request(
        "GET",
        f"/repos/{cfg['repo']}/contents/{cfg['path']}?ref={cfg['branch']}",
        cfg["token"],
    )
    payload = {
        "message": "sync damoa exam records",
        "content": content,
        "branch": cfg["branch"],
    }
    if current and current.get("sha"):
        payload["sha"] = current["sha"]
    saved = _gh_request(
        "PUT",
        f"/repos/{cfg['repo']}/contents/{cfg['path']}",
        cfg["token"],
        payload,
    )
    return bool(saved)


def _schedule_push() -> None:
    if not github_sync_config():
        return
    global _push_timer
    with _push_lock:
        if _push_timer is not None:
            _push_timer.cancel()
        _push_timer = threading.Timer(2.0, _flush_push)
        _push_timer.daemon = True
        _push_timer.start()


def _flush_push() -> None:
    try:
        push_db_to_github()
    except Exception as exc:
        print(f"[damoa] GitHub DB 저장 실패: {exc}", flush=True)


def _as_row(cursor, row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return row
    cols = [item[0] for item in (cursor.description or [])]
    return dict(zip(cols, row))


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    for statement in _SCHEMA_SQL:
        conn.execute(statement)
    try:
        info = conn.execute('PRAGMA table_info("Attempt")').fetchall()
        names = {item[1] for item in info}
        if "revealMode" not in names:
            conn.execute(
                'ALTER TABLE "Attempt" ADD COLUMN "revealMode" TEXT NOT NULL DEFAULT \'end\''
            )
        if "kind" not in names:
            conn.execute(
                'ALTER TABLE "Attempt" ADD COLUMN "kind" TEXT NOT NULL DEFAULT \'mock\''
            )
    except Exception:
        pass
    conn.commit()
    _schema_ready = True


def get_conn() -> sqlite3.Connection:
    global _conn, _pulled
    if _conn is not None:
        return _conn

    if not _pulled:
        _pulled = True
        try:
            if pull_db_from_github():
                print("[damoa] GitHub 데이터 브랜치에서 기록을 불러왔습니다.", flush=True)
            elif DB_PATH.exists() and github_sync_config():
                print("[damoa] 데이터 브랜치가 비어 있어 현재 DB를 백업합니다.", flush=True)
                push_db_to_github()
        except Exception as exc:
            print(f"[damoa] GitHub DB 불러오기 실패: {exc}", flush=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(_conn)
    return _conn


def fetch_one(sql: str, params: tuple = ()):
    cursor = get_conn().execute(sql, params)
    return _as_row(cursor, cursor.fetchone())


def fetch_all(sql: str, params: tuple = ()) -> list:
    cursor = get_conn().execute(sql, params)
    return [_as_row(cursor, row) for row in cursor.fetchall()]


def execute(sql: str, params: tuple = ()) -> None:
    conn = get_conn()
    conn.execute(sql, params)
    conn.commit()
    _schedule_push()


def executemany(sql: str, params_seq: list[tuple]) -> None:
    conn = get_conn()
    conn.executemany(sql, params_seq)
    conn.commit()
    _schedule_push()
