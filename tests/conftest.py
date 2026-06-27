"""Общие фикстуры: изолированная SQLite-база на каждый тест (без Turso)."""
import os

import pytest_asyncio

# До импорта пакета bot — гарантируем локальный SQLite, не Turso.
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""

from bot.config import settings  # noqa: E402
from bot.db import backend, database as db  # noqa: E402


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    """Чистая БД-файл на тест; сбрасывает singleton backend до и после."""
    settings.turso_database_url = ""
    settings.db_path = str(tmp_path / "test.db")
    settings.daily_new_limit = 15  # дефолт; тесты лимита переопределяют локально
    backend._backend = None
    await db.init_db()
    yield
    await backend.close()
    backend._backend = None


@pytest_asyncio.fixture
async def populated(fresh_db):
    """Минимальный roadmap: 1 уровень -> 1 тема -> 3 вопроса."""
    await backend.execute(
        "INSERT INTO topics (slug, name, parent_id, sort) VALUES (?, ?, ?, ?)",
        ("L0", "Уровень 0", None, 0),
    )
    lvl = (await backend.fetchone("SELECT id FROM topics WHERE slug='L0'"))["id"]
    await backend.execute(
        "INSERT INTO topics (slug, name, parent_id, sort) VALUES (?, ?, ?, ?)",
        ("T", "Тема", lvl, 1),
    )
    top = (await backend.fetchone("SELECT id FROM topics WHERE slug='T'"))["id"]
    for i in range(3):
        await backend.execute(
            "INSERT INTO questions (topic_id, question, answer, difficulty, source, uid) "
            "VALUES (?, ?, ?, ?, 'seed', ?)",
            (top, f"Вопрос {i}", f"Ответ {i}", 2, f"u{i}"),
        )
    return {"level": lvl, "topic": top}
