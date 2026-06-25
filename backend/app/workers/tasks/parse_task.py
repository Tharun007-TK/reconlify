"""
ARQ background task: Parse uploaded PR and GSTR-2B files.
Triggered immediately after upload confirmation.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from app.config import settings
from app.db.repositories.mismatches_repo import MismatchesRepository
from app.db.repositories.runs_repo import RunsRepository
from app.db.repositories.uploads_repo import UploadsRepository
from app.db.session import db_pool
from app.services.parser.gstr2b import parse_gstr2b
from app.services.parser.purchase_register import parse_purchase_register
from app.services.storage.supabase_storage import download_file, upload_file

logger = structlog.get_logger(__name__)


async def parse_task(
    ctx: dict[str, Any],
    *,
    run_id: str,
    client_id: str,
    upload_pr_id: str,
    upload_2b_id: str,
    pr_storage_path: str,
    gstr2b_storage_path: str,
    pr_original_filename: str,
    gstr2b_original_filename: str,
    pr_salt: str,
    gstr2b_salt: str,
) -> dict[str, Any]:
    """
    ARQ task: parse uploaded files and load normalized records into the DB.

    Enqueues `recon_task` on success.
    Marks run as 'failed' on any exception.
    """
    structlog.contextvars.bind_contextvars(run_id=run_id, task="parse_task")
    logger.info("parse_task.start")

    async with db_pool.acquire_for_tenant(client_id) as conn:
        runs_repo = RunsRepository(conn)
        uploads_repo = UploadsRepository(conn)

        try:
            await runs_repo.update_status(uuid.UUID(run_id), "parsing")

            # ── Download raw files from Supabase Storage ──────────────────────
            bucket, pr_path = pr_storage_path.split("/", 1)
            pr_bytes = await download_file(bucket, pr_path)

            bucket2, gstr2b_path = gstr2b_storage_path.split("/", 1)
            gstr2b_bytes = await download_file(bucket2, gstr2b_path)

            # ── Parse PR ──────────────────────────────────────────────────────
            pr_result = parse_purchase_register(
                pr_bytes,
                pr_original_filename,
                pr_salt,
                job_id=run_id,
                client_id=client_id,
            )

            await uploads_repo.update_parse_result(
                uuid.UUID(upload_pr_id),
                status="parsed" if pr_result.error_rows == 0 else "parsed",
                total_rows=pr_result.total_rows,
                parsed_rows=pr_result.parsed_rows,
                error_rows=pr_result.error_rows,
                parse_errors=pr_result.errors,
            )

            # ── Parse GSTR-2B ──────────────────────────────────────────────────
            gstr2b_result = parse_gstr2b(
                gstr2b_bytes,
                gstr2b_original_filename,
                gstr2b_salt,
                job_id=run_id,
                client_id=client_id,
            )

            await uploads_repo.update_parse_result(
                uuid.UUID(upload_2b_id),
                status="parsed",
                total_rows=gstr2b_result.total_rows,
                parsed_rows=gstr2b_result.parsed_rows,
                error_rows=gstr2b_result.error_rows,
                parse_errors=gstr2b_result.errors,
            )

            # ── Store normalized records ──────────────────────────────────────
            await runs_repo.update_status(uuid.UUID(run_id), "normalizing")

            # Bulk-insert PR records to purchase_records table
            if pr_result.records:
                pr_rows = [
                    (
                        uuid.uuid4(), uuid.UUID(run_id), uuid.UUID(client_id),
                        r["invoice_number"], r["gstin_supplier"], r.get("supplier_name"),
                        r.get("invoice_date"), r.get("taxable_value"), r.get("igst"),
                        r.get("cgst"), r.get("sgst"), r.get("cess"), r.get("return_period"),
                        r["row_hash"],
                    )
                    for r in pr_result.records
                ]
                await conn.executemany(
                    """
                    INSERT INTO purchase_records
                        (id, run_id, client_id, invoice_number, gstin_supplier,
                         supplier_name, invoice_date, taxable_value, igst, cgst,
                         sgst, cess, return_period, row_hash)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    ON CONFLICT (run_id, row_hash) DO NOTHING
                    """,
                    pr_rows,
                )

            # Bulk-insert GSTR-2B records
            if gstr2b_result.records:
                b2_rows = [
                    (
                        uuid.uuid4(), uuid.UUID(run_id), uuid.UUID(client_id),
                        r["invoice_number"], r["gstin_supplier"], r.get("supplier_name"),
                        r.get("invoice_date"), r.get("taxable_value"), r.get("igst"),
                        r.get("cgst"), r.get("sgst"), r.get("cess"),
                        r.get("document_type", "INV"), r.get("is_amended", False),
                        r.get("return_period"), r["row_hash"],
                    )
                    for r in gstr2b_result.records
                ]
                await conn.executemany(
                    """
                    INSERT INTO gstr2b_records
                        (id, run_id, client_id, invoice_number, gstin_supplier,
                         supplier_name, invoice_date, taxable_value, igst, cgst, sgst,
                         cess, document_type, is_amended, return_period, row_hash)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                    ON CONFLICT (run_id, row_hash) DO NOTHING
                    """,
                    b2_rows,
                )

            logger.info(
                "parse_task.success",
                run_id=run_id,
                pr_records=len(pr_result.records),
                gstr2b_records=len(gstr2b_result.records),
            )

            # ── Enqueue recon task ────────────────────────────────────────────
            await ctx["queue"].enqueue_job(
                "recon_task",
                run_id=run_id,
                client_id=client_id,
                pr_records=pr_result.records,
                gstr2b_records=gstr2b_result.records,
            )

            return {
                "status": "success",
                "pr_records": len(pr_result.records),
                "gstr2b_records": len(gstr2b_result.records),
            }

        except Exception as exc:
            logger.exception("parse_task.failed", run_id=run_id)
            await runs_repo.fail(
                uuid.UUID(run_id),
                {"stage": "parsing", "error": str(exc), "type": type(exc).__name__},
            )
            raise
