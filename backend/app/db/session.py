"""
Async database connection pool using asyncpg.
Provides a context-managed pool with tenant injection.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


class DatabasePool:
    """Wraps asyncpg pool with lifecycle management."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def startup(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql"),
            min_size=settings.DB_POOL_MIN_SIZE,
            max_size=settings.DB_POOL_MAX_SIZE,
            command_timeout=settings.DB_COMMAND_TIMEOUT,
            server_settings={
                "application_name": "recko-api",
                "timezone": "UTC",
            },
        )
        logger.info("db.pool_created", min=settings.DB_POOL_MIN_SIZE, max=settings.DB_POOL_MAX_SIZE)

    async def shutdown(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("db.pool_closed")

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool not initialised. Call startup() first.")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Get a raw connection from the pool."""
        async with self.pool.acquire() as conn:
            yield conn  # type: ignore[misc]

    @asynccontextmanager
    async def acquire_for_tenant(
        self, client_id: str
    ) -> AsyncIterator[asyncpg.Connection]:
        """
        Get a connection with the tenant context set.
        This enables PostgreSQL RLS policies to filter by client_id.
        """
        async with self.pool.acquire() as conn:
            # Set the tenant context for RLS enforcement
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, true)",
                str(client_id),
            )
            yield conn  # type: ignore[misc]


db_pool = DatabasePool()
