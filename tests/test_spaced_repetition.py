"""Тесты алгоритма SM-2 (чистая логика, без БД)."""
from datetime import datetime, timezone

from bot.services import spaced_repetition as sr


def test_new_card_due_now():
    c = sr.new_card()
    assert c.interval == 0
    assert c.repetitions == 0
    assert c.ease_factor == sr.DEFAULT_EASE
    assert c.due <= datetime.now(timezone.utc)


def test_known_progression():
    c = sr.new_card()
    c1 = sr.review(c, "known")
    assert c1.interval == 1 and c1.repetitions == 1
    c2 = sr.review(c1, "known")
    assert c2.interval == 6 and c2.repetitions == 2
    c3 = sr.review(c2, "known")
    assert c3.interval == round(6 * c2.ease_factor)
    # ease растёт при "known"
    assert c3.ease_factor > sr.DEFAULT_EASE


def test_again_resets():
    c = sr.review(sr.review(sr.new_card(), "known"), "known")  # reps=2
    after = sr.review(c, "again")
    assert after.repetitions == 0
    assert after.interval == 0
    assert after.ease_factor < c.ease_factor  # ease падает
    assert after.due > datetime.now(timezone.utc)  # повтор чуть позже


def test_ease_floor():
    c = sr.new_card()
    for _ in range(10):
        c = sr.review(c, "again")
    assert c.ease_factor >= sr.MIN_EASE


def test_partial_keeps_progress():
    c = sr.new_card()
    c1 = sr.review(c, "partial")
    assert c1.repetitions == 1  # partial (q=3) считается успехом
    assert c1.interval == 1
