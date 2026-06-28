"""Тренировка: вопрос -> ответ -> самооценка -> spaced repetition."""
from __future__ import annotations

import html
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
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

# Сессионное состояние на пользователя (in-memory, сбрасывается при рестарте —
# это ок, прогресс хранится в БД).
_user_level: dict[int, Optional[int]] = {}        # фильтр по уровню (None = вся база)
_user_difficulty: dict[int, Optional[int]] = {}   # фильтр по сложности (None = любая)
_recent: dict[int, deque] = {}                    # недавно показанные id (анти-повтор)
_current_card: dict[int, int] = {}                # последняя показанная карточка (для чата)
_chat_history: dict[int, deque] = {}              # история диалога с репетитором
RECENT_SIZE = 6
CHAT_HISTORY_SIZE = 12  # последних реплик диалога держим в контексте


def _remember(user_id: int, question_id: int) -> None:
    _recent.setdefault(user_id, deque(maxlen=RECENT_SIZE)).append(question_id)
    _current_card[user_id] = question_id


def _stars(difficulty: int) -> str:
    return "⭐" * max(1, min(3, difficulty))


_BULLET = re.compile(r"^([•\-*–]\s|\d+[.)]\s)")


def _reflow(text: str) -> str:
    """Склеить жёстко перенесённые строки в абзацы — Telegram перенесёт сам.

    Тексты в YAML вручную перенесены по ~76 символов, из-за чего на телефоне
    они рвутся криво. Здесь склеиваем строки одного абзаца в одну, сохраняя
    пустые строки (разделители абзацев), пункты списков и строки-формулы
    (идут после строки, заканчивающейся двоеточием).
    """
    out: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            out.append(buf)
            buf = ""

    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            flush()
            if out and out[-1] != "":
                out.append("")
            continue
        if not buf:
            buf = s
        elif _BULLET.match(s) or buf.rstrip().endswith(":"):
            flush()
            buf = s
        else:
            buf += " " + s
    flush()
    return "\n".join(out).strip()


_MD_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_MD_BULLET = re.compile(r"(?m)^[ \t]*[-*]\s+")


def md_to_html(text: str) -> str:
    """Конвертировать Markdown (вывод LLM) в безопасный Telegram-HTML.

    Telegram HTML устойчивее MarkdownV2 (не требует экранировать _ * . - и т.п.,
    которых полно в ML-тексте: feature_selection, lambda*sum). Поддержано:
    **жирный**, `код`, ```блоки```, # заголовки -> жирный, списки -> •, ссылки.
    Курсив через * НЕ трогаем — иначе ломается умножение и snake_case.
    """
    stash: list[str] = []

    def keep(snippet: str) -> str:
        stash.append(snippet)
        return f"\x00{len(stash) - 1}\x00"

    text = _MD_CODE_BLOCK.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = _MD_INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = html.escape(text)
    text = _MD_HEADER.sub(lambda m: f"<b>{m.group(1).strip()}</b>", text)
    text = _MD_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _MD_LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = _MD_BULLET.sub("• ", text)
    for i, snippet in enumerate(stash):
        text = text.replace(f"\x00{i}\x00", snippet)
    return text


def _question_text(card: dict[str, Any]) -> str:
    return (
        f"📚 <b>{html.escape(card['topic_name'])}</b>  {_stars(card['difficulty'])}\n\n"
        f"{html.escape(_reflow(card['question']))}"
    )


def _answer_text(card: dict[str, Any]) -> str:
    return (
        f"📚 <b>{html.escape(card['topic_name'])}</b>  {_stars(card['difficulty'])}\n\n"
        f"<b>Вопрос:</b>\n{html.escape(_reflow(card['question']))}\n\n"
        f"<b>Ответ:</b>\n{html.escape(_reflow(card['answer']))}\n\n"
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


async def send_next_card(message: Message, user_id: int) -> None:
    """Следующая карточка: случайный порядок + анти-повтор + фильтры уровня/сложности."""
    level_id = _user_level.get(user_id)
    diff = _user_difficulty.get(user_id)
    recents = list(_recent.get(user_id, ()))
    card = await db.pick_due_card(
        user_id, exclude_ids=recents, level_id=level_id, difficulty=diff
    )
    if card is None and recents:
        # Все подходящие карточки были показаны только что — снимаем анти-повтор.
        card = await db.pick_due_card(user_id, level_id=level_id, difficulty=diff)
    if card is None:
        if level_id is None and diff is None:
            await message.answer(
                "🎉 <b>На сегодня всё!</b>\n\nПовторения закрыты, дневная порция "
                "новых карточек введена. Именно так работает spaced repetition — "
                "по чуть-чуть каждый день, и память держится.\n\n"
                "Хочешь ещё — выбери уровень: /levels или сложность: /difficulty"
            )
        else:
            await message.answer(
                "✅ По выбранным фильтрам карточек сейчас нет.\n"
                "Сброс фильтров: /quiz · уровень: /levels · сложность: /difficulty"
            )
        return
    _remember(user_id, card["id"])
    await message.answer(
        _question_text(card),
        reply_markup=kb.show_answer_kb(card["id"], allow_text=llm_grader.is_enabled()),
    )


@router.message(Command("quiz"))
async def cmd_quiz(message: Message, state: FSMContext) -> None:
    # /quiz — вся база, без фильтров (чистый spaced repetition).
    await state.clear()
    _user_level[message.from_user.id] = None
    _user_difficulty[message.from_user.id] = None
    _chat_history.pop(message.from_user.id, None)  # свежая сессия — чистый контекст
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


@router.message(Command("difficulty"))
async def cmd_difficulty(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.ensure_cards(message.from_user.id)
    await message.answer(
        "🎚 <b>Выбери сложность</b> вопросов (фильтр совмещается с уровнем):",
        reply_markup=kb.difficulty_kb(),
    )


@router.callback_query(F.data.startswith("diff:"))
async def on_pick_difficulty(callback: CallbackQuery) -> None:
    value = callback.data.split(":")[1]
    _user_difficulty[callback.from_user.id] = None if value == "all" else int(value)
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
        f"{md_to_html(result['feedback'])}\n\n"
        f"<b>Эталон:</b>\n{html.escape(_reflow(card['answer']))}\n\n"
        f"<i>Записал как «{RATING_LABEL[verdict]}» · повтор {due_txt}</i>"
    )
    try:
        await thinking.edit_text(text)
    except TelegramBadRequest:
        await thinking.edit_text(text, parse_mode=None)
    await send_next_card(message, message.from_user.id)


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

    # Сразу следующий вопрос (анти-повтор не покажет тот же).
    await send_next_card(callback.message, user_id)


@router.callback_query(F.data == "next")
async def on_next(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_next_card(callback.message, callback.from_user.id)


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_tutor_chat(message: Message) -> None:
    """Свободный текст (вне режима ответа) = вопрос репетитору про текущую карточку.

    Держит контекст последних реплик, чтобы можно было доспрашивать и связывать
    вопросы между собой.
    """
    user_id = message.from_user.id
    if not llm_grader.is_enabled():
        await message.answer("💬 Чат-репетитор выключен (не задан GEMINI_API_KEY).")
        return
    qid = _current_card.get(user_id)
    if qid is None:
        await message.answer("Сначала возьми вопрос: /quiz — потом спрашивай по нему 💬")
        return
    card = await db.get_question(qid)
    if card is None:
        await message.answer("Не нашёл текущий вопрос. Жми /quiz")
        return

    history = _chat_history.setdefault(user_id, deque(maxlen=CHAT_HISTORY_SIZE))
    await message.bot.send_chat_action(message.chat.id, "typing")
    reply = await llm_grader.chat(
        llm_grader.tutor_system(card["question"], card["answer"]),
        list(history),
        message.text,
    )
    if reply is None:
        await message.answer("⚠️ Репетитор не ответил (Gemini недоступен). Попробуй ещё раз.")
        return
    history.append(("user", message.text))
    history.append(("model", reply))
    safe = reply[:4000]
    try:
        await message.answer(md_to_html(safe))
    except TelegramBadRequest:
        await message.answer(safe, parse_mode=None)
