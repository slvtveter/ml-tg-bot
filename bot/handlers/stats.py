"""Команда /stats — прогресс и покрытие по темам."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db import database as db

router = Router()


def _bar(learned: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "—"
    filled = round(width * learned / total)
    return "█" * filled + "░" * (width - filled)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await db.ensure_cards(message.from_user.id)
    s = await db.get_stats(message.from_user.id)

    lines = [
        "📊 <b>Твой прогресс</b>\n",
        f"Изучено карточек: <b>{s['seen']}</b> из {s['total']}",
        f"Осталось новых: <b>{s['new_total']}</b>",
        f"\n<b>Сегодня к работе:</b> {s['due_review']} к повторению + "
        f"{s['new_today']} новых",
        f"Всего ответов дано: <b>{s['total_reviews']}</b>",
        "\n<b>Освоение по уровням</b> (вопросы, вышедшие на повтор):",
    ]
    for lvl in s["by_level"]:
        learned = lvl["learned"] or 0
        total = lvl["total"] or 0
        lines.append(f"{_bar(learned, total)}  {lvl['level']} — {learned}/{total}")

    lines.append("\n💡 «Освоено» = вопрос успешно вспомнил ≥2 раз подряд.")
    lines.append("Тренировка по уровню: /levels")
    await message.answer("\n".join(lines))
