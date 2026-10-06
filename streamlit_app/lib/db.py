from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE_IMAGES = ROOT / "storage" / "question-images"

_conn: sqlite3.Connection | None = None
_schema_ready = False
_pulled = False
_push_timer: threading.Timer | None = None
_push_lock = threading.Lock()
_write_lock = threading.Lock()
_resolved_db_path: Path | None = None


def get_db_path() -> Path:
    """Streamlit Cloud의 /mount/src 는 쓰기가 막히는 경우가 있어 /tmp 를 쓴다."""
    global _resolved_db_path
    if _resolved_db_path is not None:
        return _resolved_db_path
    repo_db = ROOT / "dev.db"
    on_cloud = str(ROOT).replace("\\", "/").startswith("/mount/src")
    if on_cloud:
        dest = Path(tempfile.gettempdir()) / "datonggwa" / "dev.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if repo_db.exists() and not dest.exists():
            dest.write_bytes(repo_db.read_bytes())
        _resolved_db_path = dest
        return dest
    _resolved_db_path = repo_db
    return repo_db


def _use_tmp_db() -> Path:
    global _resolved_db_path, _conn
    dest = Path(tempfile.gettempdir()) / "datonggwa" / "dev.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = _resolved_db_path
    if src and src.exists() and src.resolve() != dest.resolve():
        dest.write_bytes(src.read_bytes())
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
    _resolved_db_path = dest
    return dest

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
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True


def push_db_to_github() -> bool:
    cfg = github_sync_config()
    if not cfg or not get_db_path().exists():
        return False
    conn = _conn
    if conn is not None:
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.commit()
        except Exception:
            pass
    _ensure_data_branch(cfg)
    content = base64.b64encode(get_db_path().read_bytes()).decode("ascii")
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


def _apply_question_corrections(conn: sqlite3.Connection) -> bool:
    """한글 원본과 맞춰 정답·해설이 밀린 문항을 고친다."""
    try:
        rows = conn.execute(
            """
            SELECT q.id, q.stem, q.sourceOrder, c.name AS categoryName
            FROM "Question" q
            JOIN "QuestionCategory" c ON c.id = q.categoryId
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    if not rows:
        return False

    dv3_exp = (
        "1. 수사기관에 피해사실을 진술하거나 관련 자료를 제출할 수 있는 권리 "
        "- 고소의 특례(法제6조)를 알린다 "
        "- 피해자가 즉시 처벌을 원하지 않더라도 이후에 행위자를 고소할 수 있다는 점을 알려준다. "
        "2. 수사 절차와 진행 상황을 통보받을 권리 "
        "- 피해자에게 사법경찰이 응급조치, 긴급임시조치, 임시조치를 취할 수 있다는 점을 고지한다. "
        "- 피해자 또는 법정 대리인이 직접 법원에 퇴거, 접근금지, 친권 제한 등 "
        "｢피해자보호명령 제도｣를 요청할 수 있다는 점을 고지한다. "
        "3. 보호시설과 상담 지원을 신청할 권리 "
        "- 가정폭력 관련 상담소 및 보호시설 인도를 요구할 수 있다. "
        "- 정신적 피해 및 보호를 위한 상담소 등의 기관안내 및 연락처를 안내받을 수 있다. "
        "4. 경제적 지원을 신청할 권리 "
        "- 피해자가 사망하거나 장해를 입은 경우 담당 경찰관은 유족구조금, 장해구조금을 "
        "지급해 주는 지방검찰청 피해자지원 전담관을 연결해 지원받을 수 있다 "
        "5. 개인정보를 보호받을 권리 "
        "- 피해자의 동의 없이 신상정보를 공개하지 않는다"
    )
    dv1_exp = (
        "가정폭력범죄의 처벌 등에 관한 특례 제66조(과태료) 다음 각호의 어느 하나에 해당하는 "
        "사람에게는 300만원 이하의 과태료를 부과한다. 1. 정당한 사유 없이 제4조 제2항각호의 "
        "어느 하나에 해당하는 사람으로서 그 직무를 수행하면서 가정폭력범죄를 알게 된 경우에도 "
        "신고하지 아니한 사람 2. 정당한 사유 없이 제8조의2 제1항에 따른 긴급임시조치(검사가 "
        "제8조의3 제1항에 따른 임시조치를 청구하지 아니하거나 법원이 임시조치의 결정을 하지 "
        "아니한 때는 제외한다)를 이행하지 아니한 사람[전문개정 2014. 12. 30.]"
    )
    dv5_exp = (
        "손실이 있음을 안 날로부터 3년, 손실이 발생한 날부터 5년 이내에 행사하지 않으면 "
        "시효의 완성으로 소멸"
    )
    dui3_exp = (
        "단순히 화를 내며 측정을 거부한 것은 상황에 따라 객관적이고 진정한 의사표시에 의한 "
        "거부로 볼 수 없을 가능성이 있다는 판례의 취지입 "
        "3번: 측정시도 3회 이상이 아니라 음주측정 불응에 따른 불이익을 5분 간격으로 3회 이상 고지 "
        "1번: 물을 많이 주는 경찰의 인심이 후하다고 볼 수 있으나 경찰단속의 신뢰성과 공정성의 훼손 우려 "
        "2번: 형사·행정소송 관련 많이 제기되는 요소로 이의제기 차단을 위해 입에 물었던 불대는 "
        "1회 사용으로 적용"
    )
    changed = 0
    for row in rows:
        qid = row["id"]
        stem = row["stem"] or ""
        cat = row["categoryName"] or ""
        n = int(row["sourceOrder"] or 0)
        if cat.startswith("6.") and n == 9:
            conn.execute('UPDATE "Question" SET answerIndex = 2 WHERE id = ?', (qid,))
            changed += 1
        elif cat.startswith("8.") and n == 1:
            conn.execute(
                'UPDATE "Question" SET explanation = ? WHERE id = ?',
                (dv1_exp, qid),
            )
            changed += 1
        elif cat.startswith("8.") and n == 2:
            marker = "다음 중 가정폭력 처벌법 제2조"
            idx = stem.find(marker)
            if idx > 0:
                conn.execute(
                    'UPDATE "Question" SET stem = ? WHERE id = ?',
                    (stem[idx:], qid),
                )
                changed += 1
        elif cat.startswith("8.") and n == 3:
            conn.execute(
                'UPDATE "Question" SET answerIndex = 2, explanation = ? WHERE id = ?',
                (dv3_exp, qid),
            )
            changed += 1
        elif cat.startswith("8.") and n == 5:
            conn.execute(
                'UPDATE "Question" SET answerIndex = 3, explanation = ? WHERE id = ?',
                (dv5_exp, qid),
            )
            changed += 1
        elif cat.startswith("13.") and n == 3:
            conn.execute(
                'UPDATE "Question" SET stem = ?, explanation = ? WHERE id = ?',
                (stem.replace("가장 적절한 것은", "가장 올바른 것은"), dui3_exp, qid),
            )
            changed += 1
    if changed:
        conn.commit()
        print(f"[damoa] 문항 정오 {changed}건을 반영했습니다.", flush=True)
    return changed > 0


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
    try:
        if _apply_question_corrections(conn):
            _schedule_push()
    except Exception as exc:
        print(f"[damoa] 문항 정오 반영 실패: {exc}", flush=True)
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
            elif get_db_path().exists() and github_sync_config():
                print("[damoa] 데이터 브랜치가 비어 있어 현재 DB를 백업합니다.", flush=True)
                push_db_to_github()
        except Exception as exc:
            print(f"[damoa] GitHub DB 불러오기 실패: {exc}", flush=True)

    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA busy_timeout = 8000")
    _conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(_conn)
    return _conn


def fetch_one(sql: str, params: tuple = ()):
    cursor = get_conn().execute(sql, params)
    return _as_row(cursor, cursor.fetchone())


def fetch_all(sql: str, params: tuple = ()) -> list:
    cursor = get_conn().execute(sql, params)
    return [_as_row(cursor, row) for row in cursor.fetchall()]


def _is_readonly_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        part in msg
        for part in ("readonly", "read-only", "unable to open", "disk i/o")
    )


def execute(sql: str, params: tuple = ()) -> None:
    last_err: Exception | None = None
    for _ in range(3):
        try:
            conn = get_conn()
            with _write_lock:
                conn.execute(sql, params)
                conn.commit()
            _schedule_push()
            return
        except sqlite3.OperationalError as exc:
            last_err = exc
            if _is_readonly_error(exc):
                _use_tmp_db()
            time.sleep(0.25)
    if last_err:
        raise last_err


def executemany(sql: str, params_seq: list[tuple]) -> None:
    last_err: Exception | None = None
    for _ in range(3):
        try:
            conn = get_conn()
            with _write_lock:
                conn.executemany(sql, params_seq)
                conn.commit()
            _schedule_push()
            return
        except sqlite3.OperationalError as exc:
            last_err = exc
            if _is_readonly_error(exc):
                _use_tmp_db()
            time.sleep(0.25)
    if last_err:
        raise last_err


def ensure_attempt_tables() -> None:
    global _schema_ready
    conn = get_conn()
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "Attempt" not in names or "AttemptQuestion" not in names:
        _schema_ready = False
        _ensure_schema(conn)


def clear_exam_records() -> int:
    """응시·문항 답안만 지운다. 회원과 문제는 유지한다."""
    global _schema_ready
    conn = get_conn()
    last_err: Exception | None = None
    count = 0
    with _write_lock:
        try:
            row = conn.execute('SELECT COUNT(*) AS n FROM "Attempt"').fetchone()
            count = int(row["n"] or 0) if row else 0
        except sqlite3.OperationalError:
            count = 0
        for _ in range(4):
            try:
                conn.execute("PRAGMA busy_timeout = 8000")
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute('DELETE FROM "AttemptQuestion"')
                conn.execute('DELETE FROM "Attempt"')
                conn.commit()
                last_err = None
                break
            except sqlite3.OperationalError as exc:
                last_err = exc
                try:
                    conn.rollback()
                except Exception:
                    pass
                time.sleep(0.35)
        if last_err is not None:
            try:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute('DROP TABLE IF EXISTS "AttemptQuestion"')
                conn.execute('DROP TABLE IF EXISTS "Attempt"')
                conn.commit()
                _schema_ready = False
                _ensure_schema(conn)
                last_err = None
            except sqlite3.OperationalError as exc:
                last_err = exc
                try:
                    conn.rollback()
                except Exception:
                    pass
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
    if last_err is not None:
        raise last_err
    _schedule_push()
    return count
