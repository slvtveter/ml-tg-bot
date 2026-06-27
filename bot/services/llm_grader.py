"""LLM-проверка открытых ответов через Google Gemini (active recall).

Пользователь пишет ответ текстом, модель сравнивает его с эталоном по СУТИ и
выдаёт вердикт (again/partial/known) + краткий разбор. Вердикт напрямую
ложится в SM-2.

Fallback: модели перебираются по списку GEMINI_MODELS. Если на одной кончилась
квота / прилетел лимит (429) / любая ошибка — запрос идёт на следующую модель.
Если все модели упали или ключ не задан — возвращаем None (бот откатывается на
ручной режим «показать ответ»).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = (
    "Ты — доброжелательный, но честный экзаменатор по Machine Learning на "
    "собеседовании. Сравни ответ студента с эталоном ПО СУТИ (не по дословной "
    "формулировке). Вердикт: 'known' — суть верна и достаточно полно; 'partial' "
    "— верно частично, есть заметные пробелы; 'again' — неверно или почти пусто. "
    "Дай краткий разбор на русском (2–4 предложения): что верно, что упущено, без "
    "воды. Технические термины оставляй в оригинале (английском)."
)

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["again", "partial", "known"]},
        "feedback": {"type": "STRING"},
    },
    "required": ["verdict", "feedback"],
}

MAX_ANSWER_CHARS = 2000


def is_enabled() -> bool:
    return bool(settings.gemini_api_key)


def _build_payload(question: str, reference: str, user_answer: str) -> dict:
    user_answer = user_answer[:MAX_ANSWER_CHARS]
    prompt = (
        f"ВОПРОС:\n{question}\n\n"
        f"ЭТАЛОННЫЙ ОТВЕТ (для сверки):\n{reference}\n\n"
        f"ОТВЕТ СТУДЕНТА:\n{user_answer}\n\n"
        "Оцени ответ студента и верни JSON по схеме."
    )
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
        },
    }


def _parse(data: dict) -> Optional[dict]:
    """Достать вердикт из ответа Gemini. None, если формат неожиданный."""
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    verdict = parsed.get("verdict")
    if verdict not in ("again", "partial", "known"):
        return None
    return {"verdict": verdict, "feedback": (parsed.get("feedback") or "").strip()}


async def _call(session: aiohttp.ClientSession, model: str, payload: dict) -> Optional[dict]:
    url = API_URL.format(model=model)
    async with session.post(
        url, params={"key": settings.gemini_api_key}, json=payload,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        if resp.status == 200:
            return _parse(await resp.json())
        body = (await resp.text())[:200]
        raise RuntimeError(f"HTTP {resp.status}: {body}")


async def grade(question: str, reference: str, user_answer: str) -> Optional[dict]:
    """Оценить ответ. Возвращает {'verdict', 'feedback'} или None при недоступности."""
    if not settings.gemini_api_key:
        return None
    payload = _build_payload(question, reference, user_answer)
    async with aiohttp.ClientSession() as session:
        for model in settings.gemini_model_list:
            try:
                result = await _call(session, model, payload)
                if result is not None:
                    return result
                logger.warning("Gemini %s: неожиданный формат ответа", model)
            except Exception as e:  # noqa: BLE001 — пробуем следующую модель
                logger.warning("Gemini %s недоступна, пробую следующую: %s", model, e)
                continue
    return None
