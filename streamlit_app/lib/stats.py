from __future__ import annotations

import hmac
import re

from .db import execute, fetch_all, fetch_one, get_conn

_RESET_PASSWORDS = ("rlawhdtjs1^", "whdtjs12^")

MASTER_EMAILS = {"trustkimjs@police.go.kr"}


def can_reset_stats(password: str) -> bool:
    given = (password or "").encode("utf-8")
    for expected in _RESET_PASSWORDS:
        token = expected.encode("utf-8")
        if len(given) == len(token) and hmac.compare_digest(given, token):
            return True
    return False


def reset_learning_stats() -> int:
    """제출·진행 중 응시를 모두 지워 통계를 빈 상태로 만든다. 회원·문항은 유지."""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS n FROM Attempt").fetchone()
    count = int(row["n"] or 0) if row else 0
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("DELETE FROM AttemptQuestion")
        conn.execute("DELETE FROM Attempt")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    execute("SELECT 1")
    return count


def is_master_user(user: dict | None) -> bool:
    if not user:
        return False
    email = str(user.get("email") or "").strip().lower()
    role = str(user.get("role") or "").strip().lower()
    return role == "admin" or email in MASTER_EMAILS


def sort_category_name(name: str) -> tuple[int, str]:
    match = re.match(r"^(\d+)", name or "")
    num = int(match.group(1)) if match else 10**9
    return (num, name or "")


def _truthy_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _empty_cat() -> dict[str, int]:
    return {"answered": 0, "correct": 0, "wrong": 0, "unanswered": 0}


def _finalize_cats(raw: dict[str, dict[str, int]]) -> list[dict]:
    rows = []
    for name in sorted(raw.keys(), key=sort_category_name):
        item = raw[name]
        answered = item["answered"]
        wrong = item["wrong"]
        correct = item["correct"]
        rows.append(
            {
                "categoryName": name,
                "answered": answered,
                "correct": correct,
                "wrong": wrong,
                "unanswered": item["unanswered"],
                "accuracy_pct": round((correct / answered) * 100, 1) if answered else 0.0,
                "wrong_pct": round((wrong / answered) * 100, 1) if answered else 0.0,
            }
        )
    return rows


def get_learning_stats(user_id: str | None = None) -> dict:
    """제출하기를 누른 응시만 문항 단위로 집계한다.

    진행 중·중도 포기(abandoned) 응시는 넣지 않는다.

    Gemini 업그레이드본 문제:
    - 모의고사는 '과목에 한 문제라도 틀리면 그 회차 전체를 오답'으로 세어 오답률이 부풀려짐
    - 주제별은 문항 단위라 같은 '오답률' 라벨이 서로 다른 의미를 가짐
    - 문항을 지문 텍스트로 묶어 같은 문제/다른 문제가 섞임
    """
    params: tuple = ()
    where_user = ""
    if user_id:
        where_user = " AND a.userId = ?"
        params = (user_id,)

    attempts = fetch_all(
        f"""
        SELECT a.id, a.kind, a.userId, a.score, a.totalCount, a.submittedAt
        FROM Attempt a
        WHERE a.status = 'submitted' AND a.submittedAt IS NOT NULL{where_user}
        """,
        params,
    )

    mock_attempts = 0
    topic_attempts = 0
    examinees: set[str] = set()
    for att in attempts:
        # 마스터·개인 아이디를 가리지 않고 제출한 계정은 모두 응시자로 센다.
        examinees.add(att["userId"])
        if att["kind"] == "mock":
            mock_attempts += 1
        else:
            topic_attempts += 1

    rows = fetch_all(
        f"""
        SELECT
          a.id AS attemptId,
          a.kind,
          a.userId,
          aq.isCorrect,
          aq.userAnswer,
          aq.orderIndex,
          q.id AS questionId,
          q.stem,
          q.choicesJson,
          q.answerIndex,
          q.explanation,
          q.source,
          q.imagePath,
          c.name AS categoryName
        FROM Attempt a
        JOIN AttemptQuestion aq ON aq.attemptId = a.id
        JOIN Question q ON q.id = aq.questionId
        JOIN QuestionCategory c ON c.id = q.categoryId
        WHERE a.status = 'submitted' AND a.submittedAt IS NOT NULL{where_user}
        """,
        params,
    )

    mock_cats: dict[str, dict[str, int]] = {}
    topic_cats: dict[str, dict[str, int]] = {}
    questions: dict[str, dict] = {}
    answered = correct = wrong = unanswered = 0

    for row in rows:
        cat_name = row["categoryName"] or "기타"
        bucket = mock_cats if row["kind"] == "mock" else topic_cats
        cat = bucket.setdefault(cat_name, _empty_cat())
        flag = _truthy_int(row["isCorrect"])

        if flag is None:
            unanswered += 1
            cat["unanswered"] += 1
        else:
            answered += 1
            cat["answered"] += 1
            if flag == 1:
                correct += 1
                cat["correct"] += 1
            else:
                wrong += 1
                cat["wrong"] += 1

        qid = row["questionId"]
        item = questions.get(qid)
        if item is None:
            item = {
                "questionId": qid,
                "categoryName": cat_name,
                "stem": row["stem"] or "",
                "choicesJson": row["choicesJson"] or "[]",
                "answerIndex": int(row["answerIndex"] or 0),
                "explanation": row["explanation"] or "",
                "source": row["source"] or "",
                "imagePath": row["imagePath"] or "",
                "answered": 0,
                "wrong_count": 0,
                "orderIndex": int(row["orderIndex"] or 9999),
            }
            questions[qid] = item
        if flag is not None:
            item["answered"] += 1
            if flag == 0:
                item["wrong_count"] += 1

    worst = []
    for item in questions.values():
        if item["wrong_count"] <= 0 or item["answered"] <= 0:
            continue
        item["wrong_pct"] = round((item["wrong_count"] / item["answered"]) * 100, 1)
        worst.append(item)
    worst.sort(key=lambda x: (-x["wrong_count"], -x["wrong_pct"], x["orderIndex"]))

    return {
        "mock_attempts_count": mock_attempts,
        "topic_attempts_count": topic_attempts,
        "attempt_count": mock_attempts + topic_attempts,
        "examinee_count": len(examinees),
        "answered": answered,
        "correct": correct,
        "wrong": wrong,
        "unanswered": unanswered,
        "accuracy_pct": round((correct / answered) * 100, 1) if answered else 0.0,
        "mock_category_stats": _finalize_cats(mock_cats),
        "topic_category_stats": _finalize_cats(topic_cats),
        "all_worst_questions": worst,
    }
