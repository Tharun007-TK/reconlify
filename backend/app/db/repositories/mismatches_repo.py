"""
Repository for unmatched_records (mismatches) table.
"""
from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from app.db.repositories.base import BaseRepository


class MismatchesRepository(BaseRepository):

    async def bulk_insert(self, records: list[dict]) -> int:  # type: ignore[type-arg]
        """High-throughput bulk insert using COPY protocol."""
        if not records:
            return 0

        cols = [
            "run_id", "client_id", "source", "invoice_number", "gstin_supplier",
            "supplier_name", "invoice_date", "taxable_value", "igst", "cgst",
            "sgst", "cess", "category", "mismatch_fields", "status", "row_hash",
        ]

        rows = [
            (
                r["run_id"], r["client_id"], r["source"], r["invoice_number"],
                r["gstin_supplier"], r.get("supplier_name"), r.get("invoice_date"),
                r.get("taxable_value"), r.get("igst"), r.get("cgst"),
                r.get("sgst"), r.get("cess", 0), r["category"],
                r.get("mismatch_fields", []), "open", r["row_hash"],
            )
            for r in records
        ]

        result = await self._conn.copy_records_to_table(
            "unmatched_records",
            records=rows,
            columns=cols,
        )
        return len(rows)

    async def list_for_run(
        self,
        run_id: UUID,
        client_id: UUID,
        *,
        category: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        conditions = ["run_id = $1", "client_id = $2"]
        params: list[object] = [run_id, client_id]
        idx = 3

        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        return await self.fetch_many(
            f"""
            SELECT * FROM unmatched_records
            WHERE {where}
            ORDER BY itc_impact DESC, created_at ASC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
        )

    async def count_for_run(
        self,
        run_id: UUID,
        client_id: UUID,
        category: str | None = None,
        status: str | None = None,
    ) -> int:
        conditions = ["run_id = $1", "client_id = $2"]
        params: list[object] = [run_id, client_id]
        idx = 3

        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where = " AND ".join(conditions)
        return await self.fetch_val(
            f"SELECT COUNT(*) FROM unmatched_records WHERE {where}", *params
        )

    async def resolve(
        self,
        record_id: UUID,
        client_id: UUID,
        *,
        status: str,
        note: str | None,
        resolved_by: UUID,
    ) -> asyncpg.Record | None:
        return await self.fetch_one(
            """
            UPDATE unmatched_records
            SET status = $3, resolution_note = $4, resolved_by = $5, resolved_at = NOW()
            WHERE id = $1 AND client_id = $2
            RETURNING *
            """,
            record_id, client_id, status, note, resolved_by,
        )

    async def category_summary(
        self, run_id: UUID, client_id: UUID
    ) -> list[asyncpg.Record]:
        return await self.fetch_many(
            """
            SELECT
                category,
                COUNT(*)              AS count,
                SUM(itc_impact)       AS total_itc_impact,
                COUNT(*) FILTER (WHERE status = 'open') AS open_count
            FROM unmatched_records
            WHERE run_id = $1 AND client_id = $2
            GROUP BY category
            ORDER BY total_itc_impact DESC
            """,
            run_id, client_id,
        )
