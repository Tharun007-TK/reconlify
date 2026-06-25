"""
Reports API router.
Handles report generation queuing, status polling, and secure downloads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Query, status
from fastapi.responses import ORJSONResponse

from app.config import settings
from app.core.exceptions import NotFoundError
from app.core.security import CurrentUser, UserRole
from app.db.session import db_pool
from app.services.storage.supabase_storage import get_signed_url

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post(
    "/{run_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue report generation for a reconciliation run",
)
async def generate_report(
    run_id: uuid.UUID,
    current_user: CurrentUser,
    report_type: Annotated[
        str,
        Query(pattern="^(full_reconciliation|mismatch_summary|vendor_analysis|itc_risk_report)$"),
    ] = "full_reconciliation",
    report_format: Annotated[str, Query(pattern="^(xlsx|pdf)$")] = "xlsx",
) -> ORJSONResponse:
    current_user.require_role(UserRole.AUDITOR)

    # Verify run belongs to client
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        run = await conn.fetchrow(
            "SELECT id, status FROM reconciliation_runs WHERE id = $1 AND client_id = $2",
            run_id, current_user.client_id,
        )
        if not run:
            raise NotFoundError(f"Run {run_id} not found.")

        # Create pending report record
        report_id = uuid.uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REPORT_EXPIRY_DAYS)
        await conn.execute(
            """
            INSERT INTO reports
                (id, run_id, client_id, generated_by, rtype, rformat, status, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7)
            """,
            report_id, run_id, current_user.client_id,
            current_user.user_id, report_type, report_format, expires_at,
        )

    # Enqueue generation task
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await redis.enqueue_job(
        "report_task",
        run_id=str(run_id),
        client_id=str(current_user.client_id),
        report_id=str(report_id),
        report_type=report_type,
        report_format=report_format,
        _queue_name="recko:queue",
    )
    await redis.aclose()

    logger.info("report.queued", report_id=str(report_id), run_id=str(run_id))

    return ORJSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"report_id": str(report_id), "status": "queued"},
    )


@router.get(
    "/{run_id}",
    summary="List all reports for a reconciliation run",
)
async def list_reports(
    run_id: uuid.UUID,
    current_user: CurrentUser,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        reports = await conn.fetch(
            """
            SELECT id, rtype, rformat, status, file_size_bytes, generated_at, expires_at
            FROM reports
            WHERE run_id = $1 AND client_id = $2
            ORDER BY generated_at DESC
            """,
            run_id, current_user.client_id,
        )
    return ORJSONResponse({"reports": [dict(r) for r in reports]})


@router.get(
    "/{run_id}/{report_id}/download",
    summary="Get a signed download URL for a report",
    description="Returns a 15-minute signed URL for secure report download.",
)
async def download_report(
    run_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: CurrentUser,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        report = await conn.fetchrow(
            """
            SELECT id, storage_path, rformat, status
            FROM reports
            WHERE id = $1 AND run_id = $2 AND client_id = $3
            """,
            report_id, run_id, current_user.client_id,
        )

    if not report:
        raise NotFoundError(f"Report {report_id} not found.")

    if report["status"] != "ready":
        return ORJSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": report["status"], "message": "Report is not ready yet."},
        )

    # Parse bucket and path from storage_path
    storage_path: str = report["storage_path"]
    bucket, path = storage_path.split("/", 1)
    signed_url = await get_signed_url(bucket, path)

    return ORJSONResponse({
        "download_url": signed_url,
        "format": report["rformat"],
        "expires_in_seconds": settings.SIGNED_URL_EXPIRY_SECONDS,
    })
