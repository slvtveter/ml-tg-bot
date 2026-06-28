"""Тесты слоя БД: уровни, дозированный ввод новых, приоритет повторов, статистика."""
from datetime import datetime, timedelta, timezone

from bot.config import settings
from bot.db import backend, database as db
from bot.services import spaced_repetition as sr
from bot.services.spaced_repetition import CardState

USER = 1


async def _rate(user, qid, rating):
    state = await db.get_card_state(user, qid)
    await db.save_review(user, qid, rating, sr.review(state, rating))


async def test_list_levels(populated):
    levels = await db.list_levels()
    assert len(levels) == 1
    assert levels[0]["total"] == 3


async def test_pick_new_when_no_reviews(populated):
    await db.ensure_cards(USER)
    card = await db.pick_due_card(USER)
    assert card is not None  # отдаёт новую карточку


async def test_review_takes_priority_over_new(populated):
    await db.ensure_cards(USER)
    qid = (await db.pick_due_card(USER))["id"]
    # Делаем карточку «виденной» и просроченной (due в прошлом).
    past = CardState(2.5, 1, 1, datetime.now(timezone.utc) - timedelta(days=1))
    await db.save_review(USER, qid, "known", past)
    # Несмотря на доступные новые, должен прийти именно повтор.
    card = await db.pick_due_card(USER)
    assert card["id"] == qid


async def test_daily_new_limit(populated):
    settings.daily_new_limit = 2
    await db.ensure_cards(USER)
    introduced = []
    for _ in range(2):
        card = await db.pick_due_card(USER)
        assert card is not None
        await _rate(USER, card["id"], "known")
        introduced.append(card["id"])
    assert len(set(introduced)) == 2
    # Лимит исчерпан, due-повторов нет -> на сегодня всё.
    assert await db.pick_due_card(USER) is None
    # Но фокус на уровне обходит лимит (осознанная проработка).
    forced = await db.pick_due_card(USER, level_id=populated["level"])
    assert forced is not None and forced["id"] not in introduced


async def test_workload_counts(populated):
    settings.daily_new_limit = 5
    await db.ensure_cards(USER)
    wl = await db.count_today_workload(USER)
    assert wl["new_total"] == 3
    assert wl["new_today"] == 3  # min(3 новых, лимит 5)
    assert wl["due_review"] == 0
    assert wl["total"] == 3


async def test_stats_fields(populated):
    await db.ensure_cards(USER)
    card = await db.pick_due_card(USER)
    await _rate(USER, card["id"], "known")
    s = await db.get_stats(USER)
    assert s["total"] == 3
    assert s["seen"] == 1
    assert s["new_total"] == 2
    assert len(s["by_level"]) == 1
    assert s["by_level"][0]["total"] == 3


async def test_new_introduced_today(populated):
    await db.ensure_cards(USER)
    assert await db.new_introduced_today(USER) == 0
    card = await db.pick_due_card(USER)
    await _rate(USER, card["id"], "known")
    assert await db.new_introduced_today(USER) == 1


async def test_exclude_ids_avoids_repeat(populated):
    await db.ensure_cards(USER)
    c1 = await db.pick_due_card(USER)
    c2 = await db.pick_due_card(USER, exclude_ids=[c1["id"]])
    assert c2 is not None and c2["id"] != c1["id"]


async def test_no_immediate_repeats_in_window(populated):
    """Симуляция сессии: с анти-повтором (deque) карточки не повторяются в окне 6."""
    from collections import deque

    for i in range(3, 10):  # доводим до 10 вопросов
        await backend.execute(
            "INSERT INTO questions (topic_id, question, answer, difficulty, source, uid) "
            "VALUES (?, ?, ?, ?, 'seed', ?)",
            (populated["topic"], f"Q{i}", f"A{i}", 2, f"u{i}"),
        )
    settings.daily_new_limit = 100
    await db.ensure_cards(USER)

    recent: deque = deque(maxlen=6)
    seq = []
    for _ in range(20):
        card = await db.pick_due_card(USER, exclude_ids=list(recent))
        assert card is not None
        assert card["id"] not in recent  # не повторяется в окне недавних
        recent.append(card["id"])
        seq.append(card["id"])
    for i in range(len(seq)):  # ни один id не встречается среди предыдущих 6
        assert seq[i] not in seq[max(0, i - 6):i]


async def test_difficulty_filter(populated):
    await backend.execute(
        "INSERT INTO questions (topic_id, question, answer, difficulty, source, uid) "
        "VALUES (?, ?, ?, ?, 'seed', ?)",
        (populated["topic"], "Сложный", "Ответ", 3, "uhard"),
    )
    await db.ensure_cards(USER)
    card = await db.pick_due_card(USER, difficulty=3)
    assert card is not None and card["difficulty"] == 3
    # единственную сложную исключаем -> по сложности 3 ничего не остаётся
    assert await db.pick_due_card(USER, difficulty=3, exclude_ids=[card["id"]]) is None
