"""
Repository for uploads table.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from app.db.repositories.base import BaseRepository


class UploadsRepository(BaseRepository):

    async def create(
        self,
        *,
        project_id: UUID,
        client_id: UUID,
        uploaded_by: UUID,
        file_type: str,
        original_filename: str,
        storage_path: str,
        file_size_bytes: int,
        mime_type: str,
    ) -> asyncpg.Record:
        return await self.fetch_one(
            """
            INSERT INTO uploads
                (project_id, client_id, uploaded_by, file_type,
                 original_filename, storage_path, file_size_bytes, mime_type, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'uploaded')
            RETURNING *
            """,
            project_id, client_id, uploaded_by, file_type,
            original_filename, storage_path, file_size_bytes, mime_type,
        )

    async def get_by_id(self, upload_id: UUID, client_id: UUID) -> asyncpg.Record | None:
        return await self.fetch_one(
            "SELECT * FROM uploads WHERE id = $1 AND client_id = $2",
            upload_id, client_id,
        )

    async def update_parse_result(
        self,
        upload_id: UUID,
        *,
        status: str,
        total_rows: int,
        parsed_rows: int,
        error_rows: int,
        parse_errors: list[dict],  # type: ignore[type-arg]
    ) -> None:
        import json
        await self.execute(
            """
            UPDATE uploads SET
                status      = $2,
                total_rows  = $3,
                parsed_rows = $4,
                error_rows  = $5,
                parse_errors = $6::jsonb,
                parsed_at   = NOW()
            WHERE id = $1
            """,
            upload_id, status, total_rows, parsed_rows,
            error_rows, json.dumps(parse_errors),
        )
