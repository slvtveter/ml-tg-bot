"""Точка входа.

Два режима работы (выбираются автоматически по наличию WEBHOOK_URL):
  • polling  — локальная разработка (бот сам опрашивает Telegram).
  • webhook  — хостинг типа Render: поднимаем HTTP-сервер, Telegram шлёт апдейты
               на наш публичный URL. Плюс keep-alive: сами пингуем свой адрес
               каждые KEEPALIVE_MINUTES, чтобы free-сервис не «засыпал».
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import ClientSession, ClientTimeout, web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.db import backend
from bot.db.seed import seed
from bot.handlers import common, quiz, stats
from bot.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="quiz", description="Следующий вопрос"),
        BotCommand(command="stats", description="Прогресс по темам"),
        BotCommand(command="subscribe", description="Включить вопрос дня"),
        BotCommand(command="unsubscribe", description="Выключить вопрос дня"),
        BotCommand(command="help", description="Справка"),
    ])


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(quiz.router)
    dp.include_router(stats.router)
    return dp


async def _ping_self(url: str) -> None:
    """Keep-alive: дёргаем собственный health-эндпоинт, чтобы не уснуть."""
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as s:
            await s.get(url)
    except Exception as e:  # noqa: BLE001
        logger.warning("keep-alive ping не прошёл: %s", e)


async def run_polling(bot: Bot, dp: Dispatcher, scheduler: AsyncIOScheduler) -> None:
    scheduler.start()
    await set_commands(bot)
    logger.info("Режим polling. Запускаю опрос Telegram…")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await backend.close()


async def run_webhook(bot: Bot, dp: Dispatcher, scheduler: AsyncIOScheduler) -> None:
    base = settings.webhook_url.rstrip("/")
    webhook_url = base + WEBHOOK_PATH

    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    async def on_startup(_app: web.Application) -> None:
        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
        await set_commands(bot)
        scheduler.add_job(
            _ping_self, "interval", minutes=settings.keepalive_minutes,
            args=[base + "/"], id="keepalive", replace_existing=True,
        )
        scheduler.start()
        logger.info("Webhook установлен: %s | keep-alive каждые %d мин",
                    webhook_url, settings.keepalive_minutes)

    async def on_cleanup(_app: web.Application) -> None:
        scheduler.shutdown(wait=False)
        await backend.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    logger.info("Режим webhook. Слушаю 0.0.0.0:%d", settings.port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    await asyncio.Event().wait()  # держим процесс живым


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit(
            "Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен "
            "от @BotFather:  cp .env.example .env"
        )

    # 1. Заполняем БД темами и вопросами из YAML (идемпотентно).
    result = await seed()
    logger.info("БД: %s | тем %s, вопросов %s",
                backend.backend_name(), result["topics"], result["questions"])

    # 2. Бот с HTML-разметкой по умолчанию.
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()
    scheduler = setup_scheduler(bot)
    logger.info("«Вопрос дня» в %02d:%02d (%s)",
                settings.qotd_hour, settings.qotd_minute, settings.tz)

    if settings.webhook_url:
        await run_webhook(bot, dp, scheduler)
    else:
        await run_polling(bot, dp, scheduler)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено.")
