"""
Repository for reconciliation_runs table.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from app.db.repositories.base import BaseRepository


class RunsRepository(BaseRepository):

    async def create(
        self,
        *,
        project_id: UUID,
        client_id: UUID,
        upload_pr_id: UUID,
        upload_2b_id: UUID,
        triggered_by: UUID,
        created_by: UUID,
        run_config: dict,  # type: ignore[type-arg]
    ) -> asyncpg.Record:
        return await self.fetch_one(
            """
            INSERT INTO reconciliation_runs
                (project_id, client_id, upload_pr_id, upload_2b_id,
                 triggered_by, created_by, run_config, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'queued')
            RETURNING *
            """,
            project_id, client_id, upload_pr_id, upload_2b_id,
            triggered_by, created_by, run_config,
        )

    async def get_by_id(self, run_id: UUID, client_id: UUID) -> asyncpg.Record | None:
        return await self.fetch_one(
            "SELECT * FROM reconciliation_runs WHERE id = $1 AND client_id = $2",
            run_id, client_id,
        )

    async def list_for_project(
        self,
        project_id: UUID,
        client_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        return await self.fetch_many(
            """
            SELECT * FROM reconciliation_runs
            WHERE project_id = $1 AND client_id = $2
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
            """,
            project_id, client_id, limit, offset,
        )

    async def update_status(self, run_id: UUID, status: str) -> None:
        await self.execute(
            """
            UPDATE reconciliation_runs
            SET status = $1,
                started_at = CASE WHEN $1 = 'parsing' AND started_at IS NULL
                                  THEN NOW() ELSE started_at END
            WHERE id = $2
            """,
            status, run_id,
        )

    async def complete(
        self,
        run_id: UUID,
        *,
        matched_count: int,
        unmatched_pr_count: int,
        unmatched_2b_count: int,
        duplicate_count: int,
        total_itc_claimed: float,
        itc_at_risk: float,
        itc_matched: float,
        total_pr_records: int,
        total_2b_records: int,
        reconlify_version: str,
    ) -> None:
        await self.execute(
            """
            UPDATE reconciliation_runs SET
                status              = 'completed',
                matched_count       = $2,
                unmatched_pr_count  = $3,
                unmatched_2b_count  = $4,
                duplicate_count     = $5,
                total_itc_claimed   = $6,
                itc_at_risk         = $7,
                itc_matched         = $8,
                total_pr_records    = $9,
                total_2b_records    = $10,
                reconlify_version   = $11,
                completed_at        = NOW()
            WHERE id = $1
            """,
            run_id, matched_count, unmatched_pr_count, unmatched_2b_count,
            duplicate_count, total_itc_claimed, itc_at_risk, itc_matched,
            total_pr_records, total_2b_records, reconlify_version,
        )

    async def fail(self, run_id: UUID, error_detail: dict) -> None:  # type: ignore[type-arg]
        await self.execute(
            """
            UPDATE reconciliation_runs
            SET status = 'failed', error_detail = $2::jsonb, completed_at = NOW()
            WHERE id = $1
            """,
            run_id, error_detail,
        )
