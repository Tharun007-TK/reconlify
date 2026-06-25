"""
Internal Admin router — restricted to internal_admin role only.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter
from fastapi.responses import ORJSONResponse

from app.core.security import CurrentUser, UserRole
from app.db.session import db_pool

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("/tenants", summary="List all client tenants")
async def list_tenants(current_user: CurrentUser) -> ORJSONResponse:
    current_user.require_role(UserRole.INTERNAL_ADMIN)
    async with db_pool.acquire() as conn:
        clients = await conn.fetch(
            "SELECT id, name, gstin, plan, status, max_users, created_at FROM clients ORDER BY created_at DESC"
        )
    return ORJSONResponse({"clients": [dict(c) for c in clients]})


@router.get("/system/health", summary="System health check", include_in_schema=False)
async def system_health(current_user: CurrentUser) -> ORJSONResponse:
    current_user.require_role(UserRole.INTERNAL_ADMIN)
    import redis.asyncio as aioredis
    from app.config import settings

    health: dict[str, object] = {"api": "ok"}

    # DB check
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        health["database"] = "ok"
    except Exception as e:
        health["database"] = f"error: {e}"

    # Redis check
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.aclose()
        health["redis"] = "ok"
    except Exception as e:
        health["redis"] = f"error: {e}"

    return ORJSONResponse(health)


@router.get("/system/metrics", summary="Usage metrics across all tenants")
async def system_metrics(current_user: CurrentUser) -> ORJSONResponse:
    current_user.require_role(UserRole.INTERNAL_ADMIN)
    async with db_pool.acquire() as conn:
        metrics = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM clients WHERE status = 'active') AS active_clients,
                (SELECT COUNT(*) FROM users WHERE is_active = true)    AS active_users,
                (SELECT COUNT(*) FROM reconciliation_runs)              AS total_runs,
                (SELECT COUNT(*) FROM reconciliation_runs WHERE status = 'completed') AS completed_runs,
                (SELECT COUNT(*) FROM reconciliation_runs WHERE status = 'failed')   AS failed_runs,
                (SELECT COUNT(*) FROM reconciliation_runs WHERE status IN ('queued','parsing','reconciling')) AS running_jobs
            """
        )
    return ORJSONResponse(dict(metrics) if metrics else {})
