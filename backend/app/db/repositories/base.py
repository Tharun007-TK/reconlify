"""
Base repository providing generic CRUD primitives with asyncpg.
All domain repositories extend this class.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


class BaseRepository:
    """
    Abstract base repository.
    Receives a raw asyncpg.Connection; transaction management is caller's responsibility.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def fetch_one(
        self,
        query: str,
        *args: Any,
    ) -> asyncpg.Record | None:
        logger.debug("db.fetch_one", query=query[:80])
        return await self._conn.fetchrow(query, *args)

    async def fetch_many(
        self,
        query: str,
        *args: Any,
    ) -> list[asyncpg.Record]:
        logger.debug("db.fetch_many", query=query[:80])
        return await self._conn.fetch(query, *args)

    async def execute(
        self,
        query: str,
        *args: Any,
    ) -> str:
        logger.debug("db.execute", query=query[:80])
        return await self._conn.execute(query, *args)

    async def execute_many(
        self,
        query: str,
        args: list[tuple[Any, ...]],
    ) -> None:
        logger.debug("db.execute_many", query=query[:80], rows=len(args))
        await self._conn.executemany(query, args)

    async def fetch_val(
        self,
        query: str,
        *args: Any,
        column: int = 0,
    ) -> Any:
        return await self._conn.fetchval(query, *args, column=column)

    async def count(self, table: str, where: str = "", *args: Any) -> int:
        q = f"SELECT COUNT(*) FROM {table}"
        if where:
            q += f" WHERE {where}"
        result = await self._conn.fetchval(q, *args)
        return int(result or 0)

    async def exists(self, table: str, id: UUID) -> bool:
        result = await self._conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM {table} WHERE id = $1)", id
        )
        return bool(result)
