"""
Upload API router.
Handles file validation, Supabase Storage presigned URLs, and upload confirmation.
"""
from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import ORJSONResponse

from app.api.v1.upload.schemas import (
    PresignResponse,
    UploadConfirmRequest,
    UploadConfirmResponse,
    UploadedFileInfo,
)
from app.config import settings
from app.core.exceptions import UploadError, ValidationError
from app.core.security import CurrentUser, UserRole, verify_turnstile_token
from app.db.repositories.uploads_repo import UploadsRepository
from app.db.repositories.runs_repo import RunsRepository
from app.db.session import db_pool
from app.services.storage.supabase_storage import (
    build_upload_path,
    get_signed_url,
    upload_file,
)

router = APIRouter()
logger = structlog.get_logger(__name__)

ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv", "json"}


def _validate_file(file: UploadFile) -> None:
    """Validate MIME type and extension before accepting upload."""
    if file.content_type not in settings.ALLOWED_MIME_TYPES:
        raise UploadError(
            f"File type '{file.content_type}' is not allowed. "
            f"Accepted: xlsx, xls, csv, json"
        )
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError(f"File extension '.{ext}' not supported.")


@router.post(
    "/direct",
    response_model=UploadedFileInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file directly (server-side)",
    description=(
        "Upload PR or GSTR-2B file directly via multipart form. "
        "For large files, prefer the presign → direct-to-storage flow."
    ),
    dependencies=[Depends(verify_turnstile_token)],
)
async def upload_direct(
    current_user: CurrentUser,
    project_id: Annotated[uuid.UUID, Form()],
    file_type: Annotated[str, Form(pattern="^(purchase_register|gstr_2b)$")],
    file: Annotated[UploadFile, File()],
) -> ORJSONResponse:
    current_user.require_role(UserRole.AUDITOR)

    if not file.filename:
        raise UploadError("Filename is required.")

    _validate_file(file)

    size = file.size or 0
    if size > settings.max_upload_size_bytes:
        raise UploadError(
            f"File size {size / 1024 / 1024:.1f}MB exceeds "
            f"maximum allowed {settings.MAX_UPLOAD_SIZE_MB}MB."
        )

    upload_id = uuid.uuid4()
    salt = str(uuid.uuid4())
    storage_path_key = build_upload_path(
        str(current_user.client_id),
        str(project_id),
        str(upload_id),
        file.filename,
    )

    stored_path = await upload_file(
        settings.STORAGE_BUCKET_UPLOADS,
        storage_path_key,
        file.file,
        file.content_type or "application/octet-stream",
    )

    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        repo = UploadsRepository(conn)
        record = await repo.create(
            project_id=project_id,
            client_id=current_user.client_id,
            uploaded_by=current_user.user_id,
            file_type=file_type,
            original_filename=file.filename,
            storage_path=stored_path,
            file_size_bytes=size,
            mime_type=file.content_type or "application/octet-stream",
        )
        # Store the salt on the record (we use row_hash_salt column)
        await conn.execute(
            "UPDATE uploads SET row_hash_salt = $1 WHERE id = $2",
            salt, record["id"],
        )

    logger.info(
        "upload.direct.ok",
        upload_id=str(record["id"]),
        file_type=file_type,
        size=size,
    )

    return ORJSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "upload_id": str(record["id"]),
            "file_type": file_type,
            "original_filename": file.filename,
            "file_size_bytes": size,
            "storage_path": stored_path,
            "status": "uploaded",
        },
    )


@router.post(
    "/confirm",
    response_model=UploadConfirmResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirm uploads and trigger reconciliation pipeline",
    description=(
        "After both PR and GSTR-2B files are uploaded, confirm them to "
        "create a reconciliation_run and enqueue the parse pipeline."
    ),
    dependencies=[Depends(verify_turnstile_token)],
)
async def confirm_upload(
    current_user: CurrentUser,
    body: UploadConfirmRequest,
) -> ORJSONResponse:
    current_user.require_role(UserRole.AUDITOR)

    async with db_pool.acquire_for_tenant(str(current_user.client_id)) as conn:
        uploads_repo = UploadsRepository(conn)
        runs_repo = RunsRepository(conn)

        # Validate both uploads exist and belong to this client
        pr_upload = await uploads_repo.get_by_id(body.upload_pr_id, current_user.client_id)
        gstr2b_upload = await uploads_repo.get_by_id(body.upload_2b_id, current_user.client_id)

        if not pr_upload:
            raise ValidationError(f"Purchase Register upload {body.upload_pr_id} not found.")
        if not gstr2b_upload:
            raise ValidationError(f"GSTR-2B upload {body.upload_2b_id} not found.")

        if pr_upload["file_type"] != "purchase_register":
            raise ValidationError("upload_pr_id must reference a purchase_register upload.")
        if gstr2b_upload["file_type"] != "gstr_2b":
            raise ValidationError("upload_2b_id must reference a gstr_2b upload.")

        # Create the reconciliation run
        run = await runs_repo.create(
            project_id=body.project_id,
            client_id=current_user.client_id,
            upload_pr_id=body.upload_pr_id,
            upload_2b_id=body.upload_2b_id,
            triggered_by=current_user.user_id,
            created_by=current_user.user_id,
            run_config=body.run_config or {},
        )

    # Enqueue parse task
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await redis.enqueue_job(
        "parse_task",
        run_id=str(run["id"]),
        client_id=str(current_user.client_id),
        upload_pr_id=str(pr_upload["id"]),
        upload_2b_id=str(gstr2b_upload["id"]),
        pr_storage_path=pr_upload["storage_path"],
        gstr2b_storage_path=gstr2b_upload["storage_path"],
        pr_original_filename=pr_upload["original_filename"],
        gstr2b_original_filename=gstr2b_upload["original_filename"],
        pr_salt=pr_upload["row_hash_salt"],
        gstr2b_salt=gstr2b_upload["row_hash_salt"],
        _queue_name="recko:queue",
    )
    await redis.aclose()

    logger.info("upload.confirm.ok", run_id=str(run["id"]))

    return ORJSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "run_id": str(run["id"]),
            "status": "queued",
            "message": "Reconciliation pipeline started. Track status via the runs API.",
        },
    )
