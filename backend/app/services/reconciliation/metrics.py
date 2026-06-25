"""
Reconciliation metrics logger.
Emits structured metrics to structlog and optionally to Prometheus/StatsD.
Persists a metrics snapshot to the reconciliation_runs table.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog

from app.services.reconciliation.schemas import ReconMetrics

logger = structlog.get_logger(__name__)


class ReconMetricsLogger:
    """
    Logs and persists reconciliation engine metrics.

    Usage:
        async with metrics_logger.track(run_id) as tracker:
            output = await engine.reconcile(inp)
            tracker.set_output(output)
    """

    def __init__(self, run_id: str, client_id: str, engine_name: str) -> None:
        self.run_id      = run_id
        self.client_id   = client_id
        self.engine_name = engine_name
        self._start_ts   = time.time()
        self._metrics: ReconMetrics | None = None

    def record(self, metrics: ReconMetrics) -> None:
        """Store the ReconMetrics object after a successful engine run."""
        self._metrics = metrics
        self._emit_structured()

    def _emit_structured(self) -> None:
        """Emit all metrics as a single structured log line."""
        if not self._metrics:
            return
        m = self._metrics
        logger.info(
            "recon.metrics",
            run_id=self.run_id,
            client_id=self.client_id,
            engine=m.engine_name,
            engine_version=m.engine_version,
            # Volume
            pr_input=m.pr_input_count,
            gstr2b_input=m.gstr2b_input_count,
            matched=m.matched_count,
            unmatched_pr=m.unmatched_pr_count,
            unmatched_2b=m.unmatched_2b_count,
            potential=m.potential_match_count,
            # Quality
            match_rate_pct=round(m.match_rate * 100, 2),
            itc_recovery_rate_pct=round(m.itc_recovery_rate * 100, 2),
            # Financial
            total_itc_claimed=m.total_itc_claimed,
            itc_matched=m.itc_matched,
            itc_at_risk=m.itc_at_risk,
            # Performance
            duration_seconds=m.duration_seconds,
        )

    def to_db_dict(self) -> dict[str, Any]:
        """Serialize metrics for storage in reconciliation_runs.run_metrics column."""
        if not self._metrics:
            return {}
        m = self._metrics
        return {
            "engine": m.engine_name,
            "engine_version": m.engine_version,
            "duration_seconds": m.duration_seconds,
            "pr_input_count": m.pr_input_count,
            "gstr2b_input_count": m.gstr2b_input_count,
            "matched_count": m.matched_count,
            "unmatched_pr_count": m.unmatched_pr_count,
            "unmatched_2b_count": m.unmatched_2b_count,
            "potential_match_count": m.potential_match_count,
            "match_rate": m.match_rate,
            "itc_recovery_rate": m.itc_recovery_rate,
            "total_itc_claimed": m.total_itc_claimed,
            "itc_matched": m.itc_matched,
            "itc_at_risk": m.itc_at_risk,
            "config_used": m.config_used,
        }


@asynccontextmanager
async def track_recon(
    run_id: str,
    client_id: str,
    engine_name: str,
) -> AsyncGenerator[ReconMetricsLogger, None]:
    """
    Async context manager for tracking a reconciliation run.

    Emits start/end logs with duration regardless of success or failure.

    Usage:
        async with track_recon(run_id, client_id, "reconlify_cli") as tracker:
            output = await engine.reconcile(inp)
            tracker.record(output.metrics)
    """
    tracker = ReconMetricsLogger(run_id, client_id, engine_name)
    logger.info("recon.started", run_id=run_id, engine=engine_name)
    try:
        yield tracker
    except Exception:
        elapsed = round(time.time() - tracker._start_ts, 3)
        logger.error("recon.failed", run_id=run_id, engine=engine_name, elapsed=elapsed)
        raise
    else:
        elapsed = round(time.time() - tracker._start_ts, 3)
        logger.info("recon.finished", run_id=run_id, engine=engine_name, elapsed=elapsed)
