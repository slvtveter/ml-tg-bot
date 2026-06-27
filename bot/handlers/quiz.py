"""Тренировка: вопрос -> ответ -> самооценка -> spaced repetition."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import keyboards as kb
from bot.db import database as db
from bot.services import llm_grader
from bot.services import spaced_repetition as sr

router = Router()

RATING_LABEL = {"again": "❌ Не знал", "partial": "🤔 Частично", "known": "✅ Знал"}
VERDICT_EMOJI = {"again": "❌", "partial": "🤔", "known": "✅"}


class AnswerStates(StatesGroup):
    waiting = State()  # ждём текстовый ответ пользователя на конкретный вопрос

# Текущий выбранный уровень на пользователя (None = вся база, spaced repetition).
# In-memory: сбрасывается при рестарте — это ок, прогресс хранится в БД.
_user_level: dict[int, Optional[int]] = {}


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
    """Достать следующую карточку (с учётом выбранного уровня) и отправить."""
    level_id = _user_level.get(user_id)
    card = await db.pick_due_card(user_id, exclude_id=exclude_id, level_id=level_id)
    if card is None:
        if level_id is None:
            await message.answer(
                "🎉 <b>На сегодня всё!</b>\n\nПовторения закрыты, дневная порция "
                "новых карточек введена. Именно так работает spaced repetition — "
                "по чуть-чуть каждый день, и память держится.\n\n"
                "Хочешь ещё проработать тему — выбери уровень: /levels"
            )
        else:
            await message.answer(
                "✅ По этому уровню карточек к показу сейчас нет.\n"
                "Другой уровень: /levels · вся база: /quiz"
            )
        return
    await message.answer(
        _question_text(card),
        reply_markup=kb.show_answer_kb(card["id"], allow_text=llm_grader.is_enabled()),
    )


@router.message(Command("quiz"))
async def cmd_quiz(message: Message, state: FSMContext) -> None:
    # /quiz — вся база (чистый spaced repetition). Для фокуса по уровню — /levels.
    await state.clear()
    _user_level[message.from_user.id] = None
    await db.ensure_cards(message.from_user.id)
    await send_next_card(message, message.from_user.id)


@router.message(Command("levels"))
async def cmd_levels(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.ensure_cards(message.from_user.id)
    levels = await db.list_levels()
    if not levels:
        await message.answer("Уровней пока нет — банк вопросов пуст.")
        return
    await message.answer(
        "📂 <b>Выбери уровень roadmap</b> для тренировки\n"
        "(или гоняй всю базу — алгоритм сам подберёт, что повторить):",
        reply_markup=kb.levels_kb(levels),
    )


@router.callback_query(F.data.startswith("level:"))
async def on_pick_level(callback: CallbackQuery) -> None:
    value = callback.data.split(":")[1]
    _user_level[callback.from_user.id] = None if value == "all" else int(value)
    await callback.answer("Поехали!")
    await send_next_card(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("show:"))
async def on_show_answer(callback: CallbackQuery) -> None:
    question_id = int(callback.data.split(":")[1])
    card = await db.get_question(question_id)
    if card is None:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    await callback.message.edit_text(_answer_text(card), reply_markup=kb.rating_kb(question_id))
    await callback.answer()


@router.callback_query(F.data.startswith("answer:"))
async def on_answer_request(callback: CallbackQuery, state: FSMContext) -> None:
    question_id = int(callback.data.split(":")[1])
    await state.set_state(AnswerStates.waiting)
    await state.update_data(question_id=question_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✍️ Напиши свой ответ одним сообщением — проверю и разберу."
    )
    await callback.answer()


@router.message(AnswerStates.waiting, F.text, ~F.text.startswith("/"))
async def on_text_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question_id = data.get("question_id")
    await state.clear()

    card = await db.get_question(question_id)
    if card is None:
        await message.answer("Вопрос не найден. Жми /quiz")
        return

    thinking = await message.answer("⏳ Проверяю ответ…")
    result = await llm_grader.grade(card["question"], card["answer"], message.text)

    if result is None:
        # LLM недоступна (нет ключа / все модели упали) — ручной режим.
        await thinking.edit_text(
            "⚠️ Не смог проверить ответ автоматически. Вот эталон — оцени себя сам:\n\n"
            + _answer_text(card),
            reply_markup=kb.rating_kb(question_id),
        )
        return

    verdict = result["verdict"]
    cs = await db.get_card_state(message.from_user.id, question_id)
    due_txt = ""
    if cs is not None:
        new_state = sr.review(cs, verdict)
        await db.save_review(message.from_user.id, question_id, verdict, new_state)
        due_txt = _human_due(new_state)

    text = (
        f"{VERDICT_EMOJI[verdict]} <b>Оценка ответа</b>\n\n"
        f"{html.escape(result['feedback'])}\n\n"
        f"<b>Эталон:</b>\n{html.escape(card['answer'])}\n\n"
        f"<i>Записал как «{RATING_LABEL[verdict]}» · повтор {due_txt}</i>"
    )
    await thinking.edit_text(text)
    await send_next_card(message, message.from_user.id, exclude_id=question_id)


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
