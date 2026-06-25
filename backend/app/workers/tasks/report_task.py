"""ARQ background task: generate Excel and PDF reports."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from app.config import settings
from app.db.repositories.reports_repo import ReportsRepository
from app.db.session import db_pool
from app.services.reports.excel_generator import ExcelReportGenerator
from app.services.reports.pdf_generator import PDFReportGenerator
from app.services.storage.supabase_storage import build_report_path, upload_file

logger = structlog.get_logger(__name__)


async def report_task(
    ctx: dict[str, Any],
    *,
    run_id: str,
    client_id: str,
    report_id: str | None = None,
    report_type: str = "full_reconciliation",
    report_format: str = "xlsx",
) -> dict[str, Any]:
    structlog.contextvars.bind_contextvars(run_id=run_id, task="report_task")
    logger.info("report_task.start", format=report_format)

    async with db_pool.acquire_for_tenant(client_id) as conn:
        try:
            if report_id:
                await conn.execute(
                    "UPDATE reports SET status = 'generating' WHERE id = $1",
                    uuid.UUID(report_id),
                )

            # Fetch all data cleanly using ReportsRepository
            reports_repo = ReportsRepository(conn)
            report_data = await reports_repo.get_report_data(
                run_id=uuid.UUID(run_id), 
                client_id=uuid.UUID(client_id)
            )

            # Generate binary content
            if report_format == "xlsx":
                generator = ExcelReportGenerator(report_data)
                content = generator.generate()
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                extension = "xlsx"
            elif report_format == "pdf":
                generator = PDFReportGenerator(report_data)
                content = generator.generate()
                mime = "application/pdf"
                extension = "pdf"
            else:
                raise ValueError(f"Unsupported report format: {report_format}")

            # Storage upload
            rid = report_id or str(uuid.uuid4())
            filename = f"recon_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extension}"
            storage_key = build_report_path(client_id, run_id, rid, filename)
            stored_path = await upload_file(
                settings.STORAGE_BUCKET_REPORTS, storage_key, content, mime
            )

            if report_id:
                await conn.execute(
                    """
                    UPDATE reports SET
                        status          = 'ready',
                        storage_path    = $2,
                        file_size_bytes = $3,
                        generated_at    = NOW()
                    WHERE id = $1
                    """,
                    uuid.UUID(report_id), stored_path, len(content),
                )

            logger.info("report_task.success", size=len(content))
            return {"status": "success", "storage_path": stored_path}

        except Exception as exc:
            logger.exception("report_task.failed", run_id=run_id)
            if report_id:
                await conn.execute(
                    "UPDATE reports SET status = 'failed' WHERE id = $1",
                    uuid.UUID(report_id),
                )
            raise
