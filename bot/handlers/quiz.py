"""Тренировка: вопрос -> ответ -> самооценка -> spaced repetition."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot import keyboards as kb
from bot.db import database as db
from bot.services import spaced_repetition as sr

router = Router()

RATING_LABEL = {"again": "❌ Не знал", "partial": "🤔 Частично", "known": "✅ Знал"}


def _stars(difficulty: int) -> str:
    return "⭐" * max(1, min(3, difficulty))


def _question_text(card: dict[str, Any]) -> str:
    return (
        f"📚 <b>{html.escape(card['topic_name'])}</b>  {_stars(card['difficulty'])}\n\n"
        f"{html.escape(card['question'])}"
    )


def _answer_text(card: dict[str, Any]) -> str:
    return (
        f"📚 <b>{html.escape(card['topic_name'])}</b>  {_stars(card['difficulty'])}\n\n"
        f"<b>Вопрос:</b>\n{html.escape(card['question'])}\n\n"
        f"<b>Ответ:</b>\n{html.escape(card['answer'])}\n\n"
        f"<i>Насколько хорошо ты знал?</i>"
    )


def _human_due(state: sr.CardState) -> str:
    """Человекочитаемо: через сколько повторим."""
    delta = state.due - datetime.now(timezone.utc)
    days = delta.days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    return f"через {days} дн."


async def send_next_card(message: Message, user_id: int, exclude_id: Optional[int] = None) -> None:
    """Достать следующую карточку и отправить новым сообщением."""
    card = await db.pick_due_card(user_id, exclude_id=exclude_id)
    if card is None:
        await message.answer(
            "В банке пока нет вопросов 🤷 Добавь их в data/questions.yaml "
            "и перезапусти бота."
        )
        return
    await message.answer(_question_text(card), reply_markup=kb.show_answer_kb(card["id"]))


@router.message(Command("quiz"))
async def cmd_quiz(message: Message) -> None:
    await db.ensure_cards(message.from_user.id)
    await send_next_card(message, message.from_user.id)


@router.callback_query(F.data.startswith("show:"))
async def on_show_answer(callback: CallbackQuery) -> None:
    question_id = int(callback.data.split(":")[1])
    card = await db.get_question(question_id)
    if card is None:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    await callback.message.edit_text(_answer_text(card), reply_markup=kb.rating_kb(question_id))
    await callback.answer()


@router.callback_query(F.data.startswith("rate:"))
async def on_rate(callback: CallbackQuery) -> None:
    _, qid_str, rating = callback.data.split(":")
    question_id = int(qid_str)
    user_id = callback.from_user.id

    state = await db.get_card_state(user_id, question_id)
    if state is None:
        await callback.answer("Карточка не найдена", show_alert=True)
        return

    new_state = sr.review(state, rating)
    await db.save_review(user_id, question_id, rating, new_state)

    # Фиксируем оценку на сообщении (убираем кнопки) и подсказываем интервал.
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"{RATING_LABEL[rating]} · повтор {_human_due(new_state)}")

    # Сразу следующий вопрос (тот же не показываем).
    await send_next_card(callback.message, user_id, exclude_id=question_id)


@router.callback_query(F.data == "next")
async def on_next(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_next_card(callback.message, callback.from_user.id)
