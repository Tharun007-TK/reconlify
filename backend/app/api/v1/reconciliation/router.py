"""
Reconciliation API router.
Exposes run status, mismatch records, duplicates, and resolution actions.
"""
from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Query, status
from fastapi.responses import ORJSONResponse

from app.api.v1.reconciliation.schemas import (
    MismatchListResponse,
    MismatchResolveRequest,
    RunDetailResponse,
    RunSummaryResponse,
)
from app.core.exceptions import NotFoundError
from app.core.security import CurrentUser, UserRole
from app.db.repositories.mismatches_repo import MismatchesRepository
from app.db.repositories.runs_repo import RunsRepository
from app.db.session import db_pool

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get(
    "/runs",
    summary="List reconciliation runs for a project",
)
async def list_runs(
    current_user: CurrentUser,
    project_id: Annotated[uuid.UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = RunsRepository(conn)
        runs = await repo.list_for_project(
            project_id, current_user.client_id, limit=limit, offset=offset
        )
    return ORJSONResponse({"runs": [dict(r) for r in runs], "limit": limit, "offset": offset})


@router.get(
    "/runs/{run_id}",
    summary="Get reconciliation run detail",
)
async def get_run(
    run_id: uuid.UUID,
    current_user: CurrentUser,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = RunsRepository(conn)
        run = await repo.get_by_id(run_id, current_user.client_id)

    if not run:
        raise NotFoundError(f"Reconciliation run {run_id} not found.")

    return ORJSONResponse(dict(run))


@router.get(
    "/runs/{run_id}/mismatches",
    summary="List mismatch records for a run",
    description="Returns paginated unmatched records, optionally filtered by category and status.",
)
async def list_mismatches(
    run_id: uuid.UUID,
    current_user: CurrentUser,
    category: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = MismatchesRepository(conn)
        records = await repo.list_for_run(
            run_id, current_user.client_id,
            category=category,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
        total = await repo.count_for_run(
            run_id, current_user.client_id,
            category=category,
            status=status_filter,
        )
        summary = await repo.category_summary(run_id, current_user.client_id)

    return ORJSONResponse({
        "records": [dict(r) for r in records],
        "total": total,
        "limit": limit,
        "offset": offset,
        "category_summary": [dict(s) for s in summary],
    })


@router.patch(
    "/runs/{run_id}/mismatches/{record_id}",
    summary="Resolve or annotate a mismatch record",
)
async def resolve_mismatch(
    run_id: uuid.UUID,
    record_id: uuid.UUID,
    current_user: CurrentUser,
    body: MismatchResolveRequest,
) -> ORJSONResponse:
    current_user.require_role(UserRole.AUDITOR)

    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = MismatchesRepository(conn)
        updated = await repo.resolve(
            record_id,
            current_user.client_id,
            status=body.status,
            note=body.note,
            resolved_by=current_user.user_id,
        )

    if not updated:
        raise NotFoundError(f"Mismatch record {record_id} not found.")

    logger.info(
        "mismatch.resolved",
        record_id=str(record_id),
        new_status=body.status,
        resolved_by=str(current_user.user_id),
    )
    return ORJSONResponse(dict(updated))


@router.get(
    "/runs/{run_id}/duplicates",
    summary="List duplicate records for a run",
)
async def list_duplicates(
    run_id: uuid.UUID,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        records = await conn.fetch(
            """
            SELECT * FROM duplicate_records
            WHERE run_id = $1 AND client_id = $2
            ORDER BY similarity_score DESC, detected_at ASC
            LIMIT $3 OFFSET $4
            """,
            run_id, current_user.client_id, limit, offset,
        )
    return ORJSONResponse({"records": [dict(r) for r in records], "limit": limit, "offset": offset})
