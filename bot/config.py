"""Конфигурация бота. Читает значения из .env (см. .env.example)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (на уровень выше пакета bot/)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Пустой по умолчанию, чтобы seed/тесты работали без токена.
    # Наличие проверяется при старте бота (main.py).
    bot_token: str = ""
    tz: str = "Europe/Moscow"
    qotd_hour: int = 9
    qotd_minute: int = 0
    # Час ежедневного напоминания о карточках к повторению (due).
    reminder_hour: int = 20
    # Сколько НОВЫХ карточек вводить в день (дозированный spaced repetition).
    daily_new_limit: int = 15
    db_path: str = "data/bot.db"

    # LLM-проверка ответов (Google Gemini). Если ключ пуст — режим выключен.
    gemini_api_key: str = ""
    # Цепочка моделей: при лимите/ошибке на одной запрос идёт на следующую.
    gemini_models: str = "gemini-2.5-flash,gemini-2.0-flash,gemini-1.5-flash"

    # Turso/libSQL (прод-персистентность). Если пусто — берётся локальный SQLite.
    turso_database_url: str = ""
    turso_auth_token: str = ""

    # Webhook-режим. Если WEBHOOK_URL задан — бот работает на webhook+HTTP-сервере
    # (для хостингов вроде Render), иначе — polling (локально).
    webhook_url: str = ""
    port: int = 10000
    keepalive_minutes: int = 10

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def db_file(self) -> Path:
        """Абсолютный путь к файлу БД."""
        p = Path(self.db_path)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def gemini_model_list(self) -> list[str]:
        """Модели Gemini в порядке fallback."""
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]


settings = Settings()
