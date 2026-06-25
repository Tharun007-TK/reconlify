"""
ARQ worker settings and task registry.
"""
from __future__ import annotations

from arq import ArqRedis
from arq.connections import RedisSettings

from app.config import settings
from app.workers.tasks.parse_task import parse_task
from app.workers.tasks.recon_task import recon_task


async def report_task(ctx, *, run_id: str, client_id: str):  # type: ignore[no-untyped-def]
    """Delegated import to avoid circular deps."""
    from app.workers.tasks.report_task import report_task as _rt
    return await _rt(ctx, run_id=run_id, client_id=client_id)


async def notify_task(ctx, *, run_id: str, client_id: str):  # type: ignore[no-untyped-def]
    from app.workers.tasks.notify_task import notify_task as _nt
    return await _nt(ctx, run_id=run_id, client_id=client_id)


async def startup(ctx: dict) -> None:
    from app.db.session import db_pool
    from app.core.logging import configure_logging
    configure_logging()
    await db_pool.startup()
    # Expose queue in ctx so tasks can enqueue child jobs
    ctx["queue"] = ctx["redis"]


async def shutdown(ctx: dict) -> None:
    from app.db.session import db_pool
    await db_pool.shutdown()


class WorkerSettings:
    functions = [parse_task, recon_task, report_task, notify_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = settings.WORKER_CONCURRENCY
    job_timeout = settings.RECONLIFY_TIMEOUT_SECONDS + 300  # headroom
    keep_result = 3600  # keep results for 1 hour
    queue_name = "recko:queue"
