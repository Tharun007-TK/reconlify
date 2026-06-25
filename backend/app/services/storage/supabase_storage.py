"""
Supabase Storage wrapper with upload, download, and signed URL generation.
"""
from __future__ import annotations

import structlog
from typing import IO
from supabase import AsyncClient, acreate_client

from app.config import settings
from app.core.exceptions import StorageError

logger = structlog.get_logger(__name__)

_client: AsyncClient | None = None


async def _get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(
            str(settings.SUPABASE_URL),
            settings.SUPABASE_SERVICE_ROLE_KEY,
        )
    return _client


async def upload_file(
    bucket: str,
    path: str,
    data: bytes | IO[bytes],
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload bytes to Supabase Storage.

    Args:
        bucket:       Bucket name (e.g. 'uploads', 'reports')
        path:         Object path within bucket
        data:         File bytes or file-like object
        content_type: MIME type

    Returns:
        Storage path (bucket/path) for DB storage
    """
    client = await _get_client()
    try:
        response = await client.storage.from_(bucket).upload(
            path=path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        full_path = f"{bucket}/{path}"
        logger.info("storage.upload_ok", path=full_path)
        return full_path
    except Exception as exc:
        logger.error("storage.upload_failed", path=path, error=str(exc))
        raise StorageError(f"Failed to upload to storage: {exc}") from exc


async def download_file(bucket: str, path: str) -> bytes:
    """Download a file from Supabase Storage and return raw bytes."""
    client = await _get_client()
    try:
        data: bytes = await client.storage.from_(bucket).download(path)
        logger.info("storage.download_ok", path=path, size=len(data))
        return data
    except Exception as exc:
        logger.error("storage.download_failed", path=path, error=str(exc))
        raise StorageError(f"Failed to download from storage: {exc}") from exc


async def get_signed_url(
    bucket: str,
    path: str,
    expires_in: int | None = None,
) -> str:
    """
    Generate a short-lived signed URL for secure file access.

    Args:
        bucket:     Storage bucket name
        path:       Object path within bucket
        expires_in: TTL in seconds (defaults to settings value)

    Returns:
        Signed URL string
    """
    ttl = expires_in or settings.SIGNED_URL_EXPIRY_SECONDS
    client = await _get_client()
    try:
        response = await client.storage.from_(bucket).create_signed_url(
            path=path,
            expires_in=ttl,
        )
        url: str = response["signedURL"]
        logger.info("storage.signed_url_ok", path=path, expires_in=ttl)
        return url
    except Exception as exc:
        logger.error("storage.signed_url_failed", path=path, error=str(exc))
        raise StorageError(f"Failed to generate signed URL: {exc}") from exc


async def delete_file(bucket: str, path: str) -> None:
    """Delete a file from storage."""
    client = await _get_client()
    try:
        await client.storage.from_(bucket).remove([path])
        logger.info("storage.delete_ok", path=path)
    except Exception as exc:
        logger.warning("storage.delete_failed", path=path, error=str(exc))


def build_upload_path(client_id: str, project_id: str, upload_id: str, filename: str) -> str:
    return f"{client_id}/{project_id}/{upload_id}/{filename}"


def build_report_path(client_id: str, run_id: str, report_id: str, filename: str) -> str:
    return f"{client_id}/{run_id}/{report_id}/{filename}"
