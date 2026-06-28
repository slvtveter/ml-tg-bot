"""Тесты LLM-grader: парсинг вердикта, fallback по моделям, выключенный режим."""
import pytest

from bot.config import settings
from bot.services import llm_grader


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert llm_grader.is_enabled() is False


def test_parse_valid():
    data = {"candidates": [{"content": {"parts": [
        {"text": '{"verdict": "partial", "feedback": "Норм, но упустил X"}'}
    ]}}]}
    out = llm_grader._parse(data)
    assert out == {"verdict": "partial", "feedback": "Норм, но упустил X"}


def test_parse_bad_verdict_returns_none():
    data = {"candidates": [{"content": {"parts": [
        {"text": '{"verdict": "excellent", "feedback": "x"}'}
    ]}}]}
    assert llm_grader._parse(data) is None


async def test_grade_returns_none_without_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert await llm_grader.grade("q", "ref", "ans") is None


async def test_grade_fallback_to_next_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key")
    monkeypatch.setattr(settings, "gemini_models", "m1,m2")

    calls = []

    async def fake_call(session, model, payload):
        calls.append(model)
        if model == "m1":
            raise RuntimeError("HTTP 429: quota")  # лимит на первой модели
        return {"verdict": "known", "feedback": "ok"}

    monkeypatch.setattr(llm_grader, "_call", fake_call)
    result = await llm_grader.grade("q", "ref", "ans")
    assert result == {"verdict": "known", "feedback": "ok"}
    assert calls == ["m1", "m2"]  # упала первая -> ушли на вторую


async def test_grade_all_models_fail_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key")
    monkeypatch.setattr(settings, "gemini_models", "m1,m2")

    async def fake_call(session, model, payload):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(llm_grader, "_call", fake_call)
    assert await llm_grader.grade("q", "ref", "ans") is None


async def test_chat_fallback_to_next_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key")
    monkeypatch.setattr(settings, "gemini_models", "m1,m2")
    calls = []

    async def fake_text(session, model, payload):
        calls.append(model)
        if model == "m1":
            raise RuntimeError("HTTP 429")
        return "ответ репетитора"

    monkeypatch.setattr(llm_grader, "_call_text", fake_text)
    out = await llm_grader.chat("system", [], "вопрос")
    assert out == "ответ репетитора"
    assert calls == ["m1", "m2"]


async def test_chat_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert await llm_grader.chat("s", [], "q") is None
