"""
ARQ background task: Run Reconciliation Orchestrator, detect duplicates,
and aggregate vendor statistics.
"""
from __future__ import annotations

import json
import shutil
import uuid
import asyncio
from pathlib import Path
from typing import Any

import structlog

from app.db.repositories.runs_repo import RunsRepository
from app.db.repositories.vendors_repo import VendorsRepository
from app.db.session import db_pool
from app.services.reconciliation.duplicate_detector import detect_all_duplicates
from app.services.reconciliation.mismatch_classifier import (
    MismatchCategory,
    batch_classify,
    classify_missing_pr,
    classify_missing_2b,
    classify_partial_match,
)
from app.services.reconciliation.orchestrator import ReconciliationOrchestrator
from app.services.reconciliation.schemas import InvoiceRecord, ReconOutput

logger = structlog.get_logger(__name__)


# ── JSON report parser ─────────────────────────────────────────────────────────

def _safe_float(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_report_json(report: dict[str, Any]) -> dict[str, Any]:
    """
    Parse a reconlify report.json into classified mismatch buckets.

    Expected report.json structure:
    {
      "summary": {
        "matched_count": int,
        "missing_source": int,   # rows in source (PR) with no match in target (GSTR-2B)
        "missing_target": int,   # rows in target (GSTR-2B) with no match in source (PR)
        "column_mismatches": int
      },
      "results": {
        "missing_in_target": [ { gstin_supplier, invoice_number, taxable_value, igst, … } ],
        "missing_in_source": [ { … } ],
        "column_mismatches": [
          {
            "source_row": { … },
            "target_row": { … },
            "mismatched_columns": [ "taxable_value", "igst", "invoice_date", … ]
          }
        ]
      }
    }

    Returns:
        {
          "summary":         dict   (from report["summary"]),
          "unmatched_pr":    list   (missing_in_target → MISSING_IN_GSTR2B category)
          "unmatched_2b":    list   (missing_in_source → MISSING_IN_PR category)
          "partial_matches": list   (column_mismatches → amount / date / inv-no categories)
        }
    """
    results = report.get("results", {})
    summary = report.get("summary", {})

    # ── 1. missing_in_target: PR rows absent from GSTR-2B ─────────────────────
    # "missing_in_target" means the source row (PR) has no counterpart in the
    # target file (GSTR-2B).  Category → MISSING_IN_GSTR2B.
    unmatched_pr: list[dict[str, Any]] = results.get("missing_in_target", [])

    # ── 2. missing_in_source: GSTR-2B rows absent from PR ────────────────────
    # "missing_in_source" means the target row (GSTR-2B) has no counterpart in
    # the source file (PR).  Category → MISSING_IN_PR.
    unmatched_2b: list[dict[str, Any]] = results.get("missing_in_source", [])

    # ── 3. column_mismatches: rows matched on keys but with field divergences ──
    # Each entry contains source_row (PR), target_row (GSTR-2B), and a list of
    # column names that differ.  Map column names → field_variances list.
    partial_matches: list[dict[str, Any]] = []
    for cm in results.get("column_mismatches", []):
        pr_row   = cm.get("source_row", {})
        b2_row   = cm.get("target_row", {})
        columns  = cm.get("mismatched_columns", [])

        # Normalise column names to internal field names
        field_variances = []
        for col in columns:
            col_lower = col.lower()
            if col_lower in ("taxable_value", "taxablevalue", "taxable"):
                field_variances.append({"field": "taxable_value"})
            elif col_lower in ("igst",):
                field_variances.append({"field": "igst"})
            elif col_lower in ("cgst",):
                field_variances.append({"field": "cgst"})
            elif col_lower in ("sgst",):
                field_variances.append({"field": "sgst"})
            elif col_lower in ("invoice_date", "invoicedate", "date"):
                field_variances.append({"field": "invoice_date"})
            elif col_lower in ("invoice_number", "invoicenumber", "invoice_no"):
                field_variances.append({"field": "invoice_number"})
            elif col_lower in ("gstin", "gstin_supplier", "supplier_gstin"):
                field_variances.append({"field": "gstin_supplier"})
            else:
                field_variances.append({"field": col_lower})

        partial_matches.append({
            "pr":             pr_row,
            "gstr2b":         b2_row,
            "field_variances": field_variances,
        })

    logger.debug(
        "recon_task.report_parsed",
        matched=summary.get("matched_count", "?"),
        missing_source=summary.get("missing_source", len(unmatched_pr)),
        missing_target=summary.get("missing_target", len(unmatched_2b)),
        column_mismatches=summary.get("column_mismatches", len(partial_matches)),
    )

    return {
        "summary":         summary,
        "unmatched_pr":    unmatched_pr,
        "unmatched_2b":    unmatched_2b,
        "partial_matches": partial_matches,
    }


def _load_report_json(job_id: str) -> dict[str, Any]:
    """
    Read /tmp/reconlify/{job_id}/report.json from disk.

    The /tmp/reconlify mount is the persistent Fly.io volume path.
    Returns an empty dict (gracefully) if the file does not exist or is
    malformed — the orchestrator’s own output already provides the matched
    count; this is supplemental enrichment for mismatch classification.
    """
    report_path = Path("/tmp/reconlify") / job_id / "report.json"
    if not report_path.exists():
        logger.warning("recon_task.report_not_found", path=str(report_path))
        return {}
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("recon_task.report_load_error", path=str(report_path), error=str(exc))
        return {}


# ── Main task ──────────────────────────────────────────────────────────────────

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
    2. Run Orchestrator (engine execution + DB persistence)
    3. Parse report.json for detailed mismatch classification
    4. Detect duplicates (exact + fuzzy)
    5. Aggregate vendor-level stats
    6. Update reconciliation_runs with duplicate count
    7. Enqueue report_task + notify_task
    """
    structlog.contextvars.bind_contextvars(run_id=run_id, task="recon_task")
    logger.info("recon_task.start", pr=len(pr_records), gstr2b=len(gstr2b_records))

    # Isolated temp directory for this job under the persistent Fly.io mount.
    # Created here so we can clean it up in the finally block regardless of
    # where in the pipeline an error occurs.
    job_tmp_dir = Path("/tmp/reconlify") / run_id
    job_tmp_dir.mkdir(parents=True, exist_ok=True)

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

            # ── 3. Parse report.json and classify mismatches ──────────────────
            # Load the JSON report written by `reconlify run … --out report.json`
            # in a thread pool so we don't block the event loop on file I/O.
            raw_report: dict[str, Any] = await asyncio.to_thread(_load_report_json, run_id)

            if raw_report:
                parsed = _parse_report_json(raw_report)

                classified, total_itc_at_risk = batch_classify(
                    unmatched_pr=parsed["unmatched_pr"],
                    unmatched_2b=parsed["unmatched_2b"],
                    partial_matches=parsed["partial_matches"],
                    run_id=run_id,
                    client_id=client_id,
                )

                logger.info(
                    "recon_task.classified",
                    total=len(classified),
                    itc_at_risk=round(total_itc_at_risk, 2),
                    missing_in_gstr2b=sum(
                        1 for c in classified
                        if c.category == MismatchCategory.MISSING_IN_GSTR2B
                    ),
                    missing_in_pr=sum(
                        1 for c in classified
                        if c.category == MismatchCategory.MISSING_IN_PR
                    ),
                    amount_diff=sum(
                        1 for c in classified
                        if c.category == MismatchCategory.AMOUNT_DIFFERENCE
                    ),
                )
            else:
                # report.json unavailable — fall back to orchestrator output
                logger.warning(
                    "recon_task.report_fallback",
                    detail="Using orchestrator output for mismatch counts only",
                )

            # ── 4. Detect duplicates ──────────────────────────────────────────
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

            # ── 5. Aggregate vendor stats ─────────────────────────────────────
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
                vs["run_id"]    = uuid.UUID(run_id)
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

            # ── 6. Chain report + notify tasks ────────────────────────────────
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

        finally:
            # Clean up the job’s temp directory after DB results have been
            # persisted (or on failure), regardless of outcome.
            # ignore_errors=True means a missing or partially-written dir
            # will not cause a secondary exception that masks the real error.
            shutil.rmtree(job_tmp_dir, ignore_errors=True)
            logger.debug("recon_task.tmp_cleanup", path=str(job_tmp_dir))


# ── Vendor stats aggregation ───────────────────────────────────────────────────

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
        v["name"]  = str(r.supplier_name or gstin)
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
        vendors[gstin]["mismatched_invoices"] += 1

    result = []
    for gstin, v in vendors.items():
        total = v["pr_invoices"]
        rate  = v["mismatched_invoices"] / total if total > 0 else 0.0
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
