"""«Вопрос дня»: ежедневная рассылка подписчикам по расписанию."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import keyboards as kb
from bot.config import settings
from bot.db import database as db
from bot.handlers.quiz import _question_text

logger = logging.getLogger(__name__)


async def send_question_of_the_day(bot: Bot) -> None:
    """Каждому подписчику — одну due-карточку с пометкой «вопрос дня»."""
    subscribers = await db.get_subscribers()
    for user in subscribers:
        await db.ensure_cards(user["user_id"])
        card = await db.pick_due_card(user["user_id"])
        if card is None:
            continue
        text = "🌅 <b>Вопрос дня</b>\n\n" + _question_text(card)
        try:
            await bot.send_message(
                user["chat_id"], text, reply_markup=kb.show_answer_kb(card["id"])
            )
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — снимаем с подписки.
            await db.set_subscription(user["user_id"], False)
        except Exception as e:  # noqa: BLE001 — рассылка не должна падать целиком
            logger.warning("Не смог отправить вопрос дня %s: %s", user["user_id"], e)


async def send_due_reminder(bot: Bot) -> None:
    """Напоминание: если есть карточки к повторению — зовём в /quiz."""
    subscribers = await db.get_subscribers()
    for user in subscribers:
        await db.ensure_cards(user["user_id"])
        due = await db.count_due_cards(user["user_id"])
        if due <= 0:
            continue
        text = (
            f"⏰ <b>{due}</b> карточек ждут повторения. "
            "Закрепи, пока не забылось — жми /quiz"
        )
        try:
            await bot.send_message(user["chat_id"], text)
        except TelegramForbiddenError:
            await db.set_subscription(user["user_id"], False)
        except Exception as e:  # noqa: BLE001 — рассылка не должна падать целиком
            logger.warning("Не смог отправить напоминание %s: %s", user["user_id"], e)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        send_question_of_the_day,
        trigger="cron",
        hour=settings.qotd_hour,
        minute=settings.qotd_minute,
        args=[bot],
        id="question_of_the_day",
        replace_existing=True,
    )
    scheduler.add_job(
        send_due_reminder,
        trigger="cron",
        hour=settings.reminder_hour,
        args=[bot],
        id="due_reminder",
        replace_existing=True,
    )
    return scheduler
