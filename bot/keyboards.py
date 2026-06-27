"""Inline-клавиатуры для тренировки."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def show_answer_kb(question_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👀 Показать ответ", callback_data=f"show:{question_id}")
    return kb.as_markup()


def rating_kb(question_id: int) -> InlineKeyboardMarkup:
    """Три кнопки самооценки -> в spaced repetition (again/partial/known)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Не знал", callback_data=f"rate:{question_id}:again")
    kb.button(text="🤔 Частично", callback_data=f"rate:{question_id}:partial")
    kb.button(text="✅ Знал", callback_data=f"rate:{question_id}:known")
    kb.adjust(3)
    return kb.as_markup()


def next_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➡️ Следующий вопрос", callback_data="next")
    return kb.as_markup()


def levels_kb(levels: list[dict]) -> InlineKeyboardMarkup:
    """Список уровней roadmap + режим «вся база» (spaced repetition)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🎲 Вся база (spaced repetition)", callback_data="level:all")
    for lvl in levels:
        kb.button(
            text=f"{lvl['name']} · {lvl['total']}",
            callback_data=f"level:{lvl['id']}",
        )
    kb.adjust(1)
    return kb.as_markup()
