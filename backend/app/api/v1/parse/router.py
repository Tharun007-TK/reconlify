"""
Parse API endpoint — exposes the GST parser service over HTTP.
Accepts a multipart file upload and returns a structured ParseResult JSON.
"""
from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import ORJSONResponse

from app.core.exceptions import UploadError
from app.core.security import CurrentUser
from app.services.parser.gst_parser import parse_gst_file
from app.services.parser.normalizer import NormalizationConfig
from app.services.parser.schemas import FileType

router = APIRouter()
logger = structlog.get_logger(__name__)

MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post(
    "/parse",
    status_code=status.HTTP_200_OK,
    summary="Parse a GST file and return normalized records",
    description="""
Upload a Purchase Register or GSTR-2B file for parsing.

**Supported formats:**
- Excel: `.xlsx`, `.xls`
- CSV: `.csv`
- JSON: `.json` (GSTN portal format)

**Auto-detection:** File type is detected automatically from columns and structure.
Pass `file_type` to override detection.

**Response:** Returns a full `ParseResult` with normalized records, column mapping details, and data quality issues.
    """,
    tags=["Parse"],
)
async def parse_file(
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(description="GST file to parse")],
    file_type: Annotated[
        str | None,
        Form(description="Override auto-detection: 'purchase_register' | 'gstr_2b'"),
    ] = None,
    include_records: Annotated[
        bool,
        Query(description="Whether to include full records in response (default: true)"),
    ] = True,
    skip_invalid_gstin: Annotated[
        bool,
        Query(description="Skip rows with invalid GSTIN instead of including them as warnings"),
    ] = False,
) -> ORJSONResponse:
    if not file.filename:
        raise UploadError("Filename is required.")

    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise UploadError(
            f"File size {len(content) / 1024 / 1024:.1f}MB exceeds the 25MB limit."
        )
    if len(content) == 0:
        raise UploadError("File is empty.")

    # Resolve optional file type hint
    hint: FileType | None = None
    if file_type:
        try:
            hint = FileType(file_type)
        except ValueError:
            raise UploadError(
                f"Invalid file_type '{file_type}'. Use 'purchase_register' or 'gstr_2b'."
            )

    salt = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    result = parse_gst_file(
        content,
        file.filename,
        run_id=run_id,
        client_id=str(current_user.client_id),
        salt=salt,
        file_type_hint=hint,
        normalization_config=NormalizationConfig(
            skip_on_missing_gstin=True,
            skip_on_missing_invoice_no=True,
            skip_on_invalid_gstin=skip_invalid_gstin,
            row_hash_salt=salt,
        ),
    )

    logger.info(
        "parse.endpoint.done",
        filename=file.filename,
        detected=result.detected_file_type,
        parsed=result.parsed_rows,
        skipped=result.skipped_rows,
    )

    # Optionally strip records from response (summary-only mode)
    response_data = result.model_dump()
    if not include_records:
        response_data.pop("records", None)

    return ORJSONResponse(content=response_data)


@router.post(
    "/parse/summary",
    status_code=status.HTTP_200_OK,
    summary="Parse a GST file and return summary only (no individual records)",
    tags=["Parse"],
)
async def parse_file_summary(
    current_user: CurrentUser,
    file: Annotated[UploadFile, File()],
    file_type: Annotated[str | None, Form()] = None,
) -> ORJSONResponse:
    """Same as /parse but returns only the summary dict — faster for large files."""
    if not file.filename:
        raise UploadError("Filename is required.")

    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise UploadError("File exceeds 25MB limit.")

    hint = FileType(file_type) if file_type else None
    salt = str(uuid.uuid4())

    result = parse_gst_file(
        content, file.filename,
        run_id=str(uuid.uuid4()),
        client_id=str(current_user.client_id),
        salt=salt,
        file_type_hint=hint,
    )

    return ORJSONResponse(content=result.to_summary())
