"""Базовые команды: /start, /help, подписка на «вопрос дня»."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.db import database as db

router = Router()

HELP_TEXT = (
    "🤖 <b>ML Interview Trainer</b>\n"
    "Гоняю тебя по вопросам classic ML с интервальным повторением.\n\n"
    "<b>Команды:</b>\n"
    "/quiz — случайный вопрос по всей базе (spaced repetition)\n"
    "/levels — выбрать уровень roadmap и гонять его\n"
    "/difficulty — фильтр по сложности (⭐/⭐⭐/⭐⭐⭐)\n"
    "/stats — прогресс по уровням\n"
    "/subscribe — включить «вопрос дня» + напоминания\n"
    "/unsubscribe — выключить рассылку\n"
    "/help — эта справка\n\n"
    "В тренировке: читаешь вопрос → либо «Показать ответ» и честно оцени себя, "
    "либо «✍️ Ответить текстом» — напиши ответ словами, и я проверю его и "
    "разберу (active recall, ближе к собесу). Чем хуже знал — тем раньше "
    "вопрос вернётся.\n\n"
    "Новые карточки вводятся дозированно (по чуть-чуть в день) — так "
    "spaced repetition реально работает."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.upsert_user(
        message.from_user.id, message.chat.id, message.from_user.username
    )
    await db.ensure_cards(message.from_user.id)
    await message.answer(
        "Привет! 👋 Я буду гонять тебя по вопросам ML.\n\n" + HELP_TEXT
        + "\n\nНачнём? Жми /quiz"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    await db.upsert_user(
        message.from_user.id, message.chat.id, message.from_user.username
    )
    await db.set_subscription(message.from_user.id, True)
    await message.answer("🔔 Подписка на «вопрос дня» включена.")


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message) -> None:
    await db.set_subscription(message.from_user.id, False)
    await message.answer("🔕 Окей, «вопрос дня» больше не присылаю.")
