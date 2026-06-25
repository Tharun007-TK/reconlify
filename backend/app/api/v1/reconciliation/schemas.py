"""Pydantic schemas for reconciliation endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunSummaryResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    matched_count: int
    unmatched_pr_count: int
    unmatched_2b_count: int
    duplicate_count: int
    total_itc_claimed: float
    itc_at_risk: float
    itc_matched: float
    created_at: datetime
    completed_at: datetime | None = None


class RunDetailResponse(RunSummaryResponse):
    total_pr_records: int | None
    total_2b_records: int | None
    run_config: dict[str, Any]
    reconlify_version: str | None
    error_detail: dict[str, Any] | None = None


class MismatchRecord(BaseModel):
    id: uuid.UUID
    source: str
    invoice_number: str
    gstin_supplier: str
    supplier_name: str | None
    invoice_date: date | None
    taxable_value: float | None
    igst: float | None
    cgst: float | None
    sgst: float | None
    itc_impact: float | None
    category: str
    mismatch_fields: list[str]
    status: str
    resolution_note: str | None


class MismatchListResponse(BaseModel):
    records: list[MismatchRecord]
    total: int
    limit: int
    offset: int
    category_summary: list[dict[str, Any]]


class MismatchResolveRequest(BaseModel):
    status: Literal["in_review", "resolved", "accepted_variance", "disputed", "escalated"] = Field(
        ..., description="New resolution status"
    )
    note: str | None = Field(None, max_length=1000, description="Resolution note or justification")
