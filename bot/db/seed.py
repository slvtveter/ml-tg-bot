"""Загрузка тем и вопросов из data/questions.yaml в БД.

Идемпотентна: темы матчатся по slug, вопросы — по uid. Повторный запуск
обновляет тексты и добавляет новое, но не плодит дубли. Запускается на старте
бота и доступна отдельной командой:  python -m bot.db.seed
"""
from __future__ import annotations

import asyncio

import yaml

from bot.config import BASE_DIR
from bot.db import backend
from bot.db.database import init_db

QUESTIONS_FILE = BASE_DIR / "data" / "questions.yaml"


async def _upsert_topic(slug: str, name: str, parent_id, sort: int) -> int:
    await backend.execute(
        """INSERT INTO topics (slug, name, parent_id, sort) VALUES (?, ?, ?, ?)
           ON CONFLICT(slug) DO UPDATE SET name=excluded.name,
                                           parent_id=excluded.parent_id,
                                           sort=excluded.sort""",
        (slug, name, parent_id, sort),
    )
    row = await backend.fetchone("SELECT id FROM topics WHERE slug=?", (slug,))
    return row["id"]


async def seed() -> dict[str, int]:
    await init_db()
    data = yaml.safe_load(QUESTIONS_FILE.read_text(encoding="utf-8"))

    # Темы: дерево раздел -> children. Сохраняем slug -> id для вопросов.
    slug_to_id: dict[str, int] = {}
    sort = 0
    for section in data.get("topics", []):
        parent_id = await _upsert_topic(section["slug"], section["name"], None, sort)
        slug_to_id[section["slug"]] = parent_id
        sort += 1
        for child in section.get("children", []):
            child_id = await _upsert_topic(child["slug"], child["name"], parent_id, sort)
            slug_to_id[child["slug"]] = child_id
            sort += 1

    # Вопросы.
    n_questions = 0
    for i, q in enumerate(data.get("questions", [])):
        topic_slug = q["topic"]
        if topic_slug not in slug_to_id:
            raise ValueError(f"Вопрос ссылается на неизвестную тему: {topic_slug}")
        uid = q.get("uid") or f"{topic_slug}_{i}"
        await backend.execute(
            """INSERT INTO questions (topic_id, question, answer, difficulty, source, uid)
               VALUES (?, ?, ?, ?, 'seed', ?)
               ON CONFLICT(uid) DO UPDATE SET question=excluded.question,
                                              answer=excluded.answer,
                                              difficulty=excluded.difficulty,
                                              topic_id=excluded.topic_id""",
            (slug_to_id[topic_slug], q["question"].strip(), q["answer"].strip(),
             q.get("difficulty", 2), uid),
        )
        n_questions += 1

    return {"topics": len(slug_to_id), "questions": n_questions}


async def _main() -> None:
    result = await seed()
    print(f"Загружено: тем {result['topics']}, вопросов {result['questions']}")
    await backend.close()


if __name__ == "__main__":
    asyncio.run(_main())
