"""
Reconlify CLI Engine.
Wraps the Reconlify CLI binary as a subprocess, parses its output CSVs,
and translates results into the canonical ReconOutput schema.

This is the production engine used when the CLI license is present.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import structlog

from app.config import settings
from app.services.reconciliation.engine.base import (
    BaseReconciliationEngine,
    ReconciliationEngineError,
)
from app.services.reconciliation.schemas import (
    FieldVariance,
    InvoiceRecord,
    MatchConfidence,
    MatchedRecord,
    MismatchField,
    PotentialMatch,
    ReconInput,
    ReconMetrics,
    ReconOutput,
    UnmatchedRecord,
)

logger = structlog.get_logger(__name__)

AMOUNT_TOLERANCE = 1.0      # ₹ — differences below this are "exact"
HIGH_CONF_THRESHOLD = 0.95  # Similarity score → HIGH confidence
MED_CONF_THRESHOLD  = 0.80  # Similarity score → MEDIUM confidence


class ReconlifyEngine(BaseReconciliationEngine):
    """
    Production reconciliation engine backed by the Reconlify CLI.

    CLI contract (expected output files in --out directory):
      matched.csv       → Records reconciled in both PR and GSTR-2B
      unmatched_pr.csv  → PR records with no GSTR-2B counterpart
      unmatched_2b.csv  → GSTR-2B records with no PR counterpart
      potential.csv     → Records with partial matches (optional)
      metrics.json      → CLI run metadata (version, duration, etc.)
    """

    ENGINE_NAME:    ClassVar[str] = "reconlify_cli"
    ENGINE_VERSION: ClassVar[str] = "detected_at_runtime"

    def __init__(
        self,
        cli_path: str | None = None,
        tmp_dir: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self._cli_path = cli_path or settings.RECONLIFY_CLI_PATH
        self._tmp_dir  = Path(tmp_dir or settings.RECONLIFY_TMP_DIR)
        self._timeout  = timeout or settings.RECONLIFY_TIMEOUT_SECONDS

    def is_available(self) -> bool:
        """Check that the CLI binary exists and is executable."""
        return shutil.which(self._cli_path) is not None or Path(self._cli_path).exists()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _work_dir(self, run_id: str) -> Path:
        return self._tmp_dir / run_id

    @staticmethod
    def _records_to_csv(records: list[InvoiceRecord], path: Path) -> None:
        if not records:
            path.write_text("row_hash,gstin_supplier,invoice_number,invoice_date,"
                           "taxable_value,igst,cgst,sgst,cess,supplier_name,return_period\n")
            return
        rows = [
            {
                "row_hash":       r.row_hash,
                "gstin_supplier": r.gstin_supplier,
                "invoice_number": r.invoice_number,
                "invoice_date":   r.invoice_date.isoformat() if r.invoice_date else "",
                "taxable_value":  r.taxable_value,
                "igst":           r.igst,
                "cgst":           r.cgst,
                "sgst":           r.sgst,
                "cess":           r.cess,
                "supplier_name":  r.supplier_name or "",
                "return_period":  r.return_period or "",
            }
            for r in records
        ]
        pd.DataFrame(rows).to_csv(path, index=False)

    @staticmethod
    def _read_csv_safe(path: Path) -> list[dict[str, Any]]:
        if not path.exists() or path.stat().st_size == 0:
            return []
        try:
            df = pd.read_csv(path, dtype=str).fillna("")
            return df.to_dict(orient="records")  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("reconlify.csv_parse_error", path=str(path), error=str(exc))
            return []

    def _build_pr_index(
        self, pr_records: list[InvoiceRecord]
    ) -> dict[str, InvoiceRecord]:
        return {r.row_hash: r for r in pr_records}

    def _build_2b_index(
        self, gstr2b_records: list[InvoiceRecord]
    ) -> dict[str, InvoiceRecord]:
        return {r.row_hash: r for r in gstr2b_records}

    @staticmethod
    def _float(val: Any) -> float:
        try:
            return float(val or 0)
        except (TypeError, ValueError):
            return 0.0

    def _compute_field_variances(
        self, pr: InvoiceRecord, b2: InvoiceRecord
    ) -> list[FieldVariance]:
        variances: list[FieldVariance] = []

        amount_fields: list[tuple[MismatchField, float, float]] = [
            (MismatchField.TAXABLE_VALUE, pr.taxable_value, b2.taxable_value),
            (MismatchField.IGST,          pr.igst,          b2.igst),
            (MismatchField.CGST,          pr.cgst,          b2.cgst),
            (MismatchField.SGST,          pr.sgst,          b2.sgst),
        ]
        for field, pr_val, b2_val in amount_fields:
            diff = abs(pr_val - b2_val)
            if diff > AMOUNT_TOLERANCE:
                variances.append(FieldVariance(
                    field=field,
                    pr_value=pr_val,
                    gstr2b_value=b2_val,
                    variance=round(diff, 2),
                    variance_pct=round(diff / pr_val * 100, 2) if pr_val else None,
                ))

        if pr.invoice_date and b2.invoice_date and pr.invoice_date != b2.invoice_date:
            variances.append(FieldVariance(
                field=MismatchField.INVOICE_DATE,
                pr_value=str(pr.invoice_date),
                gstr2b_value=str(b2.invoice_date),
            ))

        return variances

    def _determine_confidence(
        self,
        variances: list[FieldVariance],
        similarity: float = 1.0,
    ) -> MatchConfidence:
        if not variances and similarity >= 0.99:
            return MatchConfidence.EXACT
        if len(variances) <= 1 and similarity >= HIGH_CONF_THRESHOLD:
            return MatchConfidence.HIGH
        if len(variances) <= 3 and similarity >= MED_CONF_THRESHOLD:
            return MatchConfidence.MEDIUM
        return MatchConfidence.LOW

    # ── Result parsers ────────────────────────────────────────────────────────

    def _parse_matched(
        self,
        rows: list[dict[str, Any]],
        pr_index: dict[str, InvoiceRecord],
        b2_index: dict[str, InvoiceRecord],
    ) -> list[MatchedRecord]:
        results: list[MatchedRecord] = []
        for row in rows:
            pr_hash = row.get("pr_row_hash", row.get("row_hash", ""))
            b2_hash = row.get("gstr2b_row_hash", row.get("b2b_row_hash", ""))

            pr = pr_index.get(pr_hash)
            b2 = b2_index.get(b2_hash)

            if pr and b2:
                variances = self._compute_field_variances(pr, b2)
                sim = self._float(row.get("similarity_score", 1.0))
                confidence = self._determine_confidence(variances, sim)
            else:
                variances = []
                confidence = MatchConfidence.HIGH

            results.append(MatchedRecord(
                pr_row_hash=pr_hash,
                gstr2b_row_hash=b2_hash,
                gstin_supplier=str(row.get("gstin_supplier", pr.gstin_supplier if pr else "")),
                invoice_number=str(row.get("invoice_number", pr.invoice_number if pr else "")),
                invoice_date=pr.invoice_date if pr else None,
                confidence=confidence,
                pr_taxable_value=self._float(row.get("pr_taxable_value", pr.taxable_value if pr else 0)),
                gstr2b_taxable_value=self._float(row.get("gstr2b_taxable_value", b2.taxable_value if b2 else 0)),
                pr_igst=self._float(row.get("pr_igst", pr.igst if pr else 0)),
                gstr2b_igst=self._float(row.get("gstr2b_igst", b2.igst if b2 else 0)),
                pr_cgst=self._float(row.get("pr_cgst", pr.cgst if pr else 0)),
                gstr2b_cgst=self._float(row.get("gstr2b_cgst", b2.cgst if b2 else 0)),
                pr_sgst=self._float(row.get("pr_sgst", pr.sgst if pr else 0)),
                gstr2b_sgst=self._float(row.get("gstr2b_sgst", b2.sgst if b2 else 0)),
                value_variance=abs(
                    self._float(row.get("pr_taxable_value", pr.taxable_value if pr else 0)) -
                    self._float(row.get("gstr2b_taxable_value", b2.taxable_value if b2 else 0))
                ),
                tax_variance=abs(
                    (pr.total_tax if pr else 0) - (b2.total_tax if b2 else 0)
                ),
                field_variances=variances,
                matched_on=["gstin_supplier", "invoice_number"],
            ))
        return results

    def _parse_unmatched(
        self,
        rows: list[dict[str, Any]],
        source: str,
        record_index: dict[str, InvoiceRecord],
    ) -> list[UnmatchedRecord]:
        results: list[UnmatchedRecord] = []
        for row in rows:
            row_hash = str(row.get("row_hash", ""))
            rec = record_index.get(row_hash)

            igst = self._float(row.get("igst", rec.igst if rec else 0))
            cgst = self._float(row.get("cgst", rec.cgst if rec else 0))
            sgst = self._float(row.get("sgst", rec.sgst if rec else 0))
            cess = self._float(row.get("cess", rec.cess if rec else 0))

            results.append(UnmatchedRecord(
                row_hash=row_hash,
                source=source,
                gstin_supplier=str(row.get("gstin_supplier", rec.gstin_supplier if rec else "")),
                invoice_number=str(row.get("invoice_number", rec.invoice_number if rec else "")),
                invoice_date=rec.invoice_date if rec else None,
                taxable_value=self._float(row.get("taxable_value", rec.taxable_value if rec else 0)),
                igst=igst,
                cgst=cgst,
                sgst=sgst,
                cess=cess,
                total_tax=round(igst + cgst + sgst + cess, 2),
                supplier_name=rec.supplier_name if rec else None,
                return_period=rec.return_period if rec else None,
                itc_impact=round(igst + cgst + sgst, 2) if source == "PURCHASE_REGISTER" else 0.0,
            ))
        return results

    def _parse_potential(
        self,
        rows: list[dict[str, Any]],
        pr_index: dict[str, InvoiceRecord],
        b2_index: dict[str, InvoiceRecord],
    ) -> list[PotentialMatch]:
        results: list[PotentialMatch] = []
        for row in rows:
            pr_hash = str(row.get("pr_row_hash", ""))
            b2_hash = str(row.get("gstr2b_row_hash", ""))
            pr = pr_index.get(pr_hash)
            b2 = b2_index.get(b2_hash)
            sim = self._float(row.get("similarity_score", 0.5))

            variances: list[FieldVariance] = []
            if pr and b2:
                variances = self._compute_field_variances(pr, b2)

            confidence = self._determine_confidence(variances, sim)
            action = _suggest_action(variances, confidence)

            results.append(PotentialMatch(
                pr_row_hash=pr_hash,
                gstr2b_row_hash=b2_hash,
                gstin_supplier=str(row.get("gstin_supplier", pr.gstin_supplier if pr else "")),
                pr_invoice_number=pr.invoice_number if pr else str(row.get("pr_invoice_number", "")),
                gstr2b_invoice_number=b2.invoice_number if b2 else str(row.get("gstr2b_invoice_number", "")),
                similarity_score=round(sim, 4),
                confidence=confidence,
                field_variances=variances,
                suggested_action=action,
            ))
        return results

    # ── CLI execution ─────────────────────────────────────────────────────────

    async def _execute_cli(
        self, work_dir: Path, pr_csv: Path, gstr2b_csv: Path, out_dir: Path
    ) -> tuple[str, float]:
        """Run the Reconlify CLI and return (detected_version, duration_seconds)."""

        cli_args = [
            self._cli_path,
            "recon",
            "--pr",     str(pr_csv),
            "--gstr2b", str(gstr2b_csv),
            "--out",    str(out_dir),
            "--format", "csv",
            "--match-threshold", "0.88",
            "--emit-potential",
        ]
        if settings.RECONLIFY_LICENSE_KEY:
            cli_args += ["--license", settings.RECONLIFY_LICENSE_KEY]

        env = {**os.environ, "RECONLIFY_LICENSE": settings.RECONLIFY_LICENSE_KEY or ""}

        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *cli_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ReconciliationEngineError(
                f"CLI timed out after {self._timeout}s",
                engine=self.ENGINE_NAME,
                cause=exc,
            ) from exc

        duration = round(time.perf_counter() - start, 3)

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace")[:1000]
            raise ReconciliationEngineError(
                f"CLI exited with code {proc.returncode}: {stderr_text}",
                engine=self.ENGINE_NAME,
            )

        # Extract version from stdout
        version = self.ENGINE_VERSION
        for line in stdout.decode(errors="replace").splitlines():
            if "version" in line.lower():
                version = line.split(":", 1)[-1].strip()
                break

        return version, duration

    # ── Main entry point ──────────────────────────────────────────────────────

    async def reconcile(self, inp: ReconInput) -> ReconOutput:
        run_id    = inp.run_id
        work_dir  = self._work_dir(run_id)
        out_dir   = work_dir / "output"

        logger.info(
            "reconlify.start",
            run_id=run_id,
            pr=len(inp.pr_records),
            gstr2b=len(inp.gstr2b_records),
        )

        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(exist_ok=True)

            pr_csv     = work_dir / "pr.csv"
            gstr2b_csv = work_dir / "gstr2b.csv"

            # Write inputs (in thread pool to not block event loop)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._records_to_csv, inp.pr_records, pr_csv)
            await loop.run_in_executor(None, self._records_to_csv, inp.gstr2b_records, gstr2b_csv)

            # Execute CLI
            version, duration = await self._execute_cli(work_dir, pr_csv, gstr2b_csv, out_dir)

            # Read output CSVs
            matched_rows  = await loop.run_in_executor(None, self._read_csv_safe, out_dir / "matched.csv")
            unmatched_pr  = await loop.run_in_executor(None, self._read_csv_safe, out_dir / "unmatched_pr.csv")
            unmatched_2b  = await loop.run_in_executor(None, self._read_csv_safe, out_dir / "unmatched_2b.csv")
            potential_rows = await loop.run_in_executor(None, self._read_csv_safe, out_dir / "potential.csv")

            # Build indexes for enrichment
            pr_index = self._build_pr_index(inp.pr_records)
            b2_index = self._build_2b_index(inp.gstr2b_records)

            # Parse into typed models
            matched   = self._parse_matched(matched_rows, pr_index, b2_index)
            unmatched = (
                self._parse_unmatched(unmatched_pr, "PURCHASE_REGISTER", pr_index) +
                self._parse_unmatched(unmatched_2b, "GSTR_2B", b2_index)
            )
            potential = self._parse_potential(potential_rows, pr_index, b2_index)

            # Compute financial metrics
            total_itc = sum(r.igst + r.cgst + r.sgst for r in inp.pr_records)
            itc_matched = sum(r.pr_igst + r.pr_cgst + r.pr_sgst for r in matched)
            itc_at_risk = sum(u.itc_impact for u in unmatched if u.source == "PURCHASE_REGISTER")
            n_pr = len(inp.pr_records)

            metrics = ReconMetrics(
                engine_name=self.ENGINE_NAME,
                engine_version=version,
                duration_seconds=duration,
                pr_input_count=n_pr,
                gstr2b_input_count=len(inp.gstr2b_records),
                matched_count=len(matched),
                unmatched_pr_count=sum(1 for u in unmatched if u.source == "PURCHASE_REGISTER"),
                unmatched_2b_count=sum(1 for u in unmatched if u.source == "GSTR_2B"),
                potential_match_count=len(potential),
                match_rate=round(len(matched) / n_pr, 4) if n_pr else 0.0,
                total_itc_claimed=round(total_itc, 2),
                itc_matched=round(itc_matched, 2),
                itc_at_risk=round(itc_at_risk, 2),
                itc_recovery_rate=round(itc_matched / total_itc, 4) if total_itc else 0.0,
                config_used=inp.config,
            )

            logger.info(
                "reconlify.complete",
                run_id=run_id,
                matched=len(matched),
                unmatched=len(unmatched),
                potential=len(potential),
                duration_seconds=duration,
                itc_at_risk=itc_at_risk,
            )

            return ReconOutput(
                run_id=run_id,
                client_id=inp.client_id,
                matched=matched,
                unmatched=unmatched,
                potential_matches=potential,
                metrics=metrics,
            )

        except ReconciliationEngineError:
            raise
        except Exception as exc:
            raise ReconciliationEngineError(
                str(exc), engine=self.ENGINE_NAME, run_id=run_id, cause=exc
            ) from exc
        finally:
            # Clean up temp files
            shutil.rmtree(work_dir, ignore_errors=True)


# ── Utility ────────────────────────────────────────────────────────────────────

def _suggest_action(
    variances: list[FieldVariance], confidence: MatchConfidence
) -> str:
    if not variances:
        return "Accept match"

    has_amount = any(v.field in (
        MismatchField.TAXABLE_VALUE, MismatchField.IGST,
        MismatchField.CGST, MismatchField.SGST,
    ) for v in variances)
    has_date = any(v.field == MismatchField.INVOICE_DATE for v in variances)

    if confidence == MatchConfidence.HIGH and has_date and not has_amount:
        return "Accept — date difference only; request amended invoice from supplier"
    if confidence == MatchConfidence.MEDIUM and has_amount:
        return "Review amount variance; raise debit/credit note if applicable"
    if confidence == MatchConfidence.LOW:
        return "Manual review required — multiple field discrepancies"
    return "Review and confirm"
