from __future__ import annotations

import json
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .auth import now_iso
from .db import STORAGE_IMAGES, execute, executemany, fetch_all, fetch_one

EXAM_QUESTION_COUNT = 40
MINUTES_PER_QUESTION = 1

# ㉠/㉮ 또는 ㄱ./ㄴ. 지문형 문항
_BOX_MARKER_RE = re.compile(r"[㉠-㉥㉮-㉳]|[ㄱ-ㅎ]\.")
_BOX_SPLIT_RE = re.compile(r"(?=[㉠-㉥㉮-㉳]|[ㄱ-ㅎ]\.)")


def new_id() -> str:
    return "c" + secrets.token_hex(12)


def calc_time_limit_minutes(question_count: int) -> int:
    return max(1, question_count * MINUTES_PER_QUESTION)


def shuffle(items: list):
    arr = list(items)
    random.shuffle(arr)
    return arr


def parse_choices(choices_json: str) -> list[str]:
    try:
        data = json.loads(choices_json)
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    return []


_DIFFICULTY_RE = re.compile(r"\s*[\(（][상중하][\)）]\s*")


def strip_difficulty_marker(stem: str) -> str:
    """지문 끝의 (상)/(중)/(하) 난이도 표기를 제거한다."""
    if not stem:
        return stem or ""
    cleaned = _DIFFICULTY_RE.sub(" ", stem)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def split_boxed_stem(stem: str) -> tuple[str, list[str] | None]:
    """지문형 문항을 질문 + 박스 항목으로 분리한다."""
    text = strip_difficulty_marker(stem or "")
    match = _BOX_MARKER_RE.search(text)
    if not match or match.start() <= 0:
        return text, None

    prompt = text[: match.start()].strip()
    rest = text[match.start() :].strip()
    items = [part.strip() for part in _BOX_SPLIT_RE.split(rest) if part.strip()]
    if len(items) < 2:
        return text, None
    return prompt or text, items


def pick_balanced_by_category(questions: list[dict], total_count: int) -> list[dict]:
    by_category: dict[str, list[dict]] = {}
    for q in questions:
        by_category.setdefault(q["categoryId"], []).append(q)

    pools = [
        {"categoryId": cid, "remaining": shuffle(items)}
        for cid, items in shuffle(list(by_category.items()))
    ]
    if not pools:
        return []

    target = min(total_count, len(questions))
    base = target // len(pools)
    remainder = target % len(pools)
    quotas: dict[str, int] = {}

    for pool in pools:
        want = base + (1 if remainder > 0 else 0)
        if remainder > 0:
            remainder -= 1
        quotas[pool["categoryId"]] = min(want, len(pool["remaining"]))

    selected_count = sum(quotas.values())
    while selected_count < target:
        donors = [
            p
            for p in pools
            if quotas.get(p["categoryId"], 0) < len(p["remaining"])
        ]
        if not donors:
            break
        for donor in shuffle(donors):
            if selected_count >= target:
                break
            quotas[donor["categoryId"]] = quotas.get(donor["categoryId"], 0) + 1
            selected_count += 1

    selected: list[dict] = []
    for pool in pools:
        quota = quotas.get(pool["categoryId"], 0)
        if quota > 0:
            selected.extend(pool["remaining"][:quota])
    return shuffle(selected)


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_active_attempt(user_id: str):
    return fetch_one(
        """
        SELECT * FROM Attempt
        WHERE userId = ? AND status = 'in_progress'
        ORDER BY startedAt DESC
        LIMIT 1
        """,
        (user_id,),
    )


def attempt_ends_at(attempt) -> datetime:
    started = parse_dt(attempt["startedAt"])
    return started + timedelta(minutes=int(attempt["timeLimitMinutes"]))


def is_time_expired(attempt) -> bool:
    return datetime.now(timezone.utc) >= attempt_ends_at(attempt)


def topic_categories() -> list:
    return fetch_all(
        """
        SELECT c.id, c.name, c.description, COUNT(q.id) AS questionCount
        FROM QuestionCategory c
        JOIN Question q ON q.categoryId = c.id AND q.isActive = 1
        GROUP BY c.id
        ORDER BY c.name
        """
    )


def topic_count() -> int:
    row = fetch_one(
        """
        SELECT COUNT(DISTINCT c.id) AS c
        FROM QuestionCategory c
        JOIN Question q ON q.categoryId = c.id AND q.isActive = 1
        """
    )
    return int(row["c"]) if row else 0


def recent_attempts(user_id: str, limit: int = 3) -> list:
    return fetch_all(
        """
        SELECT a.*,
          (
            SELECT c.name FROM AttemptQuestion aq
            JOIN Question q ON q.id = aq.questionId
            JOIN QuestionCategory c ON c.id = q.categoryId
            WHERE aq.attemptId = a.id
            ORDER BY aq.orderIndex
            LIMIT 1
          ) AS topicName
        FROM Attempt a
        WHERE a.userId = ? AND a.status = 'submitted'
        ORDER BY a.submittedAt DESC
        LIMIT ?
        """,
        (user_id, limit),
    )


def attempt_title(item) -> str:
    mode = (
        "학습"
        if item["revealMode"] == "immediate"
        else ("" if item["kind"] == "mock" else "시험")
    )
    if item["kind"] == "mock":
        return "실전 모의고사"
    if item["kind"] == "all":
        return f"전체 문항 · {mode}" if mode else "전체 문항"
    topic = item["topicName"] if "topicName" in item.keys() else None
    if topic:
        return f"{topic} · {mode}" if mode else topic
    return "주제별 문제"


def start_exam(
    user_id: str,
    *,
    kind: str = "mock",
    category_id: str | None = None,
    reveal_mode: str = "end",
    force_new: bool = True,
    retry_wrong_from: str | None = None,
) -> tuple[str | None, str | None]:
    if kind not in {"topic", "mock", "all"}:
        kind = "topic" if category_id else "mock"
    if kind not in {"topic", "all"} or reveal_mode != "immediate":
        reveal_mode = "end"

    existing = get_active_attempt(user_id)
    if existing and not force_new:
        return existing["id"], None

    if existing and force_new:
        # 삭제하지 않고 제출 처리해 대시보드 '최근 학습 현황'에 남긴다.
        submit_exam(existing["id"], user_id)

    selected: list[dict] = []

    if retry_wrong_from:
        source = fetch_one(
            """
            SELECT * FROM Attempt
            WHERE id = ? AND userId = ? AND status = 'submitted'
            """,
            (retry_wrong_from, user_id),
        )
        if not source:
            return None, "다시 풀 틀린 문제가 없습니다."
        wrongs = fetch_all(
            """
            SELECT questionId AS id FROM AttemptQuestion
            WHERE attemptId = ? AND (isCorrect = 0 OR isCorrect IS NULL)
              AND userAnswer IS NOT NULL
            """,
            (retry_wrong_from,),
        )
        # 미응답도 틀린 것으로 포함 (학습 종료 후 재학습용)
        if not wrongs:
            wrongs = fetch_all(
                """
                SELECT questionId AS id FROM AttemptQuestion
                WHERE attemptId = ? AND (isCorrect = 0 OR isCorrect IS NULL)
                """,
                (retry_wrong_from,),
            )
        if not wrongs:
            return None, "다시 풀 틀린 문제가 없습니다."
        selected = shuffle([{"id": w["id"]} for w in wrongs])
        # 학습 모드에서 틀린 문제 다시 풀기도 즉시 해설 유지
        if source["revealMode"] == "immediate":
            reveal_mode = "immediate"
        else:
            reveal_mode = "end"
    else:
        if kind == "topic" and category_id:
            all_q = fetch_all(
                """
                SELECT id, categoryId, sourceOrder FROM Question
                WHERE isActive = 1 AND categoryId = ?
                ORDER BY sourceOrder ASC
                """,
                (category_id,),
            )
        else:
            all_q = fetch_all(
                """
                SELECT id, categoryId, sourceOrder FROM Question
                WHERE isActive = 1
                """
            )
        all_q = [dict(r) for r in all_q]
        if not all_q:
            return None, "출제할 문제가 없습니다."

        if kind == "all":
            selected = shuffle(all_q)
        elif kind == "topic":
            selected = all_q if reveal_mode == "immediate" else shuffle(all_q)
        else:
            selected = pick_balanced_by_category(all_q, EXAM_QUESTION_COUNT)
            if len(selected) < min(EXAM_QUESTION_COUNT, len(all_q)):
                return None, "실전 모의고사 문항을 구성하지 못했습니다. 다시 시도해 주세요."

    time_limit = calc_time_limit_minutes(len(selected))
    attempt_id = new_id()
    execute(
        """
        INSERT INTO Attempt
        (id, userId, status, revealMode, kind, score, totalCount, startedAt, submittedAt, timeLimitMinutes)
        VALUES (?, ?, 'in_progress', ?, ?, NULL, ?, ?, NULL, ?)
        """,
        (
            attempt_id,
            user_id,
            reveal_mode,
            kind,
            len(selected),
            now_iso(),
            time_limit,
        ),
    )
    executemany(
        """
        INSERT INTO AttemptQuestion
        (id, attemptId, questionId, orderIndex, userAnswer, isCorrect)
        VALUES (?, ?, ?, ?, NULL, NULL)
        """,
        [
            (new_id(), attempt_id, q["id"], idx + 1)
            for idx, q in enumerate(selected)
        ],
    )
    return attempt_id, None


def load_exam(attempt_id: str, user_id: str):
    attempt = fetch_one(
        "SELECT * FROM Attempt WHERE id = ? AND userId = ?",
        (attempt_id, user_id),
    )
    if not attempt:
        return None, []

    rows = fetch_all(
        """
        SELECT aq.*, q.stem, q.choicesJson, q.answerIndex, q.explanation, q.source,
               q.imagePath, q.categoryId, c.name AS categoryName
        FROM AttemptQuestion aq
        JOIN Question q ON q.id = aq.questionId
        JOIN QuestionCategory c ON c.id = q.categoryId
        WHERE aq.attemptId = ?
        ORDER BY aq.orderIndex ASC
        """,
        (attempt_id,),
    )
    return attempt, [dict(r) for r in rows]


def save_answer(
    attempt_id: str,
    user_id: str,
    attempt_question_id: str,
    answer_index: int,
) -> tuple[bool, str, dict | None]:
    attempt = fetch_one(
        "SELECT * FROM Attempt WHERE id = ? AND userId = ?",
        (attempt_id, user_id),
    )
    if not attempt or attempt["status"] != "in_progress":
        return False, "진행 중인 시험이 아닙니다.", None
    if is_time_expired(attempt):
        return False, "제한 시간이 종료되었습니다.", None

    aq = fetch_one(
        """
        SELECT aq.*, q.answerIndex, q.explanation, q.source
        FROM AttemptQuestion aq
        JOIN Question q ON q.id = aq.questionId
        WHERE aq.id = ? AND aq.attemptId = ?
        """,
        (attempt_question_id, attempt_id),
    )
    if not aq:
        return False, "문항을 찾을 수 없습니다.", None

    if attempt["revealMode"] == "immediate" and aq["userAnswer"] is not None:
        return False, "이미 답한 문항입니다.", None

    execute(
        "UPDATE AttemptQuestion SET userAnswer = ? WHERE id = ?",
        (answer_index, attempt_question_id),
    )

    feedback = None
    if attempt["revealMode"] == "immediate":
        correct = int(aq["answerIndex"])
        feedback = {
            "isCorrect": answer_index == correct,
            "correctIndex": correct,
            "explanation": aq["explanation"],
            "source": aq["source"],
        }
    return True, "저장됨", feedback


def submit_exam(attempt_id: str, user_id: str) -> tuple[bool, str]:
    attempt = fetch_one(
        "SELECT * FROM Attempt WHERE id = ? AND userId = ?",
        (attempt_id, user_id),
    )
    if not attempt:
        return False, "시험을 찾을 수 없습니다."
    if attempt["status"] == "submitted":
        return True, "이미 제출된 시험입니다."

    rows = fetch_all(
        """
        SELECT aq.id, aq.userAnswer, q.answerIndex
        FROM AttemptQuestion aq
        JOIN Question q ON q.id = aq.questionId
        WHERE aq.attemptId = ?
        """,
        (attempt_id,),
    )
    score = 0
    for row in rows:
        is_correct = (
            row["userAnswer"] is not None
            and int(row["userAnswer"]) == int(row["answerIndex"])
        )
        if is_correct:
            score += 1
        execute(
            "UPDATE AttemptQuestion SET isCorrect = ? WHERE id = ?",
            (1 if is_correct else 0, row["id"]),
        )

    execute(
        """
        UPDATE Attempt
        SET status = 'submitted', score = ?, submittedAt = ?
        WHERE id = ?
        """,
        (score, now_iso(), attempt_id),
    )
    return True, "제출 완료"


def image_path_for(image_rel: str | None) -> Path | None:
    if not image_rel:
        return None
    path = (STORAGE_IMAGES / image_rel).resolve()
    if not str(path).startswith(str(STORAGE_IMAGES.resolve())):
        return None
    return path if path.is_file() else None
