import uuid
from typing import Any

from app.db.repositories.base import BaseRepository

class ReportsRepository(BaseRepository):
    """Repository for fetching full reconciliation run data for reports."""

    async def get_report_data(self, run_id: uuid.UUID, client_id: uuid.UUID) -> dict[str, Any]:
        """Fetch all data related to a run to generate reports."""
        run_summary = await self.conn.fetchrow(
            """
            SELECT id, status, engine_name, matched_count, unmatched_pr_count,
                   unmatched_2b_count, duplicate_count, match_rate, 
                   total_itc_claimed, itc_at_risk, itc_matched,
                   started_at, completed_at
            FROM reconciliation_runs
            WHERE id = $1 AND client_id = $2
            """,
            run_id, client_id
        )

        matched_records = await self.conn.fetch(
            """
            SELECT pr_invoice_number, gstin_supplier, pr_invoice_date, 
                   pr_taxable_value, pr_igst, pr_cgst, pr_sgst,
                   b2_invoice_number, b2_invoice_date, 
                   b2_taxable_value, b2_igst, b2_cgst, b2_sgst
            FROM matched_records
            WHERE run_id = $1 AND client_id = $2
            ORDER BY gstin_supplier, pr_invoice_number
            """,
            run_id, client_id
        )

        unmatched_records = await self.conn.fetch(
            """
            SELECT source, invoice_number, gstin_supplier, invoice_date,
                   taxable_value, igst, cgst, sgst, cess,
                   category, severity, reason, recommended_action
            FROM unmatched_records
            WHERE run_id = $1 AND client_id = $2
            ORDER BY severity DESC, gstin_supplier
            """,
            run_id, client_id
        )

        duplicate_records = await self.conn.fetch(
            """
            SELECT source, invoice_number, gstin_supplier, dtype, similarity_score,
                   diff_fields, status
            FROM duplicate_records
            WHERE run_id = $1 AND client_id = $2
            ORDER BY source, gstin_supplier
            """,
            run_id, client_id
        )

        vendor_stats = await self.conn.fetch(
            """
            SELECT v.gstin, v.name, s.pr_invoices, s.gstr2b_invoices,
                   s.matched_invoices, s.mismatched_invoices, s.mismatch_rate,
                   s.itc_claimed, s.itc_matched, s.itc_at_risk, s.risk_level
            FROM reconciliation_run_vendor_stats s
            JOIN vendors v ON s.vendor_id = v.id
            WHERE s.run_id = $1 AND s.client_id = $2
            ORDER BY s.itc_at_risk DESC
            """,
            run_id, client_id
        )

        return {
            "run": dict(run_summary) if run_summary else {},
            "matched": [dict(r) for r in matched_records],
            "unmatched": [dict(r) for r in unmatched_records],
            "duplicates": [dict(r) for r in duplicate_records],
            "vendors": [dict(r) for r in vendor_stats]
        }
