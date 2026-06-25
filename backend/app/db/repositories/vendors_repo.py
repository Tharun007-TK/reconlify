"""
Repository for vendors and vendor_run_stats tables.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from app.db.repositories.base import BaseRepository


class VendorsRepository(BaseRepository):

    async def upsert_vendor(
        self,
        *,
        client_id: UUID,
        gstin: str,
        name: str,
        state_code: str | None = None,
    ) -> asyncpg.Record:
        """Insert or return existing vendor (by client_id + GSTIN)."""
        return await self.fetch_one(
            """
            INSERT INTO vendors (client_id, gstin, name, state_code)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (client_id, gstin) DO UPDATE
                SET name        = EXCLUDED.name,
                    state_code  = COALESCE(EXCLUDED.state_code, vendors.state_code),
                    updated_at  = NOW()
            RETURNING *
            """,
            client_id, gstin, name, state_code,
        )

    async def bulk_upsert_run_stats(
        self,
        stats: list[dict],  # type: ignore[type-arg]
    ) -> None:
        """Upsert per-run vendor stats and refresh cumulative vendor totals."""
        for s in stats:
            await self.execute(
                """
                INSERT INTO vendor_run_stats
                    (vendor_id, run_id, client_id, pr_invoices, gstr2b_invoices,
                     matched_invoices, mismatched_invoices, itc_claimed,
                     itc_matched, itc_at_risk, mismatch_rate, risk_level)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (vendor_id, run_id) DO UPDATE SET
                    pr_invoices         = EXCLUDED.pr_invoices,
                    gstr2b_invoices     = EXCLUDED.gstr2b_invoices,
                    matched_invoices    = EXCLUDED.matched_invoices,
                    mismatched_invoices = EXCLUDED.mismatched_invoices,
                    itc_claimed         = EXCLUDED.itc_claimed,
                    itc_matched         = EXCLUDED.itc_matched,
                    itc_at_risk         = EXCLUDED.itc_at_risk,
                    mismatch_rate       = EXCLUDED.mismatch_rate,
                    risk_level          = EXCLUDED.risk_level
                """,
                s["vendor_id"], s["run_id"], s["client_id"],
                s["pr_invoices"], s["gstr2b_invoices"],
                s["matched_invoices"], s["mismatched_invoices"],
                s["itc_claimed"], s["itc_matched"], s["itc_at_risk"],
                s["mismatch_rate"], s["risk_level"],
            )

        # Refresh cumulative vendor stats
        if stats:
            vendor_ids = list({s["vendor_id"] for s in stats})
            for vid in vendor_ids:
                await self.execute(
                    """
                    UPDATE vendors v SET
                        total_runs              = sub.run_count,
                        total_invoices          = sub.inv_total,
                        cumulative_itc_claimed  = sub.itc_claimed,
                        cumulative_itc_at_risk  = sub.itc_at_risk,
                        avg_mismatch_rate       = sub.avg_rate,
                        risk_level              = CASE
                            WHEN sub.avg_rate > 0.30 OR sub.itc_at_risk > 100000 THEN 'critical'
                            WHEN sub.avg_rate > 0.10 OR sub.itc_at_risk > 10000  THEN 'high'
                            WHEN sub.avg_rate > 0.05 OR sub.itc_at_risk > 1000   THEN 'medium'
                            ELSE 'low'
                        END,
                        updated_at              = NOW()
                    FROM (
                        SELECT
                            COUNT(*)            AS run_count,
                            SUM(pr_invoices)    AS inv_total,
                            SUM(itc_claimed)    AS itc_claimed,
                            SUM(itc_at_risk)    AS itc_at_risk,
                            AVG(mismatch_rate)  AS avg_rate
                        FROM vendor_run_stats WHERE vendor_id = $1
                    ) sub
                    WHERE v.id = $1
                    """,
                    vid,
                )

    async def list_for_client(
        self,
        client_id: UUID,
        risk_level: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        conditions = ["client_id = $1"]
        params: list[object] = [client_id]
        idx = 2

        if risk_level:
            conditions.append(f"risk_level = ${idx}")
            params.append(risk_level)
            idx += 1
        if search:
            conditions.append(f"name_tsv @@ plainto_tsquery('english', ${idx})")
            params.append(search)
            idx += 1

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        return await self.fetch_many(
            f"""
            SELECT * FROM vendors
            WHERE {where}
            ORDER BY cumulative_itc_at_risk DESC, updated_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
        )

    async def get_run_stats(
        self, run_id: UUID, client_id: UUID
    ) -> list[asyncpg.Record]:
        return await self.fetch_many(
            """
            SELECT vrs.*, v.name AS vendor_name, v.gstin AS vendor_gstin,
                   v.gstin_active, v.risk_level AS cumulative_risk
            FROM vendor_run_stats vrs
            JOIN vendors v ON v.id = vrs.vendor_id
            WHERE vrs.run_id = $1 AND vrs.client_id = $2
            ORDER BY vrs.itc_at_risk DESC
            """,
            run_id, client_id,
        )
