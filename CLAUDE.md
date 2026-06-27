# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Telegram-бот (@mlsobesbot) для подготовки к ML-собеседованиям: карточки с
вопросами по roadmap (уровни 0–3, структура nareshka.ru) и интервальным
повторением (spaced repetition, SM-2). Стек: aiogram 3, SQLite/Turso, APScheduler.

## Команды

```bash
# Локальная разработка
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # вписать BOT_TOKEN; TURSO/WEBHOOK можно пустыми

python -m bot.main              # запустить бота (polling, если WEBHOOK_URL пуст)
python -m bot.db.seed           # залить data/questions.yaml в БД отдельно

# Docker
docker compose up --build

# Деплой: НЕ ручной — git push в main → Render авто-собирает и деплоит.
```

Формальных тестов в репозитории нет. Проверка обычно делается ad-hoc скриптом:
поднять чистый SQLite через `export DB_PATH=...; unset TURSO_*`, прогнать
`python -m bot.db.seed`, затем вызвать функции из `bot/db/database.py`
(`list_levels`, `pick_due_card`, `get_stats`) против него.

## Архитектура (что важно понять до правок)

**Два режима, выбор по env (`bot/main.py`):** если задан `WEBHOOK_URL` — webhook
(aiohttp-сервер на `PORT`, Telegram шлёт апдейты на `/webhook`, health на `/`,
keep-alive самопинг каждые `KEEPALIVE_MINUTES`); иначе — polling. На Render это
webhook: free web service ОБЯЗАН открыть порт, поэтому polling-деплой там
зависает на port scan — на проде всегда должен быть выставлен `WEBHOOK_URL`.

**Абстракция БД (`bot/db/backend.py`):** единый интерфейс `execute/fetchone/
fetchall/executescript` поверх двух бэкендов — `AiosqliteBackend` (локальный файл,
если нет Turso) и `LibsqlBackend` (Turso/libSQL для прода, данные переживают
эфемерную ФС Render). Выбор автоматический по наличию `TURSO_DATABASE_URL`.
Важная деталь: `libsql://` НОРМАЛИЗУЕТСЯ в `https://` (websocket-транспорт Turso
отдаёт 400, HTTP работает). У `LibsqlBackend.execute` есть retry с backoff.
Весь `bot/db/database.py` ходит в БД только через этот слой.

**Данные — дерево `level → topic → question` (`data/questions.yaml`):** схема
`topics(parent_id)` 2-уровневая; верхние узлы (parent_id IS NULL) — уровни
roadmap, их дети — темы, вопросы привязаны к темам по slug. Новых полей под
уровни не нужно — иерархия держится на `parent_id`. `list_levels`/`get_stats`
группируют по родителю (уровню).

**Seed идемпотентен (`bot/db/seed.py`), запускается на каждом старте.** Темы
матчатся по `slug`, вопросы по `uid` (`ON CONFLICT`). КРИТИЧНО: при изменении
банка СОХРАНЯЙ существующие `uid` — карточки/прогресс пользователя в таблице
`cards` привязаны к `question_id`, который стабилен только пока стабилен `uid`.
В конце seed чистит темы-сироты (после реструктуризации roadmap).

**Spaced repetition (`bot/services/spaced_repetition.py`):** чистый SM-2,
без побочных эффектов. Состояние карточки (ease_factor/interval/repetitions/due)
хранится в `cards` на пару (user_id, question_id). `ensure_cards` ленивая —
создаёт карточки для всех вопросов, которых у юзера ещё нет (вызывается на
/start, /quiz, /levels и в джобах рассылки).

**Поток тренировки (`bot/handlers/quiz.py`):** вопрос → callback `show:` →
оценка `rate:` (again/partial/known → SM-2) → следующая карточка. Выбранный
уровень хранится in-memory в `_user_level` (сбрасывается при рестарте — это ок).
`/quiz` = вся база, `/levels` = фокус на уровне.

**Планировщик (`bot/scheduler.py`):** APScheduler, две cron-джобы — «вопрос дня»
(`QOTD_HOUR`) и напоминание о due-карточках (`REMINDER_HOUR`); плюс keep-alive в
webhook-режиме. Джобы in-memory, пересоздаются на старте.

## Деплой / прод

Render web service (Docker, free, frankfurt) с авто-деплоем из `main`. БД —
Turso. Env-переменные (`BOT_TOKEN`, `TURSO_*`, `WEBHOOK_URL` и т.д.) заданы в
Render, НЕ в репозитории. `render.yaml` — Blueprint для пересоздания сервиса.
Латенси повышена географией (Render EU ↔ Turso us-east).

## Стиль контента (карточки)

Ответы — на русском, технические термины в оригинале (английском). Формулы —
текстовой нотацией без LaTeX (например `w := w − lr * ∇L`), греческие буквы
словами. Стиль: интуиция → шаги → пример → формула; где уместно — связь с тем,
что спрашивают на собесах. Это учебный материал, держи планку качества.
