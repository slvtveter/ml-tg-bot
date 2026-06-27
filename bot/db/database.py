"""Слой доступа к данным. SQL поверх backend (локальный SQLite или Turso).

Схема (с заделом на будущие фазы — roadmap-дерево тем уже здесь):

  topics    — темы roadmap. parent_id даёт дерево (раздел -> подтема).
  questions — вопросы, привязаны к теме. source: seed | llm | user.
  cards     — состояние интервального повторения для пары (user, question).
  reviews   — лог всех ответов (для статистики и графиков прогресса).
  users     — кто пользуется ботом + подписка на «вопрос дня».
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.db import backend
from bot.services.spaced_repetition import CardState, new_card

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id        INTEGER PRIMARY KEY,
    slug      TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    parent_id INTEGER REFERENCES topics(id),
    sort      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS questions (
    id         INTEGER PRIMARY KEY,
    topic_id   INTEGER NOT NULL REFERENCES topics(id),
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    difficulty INTEGER NOT NULL DEFAULT 2,
    source     TEXT NOT NULL DEFAULT 'seed',
    uid        TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    username   TEXT,
    subscribed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    user_id     INTEGER NOT NULL,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    ease_factor REAL NOT NULL,
    interval    INTEGER NOT NULL,
    repetitions INTEGER NOT NULL,
    due         TEXT NOT NULL,
    PRIMARY KEY (user_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(user_id, due);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    rating      TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
)
"""


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _today_start_utc_iso() -> str:
    """Начало текущего дня в TZ пользователя, в UTC ISO (для дневных лимитов)."""
    tz = ZoneInfo(settings.tz)
    start_local = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(dt.timezone.utc).isoformat()


async def new_introduced_today(user_id: int) -> int:
    """Сколько НОВЫХ карточек пользователь впервые увидел сегодня.

    «Новая впервые сегодня» = самый ранний review карточки приходится на сегодня.
    """
    row = await backend.fetchone(
        """SELECT COUNT(*) c FROM (
               SELECT question_id, MIN(reviewed_at) AS first_seen
               FROM reviews WHERE user_id=?
               GROUP BY question_id
               HAVING first_seen >= ?
           )""",
        (user_id, _today_start_utc_iso()),
    )
    return row["c"] if row else 0


async def init_db() -> None:
    """Создать таблицы, если их ещё нет."""
    await backend.executescript(SCHEMA)


# ---------- users ----------

async def upsert_user(user_id: int, chat_id: int, username: Optional[str]) -> None:
    await backend.execute(
        """INSERT INTO users (user_id, chat_id, username, subscribed, created_at)
           VALUES (?, ?, ?, 1, ?)
           ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id,
                                              username=excluded.username""",
        (user_id, chat_id, username, _utc_now_iso()),
    )


async def set_subscription(user_id: int, subscribed: bool) -> None:
    await backend.execute(
        "UPDATE users SET subscribed=? WHERE user_id=?",
        (1 if subscribed else 0, user_id),
    )


async def get_subscribers() -> list[dict]:
    return await backend.fetchall("SELECT * FROM users WHERE subscribed=1")


# ---------- cards / questions ----------

async def ensure_cards(user_id: int) -> None:
    """Создать карточки для всех вопросов, у которых их ещё нет.

    Новые карточки сразу due (готовы к показу). Вызывается на /start и после
    добавления новых вопросов, чтобы пул всегда был полным.
    """
    state = new_card()
    await backend.execute(
        """INSERT OR IGNORE INTO cards
               (user_id, question_id, ease_factor, interval, repetitions, due)
           SELECT ?, q.id, ?, ?, ?, ?
           FROM questions q""",
        (user_id, state.ease_factor, state.interval, state.repetitions,
         state.due.isoformat()),
    )


async def pick_due_card(
    user_id: int, exclude_id: Optional[int] = None, level_id: Optional[int] = None,
    respect_new_limit: bool = True,
) -> Optional[dict[str, Any]]:
    """Следующая карточка с дозированным вводом новых (настоящий spaced repetition).

    Приоритет:
      1) К ПОВТОРЕНИЮ — уже виденные карточки, у которых наступил срок (due<=now);
         их пропускать нельзя, иначе теряется смысл интервального повторения.
      2) НОВЫЕ — но не больше daily_new_limit в день (в обычном /quiz). При фокусе
         на уровне (level_id задан) лимит не применяется — это осознанная
         проработка темы (например, перед собесом).

    Возвращает None, когда на сегодня всё: нет due-повторов и дневной лимит новых
    исчерпан. exclude_id не даёт показать тот же вопрос дважды подряд.
    """
    now = _utc_now_iso()
    conds = ["c.user_id = ?"]
    params: list[Any] = [user_id]
    if exclude_id is not None:
        conds.append("q.id != ?")
        params.append(exclude_id)
    if level_id is not None:
        conds.append("t.parent_id = ?")
        params.append(level_id)
    where = " AND ".join(conds)

    cols = ("q.id, q.question, q.answer, q.difficulty, t.name AS topic_name, "
            "c.ease_factor, c.interval, c.repetitions, c.due")
    frm = ("FROM cards c JOIN questions q ON q.id = c.question_id "
           "JOIN topics t ON t.id = q.topic_id")
    seen = ("EXISTS (SELECT 1 FROM reviews r "
            "WHERE r.user_id = c.user_id AND r.question_id = c.question_id)")

    # 1) Уже виденные и пора повторить.
    row = await backend.fetchone(
        f"SELECT {cols} {frm} WHERE {where} AND c.due <= ? AND {seen} "
        f"ORDER BY c.due ASC LIMIT 1",
        (*params, now),
    )
    if row is not None:
        return row

    # 2) Новые — с учётом дневного лимита (кроме фокуса на уровне).
    if respect_new_limit and level_id is None:
        if await new_introduced_today(user_id) >= settings.daily_new_limit:
            return None
    return await backend.fetchone(
        f"SELECT {cols} {frm} WHERE {where} AND NOT {seen} "
        f"ORDER BY t.sort, q.id LIMIT 1",
        params,
    )


async def list_levels() -> list[dict[str, Any]]:
    """Уровни roadmap (top-level темы) с числом вопросов в каждом."""
    return await backend.fetchall(
        """SELECT lvl.id, lvl.name, COUNT(q.id) AS total
           FROM topics lvl
           LEFT JOIN topics t ON t.parent_id = lvl.id
           LEFT JOIN questions q ON q.topic_id = t.id
           WHERE lvl.parent_id IS NULL
           GROUP BY lvl.id
           HAVING total > 0
           ORDER BY lvl.sort"""
    )


async def get_card_state(user_id: int, question_id: int) -> Optional[CardState]:
    row = await backend.fetchone(
        "SELECT ease_factor, interval, repetitions, due FROM cards "
        "WHERE user_id=? AND question_id=?",
        (user_id, question_id),
    )
    if row is None:
        return None
    return CardState(
        ease_factor=row["ease_factor"],
        interval=row["interval"],
        repetitions=row["repetitions"],
        due=dt.datetime.fromisoformat(row["due"]),
    )


async def save_review(user_id: int, question_id: int, rating: str, state: CardState) -> None:
    """Сохранить новое состояние карточки и записать ответ в лог reviews."""
    await backend.execute(
        """UPDATE cards SET ease_factor=?, interval=?, repetitions=?, due=?
           WHERE user_id=? AND question_id=?""",
        (state.ease_factor, state.interval, state.repetitions,
         state.due.isoformat(), user_id, question_id),
    )
    await backend.execute(
        """INSERT INTO reviews (user_id, question_id, rating, reviewed_at)
           VALUES (?, ?, ?, ?)""",
        (user_id, question_id, rating, _utc_now_iso()),
    )


async def get_question(question_id: int) -> Optional[dict[str, Any]]:
    return await backend.fetchone(
        """SELECT q.id, q.question, q.answer, q.difficulty, t.name AS topic_name
           FROM questions q JOIN topics t ON t.id = q.topic_id
           WHERE q.id=?""",
        (question_id,),
    )


_SEEN = ("EXISTS (SELECT 1 FROM reviews r "
         "WHERE r.user_id = cards.user_id AND r.question_id = cards.question_id)")


async def count_today_workload(user_id: int) -> dict[str, int]:
    """Сколько карточек реально к работе сегодня: due-повторы + остаток новых."""
    now = _utc_now_iso()
    due_review = (await backend.fetchone(
        f"SELECT COUNT(*) c FROM cards WHERE user_id=? AND due<=? AND {_SEEN}",
        (user_id, now),
    ))["c"]
    new_total = (await backend.fetchone(
        f"SELECT COUNT(*) c FROM cards WHERE user_id=? AND NOT {_SEEN}", (user_id,),
    ))["c"]
    remaining = max(0, settings.daily_new_limit - await new_introduced_today(user_id))
    new_today = min(new_total, remaining)
    return {
        "due_review": due_review,
        "new_today": new_today,
        "new_total": new_total,
        "total": due_review + new_today,
    }


# ---------- статистика ----------

async def get_stats(user_id: int) -> dict[str, Any]:
    total = (await backend.fetchone("SELECT COUNT(*) c FROM questions"))["c"]
    seen = (await backend.fetchone(
        f"SELECT COUNT(*) c FROM cards WHERE user_id=? AND {_SEEN}", (user_id,)
    ))["c"]
    total_reviews = (await backend.fetchone(
        "SELECT COUNT(*) c FROM reviews WHERE user_id=?", (user_id,)
    ))["c"]
    workload = await count_today_workload(user_id)

    # Освоение по уровням: доля вопросов с repetitions>=2 (вышли на нормальный
    # интервал) внутри каждого уровня roadmap (родитель темы).
    by_level = await backend.fetchall(
        """SELECT lvl.name AS level,
                  COUNT(*) AS total,
                  SUM(CASE WHEN c.repetitions>=2 THEN 1 ELSE 0 END) AS learned
           FROM questions q
           JOIN topics t ON t.id = q.topic_id
           JOIN topics lvl ON lvl.id = t.parent_id
           LEFT JOIN cards c ON c.question_id = q.id AND c.user_id = ?
           GROUP BY lvl.id
           ORDER BY lvl.sort""",
        (user_id,),
    )
    return {
        "total": total,
        "seen": seen,
        "new_total": workload["new_total"],
        "due_review": workload["due_review"],
        "new_today": workload["new_today"],
        "total_reviews": total_reviews,
        "by_level": by_level,
    }
