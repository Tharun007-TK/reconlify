"""Vendors API router."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import ORJSONResponse

from app.core.security import CurrentUser
from app.db.repositories.vendors_repo import VendorsRepository
from app.db.session import db_pool

router = APIRouter()


@router.get("", summary="List vendors for the current client")
async def list_vendors(
    current_user: CurrentUser,
    risk_level: Annotated[str | None, Query(pattern="^(low|medium|high|critical)$")] = None,
    search: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = VendorsRepository(conn)
        vendors = await repo.list_for_client(
            current_user.client_id,
            risk_level=risk_level,
            search=search,
            limit=limit,
            offset=offset,
        )
    return ORJSONResponse({
        "vendors": [dict(v) for v in vendors],
        "limit": limit,
        "offset": offset,
    })


@router.get("/{vendor_id}/runs", summary="Get per-run stats for a vendor")
async def get_vendor_run_stats(
    vendor_id: uuid.UUID,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        records = await conn.fetch(
            """
            SELECT vrs.*, v.name, v.gstin
            FROM vendor_run_stats vrs
            JOIN vendors v ON v.id = vrs.vendor_id
            WHERE vrs.vendor_id = $1 AND vrs.client_id = $2
            ORDER BY vrs.computed_at DESC
            LIMIT $3
            """,
            vendor_id, current_user.client_id, limit,
        )
    return ORJSONResponse({"stats": [dict(r) for r in records]})


@router.get("/runs/{run_id}", summary="Vendor analysis for a specific run")
async def get_run_vendor_analysis(
    run_id: uuid.UUID,
    current_user: CurrentUser,
) -> ORJSONResponse:
    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = VendorsRepository(conn)
        stats = await repo.get_run_stats(run_id, current_user.client_id)
    return ORJSONResponse({"vendor_stats": [dict(s) for s in stats]})
