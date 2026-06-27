# ML Interview Trainer Bot

Telegram-бот, который гоняет тебя по вопросам **classic ML** с **интервальным
повторением** (spaced repetition, алгоритм SM-2 как в Anki). Цель — системно
закрыть roadmap по ML к собеседованиям: что плохо знаешь — всплывает чаще, что
усвоил — реже.

## Что умеет (Фаза 1)

- `/quiz` — тренировка по всей базе: вопрос → «показать ответ» → честная
  самооценка (❌ не знал / 🤔 частично / ✅ знал). Оценка идёт в spaced repetition
  и определяет, когда вопрос вернётся.
- `/levels` — выбрать уровень roadmap (0–3) и гонять его отдельно.
- `/stats` — прогресс и покрытие по уровням roadmap.
- `/subscribe` — рассылка: «вопрос дня» утром + напоминание о карточках к
  повторению вечером.
- Банк вопросов (~150 карточек, уровни 0–3 по roadmap nareshka) и дерево тем —
  в `data/questions.yaml` (легко дополнять).

## Стек

aiogram 3 · SQLite (aiosqlite) · APScheduler · pydantic-settings · Docker

## Запуск локально

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # впиши BOT_TOKEN от @BotFather
python -m bot.main
```

Получить токен: [@BotFather](https://t.me/BotFather) → `/newbot`.

## Запуск в Docker

```bash
cp .env.example .env          # впиши BOT_TOKEN
docker compose up --build
```

Локально база лежит в `data/bot.db` (том примонтирован — прогресс не теряется).

## Режимы работы

Бот сам выбирает режим по переменным окружения:

- **polling** (локально) — если `WEBHOOK_URL` пуст. Бот сам опрашивает Telegram.
- **webhook** (хостинг) — если задан `WEBHOOK_URL`. Поднимается HTTP-сервер,
  Telegram шлёт апдейты на `WEBHOOK_URL/webhook`. Health-эндпоинт `GET /`.
  Keep-alive: бот пингует себя каждые `KEEPALIVE_MINUTES` (<15), чтобы
  free-сервис не «засыпал».

Хранилище тоже по env:

- пусто → локальный файл `DB_PATH` (SQLite);
- задан `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` → удалённая Turso/libSQL
  (данные переживают любой передеплой на эфемерной ФС). Схема та же — это SQLite.

## Деплой на Render (free) + Turso

1. **Turso**: создай БД на [turso.tech](https://turso.tech), возьми
   `Database URL` (`libsql://...`) и `auth token`.
2. **GitHub**: запушь репозиторий (Render билдит Docker из репо).
3. **Render** → New → **Blueprint** (репо содержит `render.yaml`) или
   New → **Web Service** → Runtime: **Docker**, план **Free**.
4. Впиши переменные окружения:
   - `BOT_TOKEN` — от @BotFather
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
   - `WEBHOOK_URL` — публичный адрес сервиса, например
     `https://mlsobesbot.onrender.com` (Blueprint подставит сам).
5. Деплой. После старта проверь логи: `Webhook установлен …`.

> `WEBHOOK_URL` нормализуется в коде, главное — это публичный `https://`-адрес
> твоего сервиса без `/webhook` на конце (путь добавится сам).

## Как добавлять вопросы

Допиши блок в `data/questions.yaml` (тема по `slug`, уникальный `uid`):

```yaml
  - topic: logistic_regression
    uid: my_new_q
    difficulty: 3
    question: "..."
    answer: |
      ...
```

Темы заливаются в БД при каждом старте (идемпотентно). Можно вручную:
`python -m bot.db.seed`.

## Структура

```
bot/
  main.py                  точка входа (бот + планировщик)
  config.py                настройки из .env
  keyboards.py             inline-кнопки
  db/
    database.py            схема и доступ к данным (SQLite)
    seed.py                загрузка questions.yaml -> БД
  services/
    spaced_repetition.py   алгоритм SM-2
  handlers/
    common.py              /start /help /subscribe
    quiz.py                флоу тренировки
    stats.py               /stats
  scheduler.py             «вопрос дня»
data/
  questions.yaml           банк вопросов + roadmap тем
```

## Дальше (план)

- **Фаза 2** — LLM-проверка открытых ответов: пишешь ответ словами, Claude
  оценивает и указывает на пробелы.
- **Фаза 3** — RAG поверх твоих материалов + трекинг покрытия roadmap, чтобы
  закрыть базу целиком.
