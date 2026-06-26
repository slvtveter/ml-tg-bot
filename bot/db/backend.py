"""Абстракция доступа к БД с двумя бэкендами под одним интерфейсом:

  • AiosqliteBackend — локальный файл SQLite (для разработки без Turso).
  • LibsqlBackend    — удалённая Turso/libSQL (для прода: данные переживают
                       любой передеплой на эфемерной ФС Render).

Выбор автоматический: если задан TURSO_DATABASE_URL — берём Turso, иначе
локальный файл. Обе БД — это SQLite, поэтому SQL один и тот же.

Единый контракт:
  execute(sql, params) -> list[dict]   # для SELECT — строки, для записи — []
  executescript(script) -> None        # выполнить несколько DDL-стейтментов
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

import aiosqlite

from bot.config import settings


class AiosqliteBackend:
    """Локальный SQLite-файл через aiosqlite (одно общее соединение)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def _ensure(self) -> aiosqlite.Connection:
        if self._conn is None:
            settings.db_file.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys = ON")
            await self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        conn = await self._ensure()
        cur = await conn.execute(sql, tuple(params))
        head = sql.lstrip().lower()
        if head.startswith(("select", "with")):
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
        await conn.commit()
        return []

    async def executescript(self, script: str) -> None:
        conn = await self._ensure()
        await conn.executescript(script)
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


class LibsqlBackend:
    """Удалённая Turso/libSQL через libsql-client (HTTP-транспорт).

    libsql:// поднимается клиентом как websocket (wss), который у Turso местами
    отдаёт 400 на handshake. Переключаемся на https:// — это hrana-over-HTTP,
    работает стабильно. Тот же endpoint, другая схема.
    """

    def __init__(self, url: str, auth_token: str) -> None:
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self._url = url
        self._auth_token = auth_token
        self._client = None

    def _ensure(self):
        if self._client is None:
            import libsql_client  # импорт здесь, чтобы локальный режим не требовал пакет
            self._client = libsql_client.create_client(
                self._url, auth_token=self._auth_token
            )
        return self._client

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        rs = await self._ensure().execute(sql, list(params))
        return [dict(zip(rs.columns, row)) for row in rs.rows]

    async def executescript(self, script: str) -> None:
        statements = [s.strip() for s in script.split(";") if s.strip()]
        await self._ensure().batch(statements)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


_backend: Optional[Any] = None


def _make_backend():
    if settings.turso_database_url:
        return LibsqlBackend(settings.turso_database_url, settings.turso_auth_token)
    return AiosqliteBackend(str(settings.db_file))


def get_backend():
    global _backend
    if _backend is None:
        _backend = _make_backend()
    return _backend


async def execute(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    return await get_backend().execute(sql, params)


async def fetchone(sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
    rows = await execute(sql, params)
    return rows[0] if rows else None


async def fetchall(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    return await execute(sql, params)


async def executescript(script: str) -> None:
    await get_backend().executescript(script)


async def close() -> None:
    if _backend is not None:
        await _backend.close()


def backend_name() -> str:
    return "Turso/libSQL" if settings.turso_database_url else "локальный SQLite"
