"""
Pure-Python Pandas reconciliation engine.
Used as the fallback when Reconlify CLI is unavailable
(dev environments, CI/CD, license expiry).

Matching strategy:
1. Exact match: GSTIN + Invoice Number + Date
2. Relaxed match: GSTIN + Invoice Number (different date or date missing)
3. Fuzzy match: GSTIN exact + invoice number fuzzy (RapidFuzz token_sort_ratio)
   → produces PotentialMatch, not MatchedRecord

This engine intentionally mirrors Reconlify semantics
so swapping back is transparent to the orchestrator.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import ClassVar

import structlog
from rapidfuzz import fuzz

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

AMOUNT_TOLERANCE     = 1.0     # ₹ — below this = "same"
FUZZY_THRESHOLD      = 82.0   # Min score for a potential match (0–100)
HIGH_CONF_THRESHOLD  = 0.95
MED_CONF_THRESHOLD   = 0.80


def _f(val: object) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _compute_variances(pr: InvoiceRecord, b2: InvoiceRecord) -> list[FieldVariance]:
    variances: list[FieldVariance] = []
    for field, pv, bv in [
        (MismatchField.TAXABLE_VALUE, pr.taxable_value, b2.taxable_value),
        (MismatchField.IGST,          pr.igst,          b2.igst),
        (MismatchField.CGST,          pr.cgst,          b2.cgst),
        (MismatchField.SGST,          pr.sgst,          b2.sgst),
    ]:
        diff = abs(pv - bv)
        if diff > AMOUNT_TOLERANCE:
            variances.append(FieldVariance(
                field=field,
                pr_value=pv,
                gstr2b_value=bv,
                variance=round(diff, 2),
                variance_pct=round(diff / pv * 100, 2) if pv else None,
            ))

    if pr.invoice_date and b2.invoice_date and pr.invoice_date != b2.invoice_date:
        variances.append(FieldVariance(
            field=MismatchField.INVOICE_DATE,
            pr_value=str(pr.invoice_date),
            gstr2b_value=str(b2.invoice_date),
        ))
    return variances


def _confidence(variances: list[FieldVariance], sim: float = 1.0) -> MatchConfidence:
    if not variances and sim >= 0.99:
        return MatchConfidence.EXACT
    if len(variances) <= 1 and sim >= HIGH_CONF_THRESHOLD:
        return MatchConfidence.HIGH
    if len(variances) <= 3 and sim >= MED_CONF_THRESHOLD:
        return MatchConfidence.MEDIUM
    return MatchConfidence.LOW


def _make_matched(pr: InvoiceRecord, b2: InvoiceRecord) -> MatchedRecord:
    variances = _compute_variances(pr, b2)
    conf = _confidence(variances)
    return MatchedRecord(
        pr_row_hash=pr.row_hash,
        gstr2b_row_hash=b2.row_hash,
        gstin_supplier=pr.gstin_supplier,
        invoice_number=pr.invoice_number,
        invoice_date=pr.invoice_date,
        confidence=conf,
        pr_taxable_value=pr.taxable_value,
        gstr2b_taxable_value=b2.taxable_value,
        pr_igst=pr.igst, gstr2b_igst=b2.igst,
        pr_cgst=pr.cgst, gstr2b_cgst=b2.cgst,
        pr_sgst=pr.sgst, gstr2b_sgst=b2.sgst,
        value_variance=abs(pr.taxable_value - b2.taxable_value),
        tax_variance=abs(pr.total_tax - b2.total_tax),
        field_variances=variances,
        matched_on=["gstin_supplier", "invoice_number"],
    )


def _make_unmatched(rec: InvoiceRecord, source: str) -> UnmatchedRecord:
    return UnmatchedRecord(
        row_hash=rec.row_hash,
        source=source,
        gstin_supplier=rec.gstin_supplier,
        invoice_number=rec.invoice_number,
        invoice_date=rec.invoice_date,
        taxable_value=rec.taxable_value,
        igst=rec.igst, cgst=rec.cgst, sgst=rec.sgst, cess=rec.cess,
        total_tax=rec.total_tax,
        supplier_name=rec.supplier_name,
        return_period=rec.return_period,
        itc_impact=round(rec.igst + rec.cgst + rec.sgst, 2)
        if source == "PURCHASE_REGISTER" else 0.0,
    )


class PandasEngine(BaseReconciliationEngine):
    """
    Pure-Python fallback reconciliation engine.
    No external dependencies beyond Pandas and RapidFuzz.
    """

    ENGINE_NAME:    ClassVar[str] = "pandas_fallback"
    ENGINE_VERSION: ClassVar[str] = "1.0.0"

    def is_available(self) -> bool:
        return True   # Always available

    async def reconcile(self, inp: ReconInput) -> ReconOutput:  # noqa: C901
        start = time.perf_counter()
        run_id = inp.run_id

        logger.info(
            "pandas_engine.start",
            run_id=run_id,
            pr=len(inp.pr_records),
            gstr2b=len(inp.gstr2b_records),
        )

        # ── Build lookup structures ───────────────────────────────────────────
        # Key: (gstin, invoice_number) → first matching 2B record
        b2_by_key: dict[tuple[str, str], InvoiceRecord] = {}
        # Key: gstin → list of 2B records (for fuzzy pass)
        b2_by_gstin: dict[str, list[InvoiceRecord]] = defaultdict(list)

        for rec in inp.gstr2b_records:
            key = (rec.gstin_supplier, rec.invoice_number)
            b2_by_key.setdefault(key, rec)
            b2_by_gstin[rec.gstin_supplier].append(rec)

        matched:   list[MatchedRecord]   = []
        unmatched: list[UnmatchedRecord] = []
        potential: list[PotentialMatch]  = []

        used_b2_hashes: set[str] = set()

        for pr in inp.pr_records:
            key = (pr.gstin_supplier, pr.invoice_number)

            # ── Pass 1: Exact match (GSTIN + invoice number) ─────────────────
            if key in b2_by_key:
                b2 = b2_by_key[key]
                matched.append(_make_matched(pr, b2))
                used_b2_hashes.add(b2.row_hash)
                continue

            # ── Pass 2: Fuzzy invoice number (same GSTIN) ────────────────────
            candidates = b2_by_gstin.get(pr.gstin_supplier, [])
            best_score = 0.0
            best_b2: InvoiceRecord | None = None

            for b2 in candidates:
                if b2.row_hash in used_b2_hashes:
                    continue
                score = fuzz.token_sort_ratio(pr.invoice_number, b2.invoice_number) / 100.0
                if score > best_score:
                    best_score = score
                    best_b2 = b2

            if best_b2 and best_score >= FUZZY_THRESHOLD / 100.0:
                variances = _compute_variances(pr, best_b2)
                conf = _confidence(variances, best_score)

                # High fuzzy + no amount variance → treat as matched
                if best_score >= 0.96 and not any(
                    v.field in (MismatchField.TAXABLE_VALUE, MismatchField.IGST,
                                MismatchField.CGST, MismatchField.SGST)
                    for v in variances
                ):
                    matched.append(_make_matched(pr, best_b2))
                    used_b2_hashes.add(best_b2.row_hash)
                else:
                    potential.append(PotentialMatch(
                        pr_row_hash=pr.row_hash,
                        gstr2b_row_hash=best_b2.row_hash,
                        gstin_supplier=pr.gstin_supplier,
                        pr_invoice_number=pr.invoice_number,
                        gstr2b_invoice_number=best_b2.invoice_number,
                        similarity_score=round(best_score, 4),
                        confidence=conf,
                        field_variances=variances,
                        suggested_action=_suggest_action_pandas(variances, conf),
                    ))
            else:
                # No match found
                unmatched.append(_make_unmatched(pr, "PURCHASE_REGISTER"))

        # ── Unmatched GSTR-2B records ─────────────────────────────────────────
        for b2 in inp.gstr2b_records:
            if b2.row_hash not in used_b2_hashes:
                unmatched.append(_make_unmatched(b2, "GSTR_2B"))

        duration = round(time.perf_counter() - start, 3)

        # ── Metrics ───────────────────────────────────────────────────────────
        n_pr = len(inp.pr_records)
        total_itc  = sum(r.igst + r.cgst + r.sgst for r in inp.pr_records)
        itc_matched = sum(r.pr_igst + r.pr_cgst + r.pr_sgst for r in matched)
        itc_at_risk = sum(u.itc_impact for u in unmatched if u.source == "PURCHASE_REGISTER")

        metrics = ReconMetrics(
            engine_name=self.ENGINE_NAME,
            engine_version=self.ENGINE_VERSION,
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
            "pandas_engine.complete",
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


def _suggest_action_pandas(
    variances: list[FieldVariance], confidence: MatchConfidence
) -> str:
    has_amount = any(v.field in (
        MismatchField.TAXABLE_VALUE, MismatchField.IGST,
        MismatchField.CGST, MismatchField.SGST,
    ) for v in variances)
    has_date   = any(v.field == MismatchField.INVOICE_DATE for v in variances)

    if confidence == MatchConfidence.HIGH and has_date and not has_amount:
        return "Accept — date difference only"
    if has_amount:
        return "Review amount variance; consider debit/credit note"
    if confidence == MatchConfidence.LOW:
        return "Manual review required"
    return "Review and confirm"
