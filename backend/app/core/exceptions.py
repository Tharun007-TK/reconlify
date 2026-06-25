"""
Custom exception hierarchy and FastAPI exception handlers.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse

logger = structlog.get_logger(__name__)


# ── Domain exceptions ────────────────────────────────────────────────────────

class ReckoBaseError(Exception):
    """Base for all application errors."""
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: Any = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)


class AuthenticationError(ReckoBaseError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "AUTHENTICATION_ERROR"


class AuthorizationError(ReckoBaseError):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "AUTHORIZATION_ERROR"


class NotFoundError(ReckoBaseError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"


class ConflictError(ReckoBaseError):
    status_code = status.HTTP_409_CONFLICT
    error_code = "CONFLICT"


class ValidationError(ReckoBaseError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "VALIDATION_ERROR"


class UploadError(ReckoBaseError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "UPLOAD_ERROR"


class ParseError(ReckoBaseError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "PARSE_ERROR"


class ReconlifyError(ReckoBaseError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "RECONLIFY_ERROR"


class StorageError(ReckoBaseError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "STORAGE_ERROR"


class TenantLimitError(ReckoBaseError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    error_code = "TENANT_LIMIT_EXCEEDED"


class ReportGenerationError(ReckoBaseError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "REPORT_GENERATION_ERROR"


# ── Response builder ──────────────────────────────────────────────────────────

def _error_response(
    status_code: int,
    error_code: str,
    message: str,
    detail: Any = None,
) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error_code,
                "message": message,
                "detail": detail,
            }
        },
    )


# ── Exception handlers ────────────────────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(ReckoBaseError)
    async def recko_error_handler(request: Request, exc: ReckoBaseError) -> ORJSONResponse:
        logger.warning(
            "recko.error",
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
        )
        return _error_response(exc.status_code, exc.error_code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> ORJSONResponse:
        logger.warning("validation.error", errors=exc.errors(), path=request.url.path)
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "VALIDATION_ERROR",
            "Request validation failed",
            exc.errors(),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> ORJSONResponse:
        logger.exception("unhandled.error", path=request.url.path, exc_info=exc)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "An unexpected error occurred. Our team has been notified.",
        )
