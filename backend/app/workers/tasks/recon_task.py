"""
ARQ background task: Run Reconciliation Orchestrator, detect duplicates,
and aggregate vendor statistics.
"""
from __future__ import annotations

import uuid
import asyncio
from typing import Any

import structlog

from app.db.repositories.runs_repo import RunsRepository
from app.db.repositories.vendors_repo import VendorsRepository
from app.db.session import db_pool
from app.services.reconciliation.duplicate_detector import detect_all_duplicates
from app.services.reconciliation.orchestrator import ReconciliationOrchestrator
from app.services.reconciliation.schemas import InvoiceRecord, ReconOutput

logger = structlog.get_logger(__name__)


async def recon_task(
    ctx: dict[str, Any],
    *,
    run_id: str,
    client_id: str,
    pr_records: list[dict[str, Any]],
    gstr2b_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    ARQ task: execute full reconciliation pipeline.

    Pipeline:
    1. Parse inputs into Pydantic models
    2. Run Orchestrator (handles engine execution, mismatch classification, DB persistence)
    3. Detect duplicates (exact + fuzzy)
    4. Aggregate vendor-level stats
    5. Update reconciliation_runs with duplicate count
    6. Enqueue report_task + notify_task
    """
    structlog.contextvars.bind_contextvars(run_id=run_id, task="recon_task")
    logger.info("recon_task.start", pr=len(pr_records), gstr2b=len(gstr2b_records))

    async with db_pool.acquire_for_tenant(client_id) as conn:
        runs_repo = RunsRepository(conn)
        vendors_repo = VendorsRepository(conn)

        try:
            await runs_repo.update_status(uuid.UUID(run_id), "reconciling")

            # ── 1. Parse inputs ───────────────────────────────────────────────
            pr_models = [InvoiceRecord(**r) for r in pr_records]
            b2_models = [InvoiceRecord(**r) for r in gstr2b_records]

            # ── 2. Run Orchestrator ───────────────────────────────────────────
            orchestrator = ReconciliationOrchestrator(conn)
            output: ReconOutput = await orchestrator.run(
                run_id=run_id,
                client_id=client_id,
                pr_records=pr_models,
                gstr2b_records=b2_models,
            )

            # ── 3. Detect duplicates ──────────────────────────────────────────
            await runs_repo.update_status(uuid.UUID(run_id), "analyzing")
            duplicate_pairs = await asyncio.to_thread(
                detect_all_duplicates,
                pr_records, gstr2b_records, run_id, client_id
            )

            if duplicate_pairs:
                await conn.executemany(
                    """
                    INSERT INTO duplicate_records
                        (run_id, client_id, source, record_id_a, record_id_b,
                         invoice_number, gstin_supplier, dtype, similarity_score,
                         diff_fields, status)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11)
                    ON CONFLICT (run_id, record_id_a, record_id_b) DO NOTHING
                    """,
                    [
                        (
                            dp["run_id"], dp["client_id"], dp["source"],
                            dp["record_id_a"], dp["record_id_b"],
                            dp["invoice_number"], dp["gstin_supplier"],
                            dp["dtype"], dp["similarity_score"],
                            str(dp["diff_fields"]).replace("'", '"'),
                            dp["status"],
                        )
                        for dp in duplicate_pairs
                    ],
                )

                # Update the duplicate_count in reconciliation_runs
                await conn.execute(
                    """
                    UPDATE reconciliation_runs
                    SET duplicate_count = $1
                    WHERE id = $2 AND client_id = $3
                    """,
                    len(duplicate_pairs),
                    uuid.UUID(run_id),
                    uuid.UUID(client_id),
                )

            # ── 4. Aggregate vendor stats ─────────────────────────────────────
            vendor_stats = await _compute_vendor_stats(
                pr_models, b2_models, output, run_id, client_id
            )

            for vs in vendor_stats:
                vendor_rec = await vendors_repo.upsert_vendor(
                    client_id=uuid.UUID(client_id),
                    gstin=vs["gstin"],
                    name=vs["name"],
                )
                vs["vendor_id"] = vendor_rec["id"]
                vs["run_id"] = uuid.UUID(run_id)
                vs["client_id"] = uuid.UUID(client_id)

            if vendor_stats:
                await vendors_repo.bulk_upsert_run_stats(vendor_stats)

            logger.info(
                "recon_task.success",
                run_id=run_id,
                matched=len(output.matched),
                unmatched=len(output.unmatched),
                potential=len(output.potential_matches),
                duplicates=len(duplicate_pairs),
            )

            # ── 5. Chain report + notify tasks ────────────────────────────────
            await ctx["queue"].enqueue_job("report_task", run_id=run_id, client_id=client_id)
            await ctx["queue"].enqueue_job("notify_task", run_id=run_id, client_id=client_id)

            return {"status": "success", "matched": len(output.matched)}

        except Exception as exc:
            logger.exception("recon_task.failed", run_id=run_id)
            await runs_repo.fail(
                uuid.UUID(run_id),
                {"stage": "reconciliation", "error": str(exc), "type": type(exc).__name__},
            )
            raise


async def _compute_vendor_stats(
    pr_records: list[InvoiceRecord],
    gstr2b_records: list[InvoiceRecord],
    output: ReconOutput,
    run_id: str,
    client_id: str,
) -> list[dict[str, Any]]:
    """Aggregate per-vendor statistics from reconciliation results."""
    from collections import defaultdict

    vendors: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "gstin": "", "name": "", "pr_invoices": 0, "gstr2b_invoices": 0,
        "matched_invoices": 0, "mismatched_invoices": 0,
        "itc_claimed": 0.0, "itc_matched": 0.0, "itc_at_risk": 0.0,
    })

    for r in pr_records:
        gstin = str(r.gstin_supplier).upper().strip()
        v = vendors[gstin]
        v["gstin"] = gstin
        v["name"] = str(r.supplier_name or gstin)
        v["pr_invoices"] += 1
        v["itc_claimed"] += r.igst + r.cgst + r.sgst

    for r in gstr2b_records:
        gstin = str(r.gstin_supplier).upper().strip()
        vendors[gstin]["gstr2b_invoices"] += 1

    for m in output.matched:
        gstin = str(m.gstin_supplier).upper().strip()
        vendors[gstin]["matched_invoices"] += 1
        vendors[gstin]["itc_matched"] += m.pr_igst + m.pr_cgst + m.pr_sgst

    for u in output.unmatched:
        if u.source == "PURCHASE_REGISTER":
            gstin = str(u.gstin_supplier).upper().strip()
            vendors[gstin]["mismatched_invoices"] += 1
            vendors[gstin]["itc_at_risk"] += u.igst + u.cgst + u.sgst

    for p in output.potential_matches:
        gstin = str(p.gstin_supplier).upper().strip()
        # Depending on business rules, a potential match is often considered at-risk until accepted
        # However, for simplicity here, we only add it to mismatched count
        vendors[gstin]["mismatched_invoices"] += 1

    result = []
    for gstin, v in vendors.items():
        total = v["pr_invoices"]
        rate = v["mismatched_invoices"] / total if total > 0 else 0.0
        itc_risk = v["itc_at_risk"]

        if rate > 0.30 or itc_risk > 100_000:
            risk = "critical"
        elif rate > 0.10 or itc_risk > 10_000:
            risk = "high"
        elif rate > 0.05 or itc_risk > 1_000:
            risk = "medium"
        else:
            risk = "low"

        result.append({**v, "mismatch_rate": round(rate, 4), "risk_level": risk})

    return result
