"""Pydantic schemas for upload endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class UploadedFileInfo(BaseModel):
    upload_id: uuid.UUID
    file_type: str
    original_filename: str
    file_size_bytes: int
    storage_path: str
    status: str


class PresignResponse(BaseModel):
    upload_id: uuid.UUID
    presigned_url: str
    storage_path: str
    expires_in_seconds: int


class UploadConfirmRequest(BaseModel):
    project_id: uuid.UUID = Field(..., description="Project this reconciliation belongs to")
    upload_pr_id: uuid.UUID = Field(..., description="Upload ID of the Purchase Register file")
    upload_2b_id: uuid.UUID = Field(..., description="Upload ID of the GSTR-2B file")
    run_config: dict[str, Any] | None = Field(
        default=None,
        description="Optional run configuration overrides"
    )


class UploadConfirmResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    message: str
