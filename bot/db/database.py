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
    user_id: int, exclude_id: Optional[int] = None
) -> Optional[dict[str, Any]]:
    """Вернуть наиболее «просроченную» карточку (due <= сейчас).

    Если due-карточек нет — берём самую раннюю предстоящую, чтобы тренировка
    не упиралась в пустоту. exclude_id не даёт показать тот же вопрос дважды
    подряд.
    """
    now = _utc_now_iso()
    params: list[Any] = [user_id]
    exclude_sql = ""
    if exclude_id is not None:
        exclude_sql = "AND q.id != ?"
        params.append(exclude_id)

    base = """
        SELECT q.id, q.question, q.answer, q.difficulty, t.name AS topic_name,
               c.ease_factor, c.interval, c.repetitions, c.due
        FROM cards c
        JOIN questions q ON q.id = c.question_id
        JOIN topics t ON t.id = q.topic_id
        WHERE c.user_id = ? {extra}
        ORDER BY c.due ASC
        LIMIT 1
    """
    # Сначала — то, что уже пора повторять.
    row = await backend.fetchone(
        base.format(extra=f"{exclude_sql} AND c.due <= ?"), (*params, now)
    )
    if row is None:
        # Ничего не пора — берём ближайшее будущее (тренировка «наперёд»).
        row = await backend.fetchone(base.format(extra=exclude_sql), params)
    return row


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


# ---------- статистика ----------

async def get_stats(user_id: int) -> dict[str, Any]:
    now = _utc_now_iso()
    total = (await backend.fetchone("SELECT COUNT(*) c FROM questions"))["c"]
    reviewed = (await backend.fetchone(
        "SELECT COUNT(*) c FROM cards WHERE user_id=? AND repetitions>0", (user_id,)
    ))["c"]
    due = (await backend.fetchone(
        "SELECT COUNT(*) c FROM cards WHERE user_id=? AND due<=?", (user_id, now)
    ))["c"]
    total_reviews = (await backend.fetchone(
        "SELECT COUNT(*) c FROM reviews WHERE user_id=?", (user_id,)
    ))["c"]

    # Освоение по темам: доля вопросов с repetitions>=2 (вышли на нормальный
    # интервал) внутри каждой темы.
    by_topic = await backend.fetchall(
        """SELECT t.name AS topic,
                  COUNT(*) AS total,
                  SUM(CASE WHEN c.repetitions>=2 THEN 1 ELSE 0 END) AS learned
           FROM questions q
           JOIN topics t ON t.id = q.topic_id
           LEFT JOIN cards c ON c.question_id = q.id AND c.user_id = ?
           GROUP BY t.id
           ORDER BY t.sort, t.name""",
        (user_id,),
    )
    return {
        "total": total,
        "reviewed": reviewed,
        "due": due,
        "total_reviews": total_reviews,
        "by_topic": by_topic,
    }
