"""Алгоритм интервального повторения (spaced repetition), вариант SM-2.

Идея: чем хуже ты помнишь карточку, тем чаще её надо показывать;
чем увереннее знаешь — тем реже. Так память тратится эффективно:
лёгкие вопросы не отвлекают, сложные всплывают, пока не закрепятся.

У каждой карточки три параметра состояния:
  - ease_factor (EF) — "коэффициент лёгкости", во сколько раз растёт интервал.
        Стартует с 2.5, не опускается ниже 1.3.
  - interval     — через сколько дней показать снова.
  - repetitions  — сколько раз подряд успешно вспомнил.

Пользователь оценивает ответ одной из трёх кнопок, мы переводим это в
"quality" (q) по шкале SM-2 (0..5):
  - "Не знал"   -> q = 2  (провал: сбрасываем прогресс, повтор сегодня)
  - "Частично"  -> q = 3  (вспомнил с трудом)
  - "Знал"      -> q = 5  (уверенно)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MIN_EASE = 1.3
DEFAULT_EASE = 2.5

# Кнопки оценки -> quality по SM-2
QUALITY = {
    "again": 2,    # не знал
    "partial": 3,  # частично
    "known": 5,    # знал
}


@dataclass
class CardState:
    ease_factor: float
    interval: int          # в днях
    repetitions: int
    due: datetime          # когда показать снова (UTC)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_card() -> CardState:
    """Стартовое состояние для новой карточки — она due прямо сейчас."""
    return CardState(
        ease_factor=DEFAULT_EASE,
        interval=0,
        repetitions=0,
        due=_now(),
    )


def review(state: CardState, rating: str) -> CardState:
    """Пересчитать состояние карточки по оценке пользователя.

    rating: один из "again" / "partial" / "known".
    Возвращает НОВОЕ состояние (старое не мутируем).
    """
    q = QUALITY[rating]
    ef = state.ease_factor
    reps = state.repetitions

    if q < 3:
        # Провал: сбрасываем серию, показываем снова в этой же сессии.
        reps = 0
        interval = 0
        due = _now() + timedelta(minutes=1)
    else:
        # Успех: наращиваем интервал.
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = round(state.interval * ef)
        reps += 1
        due = _now() + timedelta(days=interval)

    # Обновляем коэффициент лёгкости. Формула из SM-2:
    #   EF := EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    # Чем ниже оценка, тем сильнее падает EF -> тем медленнее растут
    # интервалы у тяжёлых карточек.
    ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ef = max(MIN_EASE, ef)

    return CardState(ease_factor=ef, interval=interval, repetitions=reps, due=due)
