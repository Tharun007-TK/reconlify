"""
PostgreSQL repository for reconciliation results.
Persists matched_records, unmatched_records, potential_matches,
and updates reconciliation_runs with final metrics.

Uses asyncpg COPY protocol for high-throughput bulk inserts.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg
import structlog

from app.services.reconciliation.schemas import (
    MatchedRecord,
    PotentialMatch,
    ReconOutput,
    UnmatchedRecord,
)

logger = structlog.get_logger(__name__)


class ReconciliationResultsRepository:
    """
    Stores all outputs of a reconciliation run to PostgreSQL.
    Single responsibility: persistence only — no business logic.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ── Matched records ───────────────────────────────────────────────────────

    async def bulk_insert_matched(
        self,
        run_id: uuid.UUID,
        client_id: uuid.UUID,
        records: list[MatchedRecord],
    ) -> int:
        """
        Bulk-insert matched records using asyncpg COPY.
        Returns number of rows inserted.
        """
        if not records:
            return 0

        rows = [
            (
                uuid.uuid4(),               # id
                run_id,
                client_id,
                r.pr_row_hash,
                r.gstr2b_row_hash,
                r.gstin_supplier,
                r.invoice_number,
                r.invoice_date,
                r.confidence.value,
                r.pr_taxable_value,
                r.gstr2b_taxable_value,
                r.pr_igst,
                r.gstr2b_igst,
                r.pr_cgst,
                r.gstr2b_cgst,
                r.pr_sgst,
                r.gstr2b_sgst,
                r.value_variance,
                r.tax_variance,
                json.dumps([v.model_dump() for v in r.field_variances]),
                json.dumps(r.matched_on),
            )
            for r in records
        ]

        await self._conn.copy_records_to_table(
            "matched_records",
            records=rows,
            columns=[
                "id", "run_id", "client_id",
                "pr_row_hash", "gstr2b_row_hash",
                "gstin_supplier", "invoice_number", "invoice_date",
                "confidence",
                "pr_taxable_value", "gstr2b_taxable_value",
                "pr_igst", "gstr2b_igst",
                "pr_cgst", "gstr2b_cgst",
                "pr_sgst", "gstr2b_sgst",
                "value_variance", "tax_variance",
                "field_variances", "matched_on",
            ],
        )

        logger.info("repo.matched.inserted", run_id=str(run_id), count=len(rows))
        return len(rows)

    # ── Unmatched records ─────────────────────────────────────────────────────

    async def bulk_insert_unmatched(
        self,
        run_id: uuid.UUID,
        client_id: uuid.UUID,
        records: list[UnmatchedRecord],
    ) -> int:
        if not records:
            return 0

        rows = [
            (
                uuid.uuid4(),
                run_id,
                client_id,
                r.row_hash,
                r.source,
                r.gstin_supplier,
                r.invoice_number,
                r.invoice_date,
                r.taxable_value,
                r.igst,
                r.cgst,
                r.sgst,
                r.cess,
                r.total_tax,
                r.itc_impact,
                r.supplier_name,
                r.return_period,
                # Mismatch category will be set by the classifier in a follow-up pass
                None,   # category
                "open", # status
                None,   # mismatch_fields
                None,   # resolution_note
                None,   # resolved_by
            )
            for r in records
        ]

        await self._conn.copy_records_to_table(
            "unmatched_records",
            records=rows,
            columns=[
                "id", "run_id", "client_id",
                "row_hash", "source",
                "gstin_supplier", "invoice_number", "invoice_date",
                "taxable_value", "igst", "cgst", "sgst", "cess", "total_tax",
                "itc_impact", "supplier_name", "return_period",
                "category", "status", "mismatch_fields",
                "resolution_note", "resolved_by",
            ],
        )

        logger.info("repo.unmatched.inserted", run_id=str(run_id), count=len(rows))
        return len(rows)

    # ── Potential matches ─────────────────────────────────────────────────────

    async def bulk_insert_potential(
        self,
        run_id: uuid.UUID,
        client_id: uuid.UUID,
        records: list[PotentialMatch],
    ) -> int:
        if not records:
            return 0

        rows = [
            (
                uuid.uuid4(),
                run_id,
                client_id,
                r.pr_row_hash,
                r.gstr2b_row_hash,
                r.gstin_supplier,
                r.pr_invoice_number,
                r.gstr2b_invoice_number,
                r.similarity_score,
                r.confidence.value,
                json.dumps([v.model_dump() for v in r.field_variances]),
                r.suggested_action,
                "pending",   # review_status
            )
            for r in records
        ]

        await self._conn.copy_records_to_table(
            "potential_matches",
            records=rows,
            columns=[
                "id", "run_id", "client_id",
                "pr_row_hash", "gstr2b_row_hash",
                "gstin_supplier",
                "pr_invoice_number", "gstr2b_invoice_number",
                "similarity_score", "confidence",
                "field_variances", "suggested_action",
                "review_status",
            ],
        )

        logger.info("repo.potential.inserted", run_id=str(run_id), count=len(rows))
        return len(rows)

    # ── Run metadata update ───────────────────────────────────────────────────

    async def update_run_with_results(
        self,
        run_id: uuid.UUID,
        client_id: uuid.UUID,
        output: ReconOutput,
        metrics_dict: dict[str, Any],
    ) -> None:
        """Update reconciliation_runs with final counts and metrics."""
        m = output.metrics
        unmatched_pr_count = sum(1 for u in output.unmatched if u.source == "PURCHASE_REGISTER")
        unmatched_2b_count = sum(1 for u in output.unmatched if u.source == "GSTR_2B")

        await self._conn.execute(
            """
            UPDATE reconciliation_runs SET
                status                = 'completed',
                matched_count         = $3,
                unmatched_pr_count    = $4,
                unmatched_2b_count    = $5,
                duplicate_count       = $6,
                total_pr_records      = $7,
                total_2b_records      = $8,
                total_itc_claimed     = $9,
                itc_matched           = $10,
                itc_at_risk           = $11,
                run_metrics           = $12::jsonb,
                engine_name           = $13,
                completed_at          = NOW()
            WHERE id = $1 AND client_id = $2
            """,
            run_id,
            client_id,
            m.matched_count,
            unmatched_pr_count,
            unmatched_2b_count,
            0,  # Duplicates handled by duplicate_detector separately
            m.pr_input_count,
            m.gstr2b_input_count,
            m.total_itc_claimed,
            m.itc_matched,
            m.itc_at_risk,
            json.dumps(metrics_dict),
            m.engine_name,
        )
        logger.info("repo.run_updated", run_id=str(run_id))

    # ── Convenience: persist full ReconOutput ─────────────────────────────────

    async def persist_output(
        self,
        run_id: uuid.UUID,
        client_id: uuid.UUID,
        output: ReconOutput,
        metrics_dict: dict[str, Any],
    ) -> dict[str, int]:
        """
        Persist all reconciliation results in a single transaction.
        Returns insertion counts per table.
        """
        async with self._conn.transaction():
            n_matched   = await self.bulk_insert_matched(run_id, client_id, output.matched)
            n_unmatched = await self.bulk_insert_unmatched(run_id, client_id, output.unmatched)
            n_potential = await self.bulk_insert_potential(run_id, client_id, output.potential_matches)
            await self.update_run_with_results(run_id, client_id, output, metrics_dict)

        logger.info(
            "repo.output_persisted",
            run_id=str(run_id),
            matched=n_matched,
            unmatched=n_unmatched,
            potential=n_potential,
        )

        return {
            "matched":   n_matched,
            "unmatched": n_unmatched,
            "potential": n_potential,
        }
