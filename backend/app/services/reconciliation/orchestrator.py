"""
Reconciliation Orchestrator — the single public entry point.

Ties together:
  1. Engine selection (registry)
  2. Input validation
  3. Engine execution
  4. Metrics recording
  5. Result persistence (PostgreSQL)
  6. Mismatch classification (hand-off to classifier)

Usage:
    orchestrator = ReconciliationOrchestrator(conn, engine_type=EngineType.AUTO)
    output = await orchestrator.run(run_id, client_id, pr_records, gstr2b_records)
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.db.repositories.recon_results_repo import ReconciliationResultsRepository
from app.services.reconciliation.engine.base import ReconciliationEngineError
from app.services.reconciliation.engine.registry import EngineType, get_engine
from app.services.reconciliation.metrics import track_recon
from app.services.reconciliation.mismatch_classifier import batch_classify
from app.services.reconciliation.schemas import (
    InvoiceRecord,
    ReconInput,
    ReconOutput,
)

logger = structlog.get_logger(__name__)


class ReconciliationOrchestrator:
    """
    High-level service that orchestrates a complete reconciliation run.

    Responsibilities:
    - Validate input counts
    - Select and instantiate the appropriate engine
    - Track metrics
    - Persist results to PostgreSQL
    - Run post-reconciliation classification on unmatched records

    This class is deliberately engine-agnostic. Swap the engine without
    touching anything here.
    """

    def __init__(
        self,
        conn: Any,                               # asyncpg.Connection (type erased for testability)
        engine_type: EngineType = EngineType.AUTO,
        run_config: dict[str, Any] | None = None,
    ) -> None:
        self._conn       = conn
        self._engine     = get_engine(engine_type)
        self._run_config = run_config or {}

    @property
    def engine_name(self) -> str:
        return self._engine.ENGINE_NAME

    async def run(
        self,
        run_id: str,
        client_id: str,
        pr_records: list[InvoiceRecord],
        gstr2b_records: list[InvoiceRecord],
    ) -> ReconOutput:
        """
        Execute a full reconciliation pipeline.

        Args:
            run_id:         UUID of the reconciliation_runs row
            client_id:      Tenant client UUID
            pr_records:     Validated, normalized Purchase Register records
            gstr2b_records: Validated, normalized GSTR-2B records

        Returns:
            ReconOutput with matched, unmatched, potential matches, and metrics

        Raises:
            ReconciliationEngineError: On engine failure (after DB mark-fail)
            ValueError:               On invalid input
        """
        structlog.contextvars.bind_contextvars(run_id=run_id, engine=self.engine_name)

        # ── Input guard ───────────────────────────────────────────────────────
        if not pr_records:
            raise ValueError("Cannot reconcile: Purchase Register has 0 records")
        if not gstr2b_records:
            raise ValueError("Cannot reconcile: GSTR-2B has 0 records")

        logger.info(
            "orchestrator.run.start",
            run_id=run_id,
            client_id=client_id,
            pr_count=len(pr_records),
            gstr2b_count=len(gstr2b_records),
            engine=self.engine_name,
        )

        inp = ReconInput(
            run_id=run_id,
            client_id=client_id,
            pr_records=pr_records,
            gstr2b_records=gstr2b_records,
            config=self._run_config,
        )

        # ── Execute engine with metrics tracking ──────────────────────────────
        async with track_recon(run_id, client_id, self.engine_name) as tracker:
            output = await self._engine.reconcile(inp)
            tracker.record(output.metrics)

        # ── Post-process: classify unmatched records ──────────────────────────
        if output.unmatched:
            unmatched_pr = [
                u.model_dump() for u in output.unmatched
                if u.source == "PURCHASE_REGISTER"
            ]
            unmatched_2b = [
                u.model_dump() for u in output.unmatched
                if u.source == "GSTR_2B"
            ]

            classified, total_itc_at_risk = batch_classify(
                unmatched_pr, unmatched_2b,
                run_id=run_id,
                client_id=client_id,
            )

            logger.info(
                "orchestrator.classified",
                count=len(classified),
                itc_at_risk=round(total_itc_at_risk, 2),
            )

        # ── Persist to PostgreSQL ─────────────────────────────────────────────
        repo = ReconciliationResultsRepository(self._conn)
        counts = await repo.persist_output(
            run_id=uuid.UUID(run_id),
            client_id=uuid.UUID(client_id),
            output=output,
            metrics_dict=tracker.to_db_dict(),
        )

        logger.info(
            "orchestrator.run.complete",
            run_id=run_id,
            **counts,
            match_rate_pct=round(output.metrics.match_rate * 100, 2),
            itc_at_risk=output.metrics.itc_at_risk,
        )

        return output

    async def get_run_summary(self, run_id: str) -> dict[str, Any]:
        """Fetch a lightweight summary of a completed run from the DB."""
        row = await self._conn.fetchrow(
            """
            SELECT
                id, status, engine_name, matched_count, unmatched_pr_count,
                unmatched_2b_count, total_itc_claimed, itc_at_risk, itc_matched,
                run_metrics, completed_at
            FROM reconciliation_runs
            WHERE id = $1
            """,
            uuid.UUID(run_id),
        )
        if not row:
            return {}
        return dict(row)
