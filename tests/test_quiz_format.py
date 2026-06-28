"""Тесты рефлоу текста карточек (склейка жёстко перенесённых строк)."""
from bot.handlers.quiz import _reflow


def test_joins_paragraph():
    src = "Первая строка\nпродолжение строки\nещё продолжение"
    assert _reflow(src) == "Первая строка продолжение строки ещё продолжение"


def test_keeps_blank_lines():
    src = "Абзац один\nстрока два\n\nАбзац два"
    assert _reflow(src) == "Абзац один строка два\n\nАбзац два"


def test_keeps_bullets_and_joins_their_continuation():
    src = "Список:\n• первый пункт\n  его продолжение\n• второй пункт"
    lines = _reflow(src).split("\n")
    assert lines[0] == "Список:"
    assert lines[1] == "• первый пункт его продолжение"
    assert lines[2] == "• второй пункт"


def test_breaks_after_colon():
    src = "Заголовок:\n  значение формулы"
    assert _reflow(src) == "Заголовок:\nзначение формулы"


def test_numbered_items_are_separate():
    src = "1. раз\nпродолжение\n2. два"
    lines = _reflow(src).split("\n")
    assert lines[0] == "1. раз продолжение"
    assert lines[1] == "2. два"
