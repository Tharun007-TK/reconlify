"""
Mismatch Categorization Engine.

Analyzes unmatched records, partial matches, and duplicates
to categorize them into standard business anomaly types with
explicit severities, reasons, and recommended actions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
AMOUNT_TOLERANCE = 1.0


class MismatchCategory(StrEnum):
    MISSING_IN_GSTR2B           = "Missing in GSTR2B"
    MISSING_IN_PR               = "Missing in Purchase Register"
    AMOUNT_DIFFERENCE           = "Amount Difference"
    INVOICE_NUMBER_DIFFERENCE   = "Invoice Number Difference"
    DATE_DIFFERENCE             = "Date Difference"
    DUPLICATE_ENTRY             = "Duplicate Entry"
    POTENTIAL_MATCH             = "Potential Match"


class Severity(StrEnum):
    CRITICAL = "Critical"
    HIGH     = "High"
    MEDIUM   = "Medium"
    LOW      = "Low"


@dataclass
class ClassifiedRecord:
    category: MismatchCategory
    severity: Severity
    reason: str
    recommended_action: str
    mismatch_fields: list[str]

    # Core record tracing fields
    source: str
    row_hash: str
    gstin_supplier: str
    invoice_number: str
    taxable_value: float
    itc_impact: float
    
    # Dump for persistence
    raw: dict[str, Any]


def _safe_float(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_missing_pr(record: dict[str, Any]) -> ClassifiedRecord:
    """Classify a record found in PR but missing in GSTR-2B."""
    gstin = str(record.get("gstin_supplier", "")).upper().strip()
    igst = _safe_float(record.get("igst"))
    cgst = _safe_float(record.get("cgst"))
    sgst = _safe_float(record.get("sgst"))
    itc_impact = igst + cgst + sgst

    if gstin and not GSTIN_RE.match(gstin):
        reason = f"Invalid Vendor GSTIN: {gstin}"
        severity = Severity.CRITICAL
        recommended_action = "Update vendor master with valid GSTIN and re-run."
    elif itc_impact > 50000:
        reason = "High-value invoice missing from vendor's GSTR-2B filings."
        severity = Severity.HIGH
        recommended_action = "Contact vendor immediately to file missing invoice; withhold payment."
    else:
        reason = "Invoice not filed by vendor in current GSTR-2B period."
        severity = Severity.MEDIUM
        recommended_action = "Follow up with vendor for next filing period."

    return ClassifiedRecord(
        category=MismatchCategory.MISSING_IN_GSTR2B,
        severity=severity,
        reason=reason,
        recommended_action=recommended_action,
        source="PURCHASE_REGISTER",
        row_hash=str(record.get("row_hash", "")),
        gstin_supplier=gstin,
        invoice_number=str(record.get("invoice_number", "")),
        taxable_value=_safe_float(record.get("taxable_value")),
        itc_impact=itc_impact,
        mismatch_fields=[],
        raw=record,
    )


def classify_missing_2b(record: dict[str, Any]) -> ClassifiedRecord:
    """Classify a record found in GSTR-2B but missing in PR."""
    igst = _safe_float(record.get("igst"))
    cgst = _safe_float(record.get("cgst"))
    sgst = _safe_float(record.get("sgst"))
    tax_amount = igst + cgst + sgst
    
    if tax_amount > 10000:
        severity = Severity.MEDIUM
        reason = "Significant ITC available but invoice not found in internal books."
        recommended_action = "Verify if purchase was made but not accounted for. Ensure no internal fraud."
    else:
        severity = Severity.LOW
        reason = "Invoice filed by vendor but absent in internal books."
        recommended_action = "Review and account for the invoice to claim ITC."

    return ClassifiedRecord(
        category=MismatchCategory.MISSING_IN_PR,
        severity=severity,
        reason=reason,
        recommended_action=recommended_action,
        source="GSTR_2B",
        row_hash=str(record.get("row_hash", "")),
        gstin_supplier=str(record.get("gstin_supplier", "")),
        invoice_number=str(record.get("invoice_number", "")),
        taxable_value=_safe_float(record.get("taxable_value")),
        itc_impact=0.0, # Not an ITC loss, rather an un-availed ITC opportunity
        mismatch_fields=[],
        raw=record,
    )


def classify_partial_match(
    pr_record: dict[str, Any], gstr2b_record: dict[str, Any], field_variances: list[dict[str, Any]]
) -> ClassifiedRecord:
    """Classify a record that matches partially (e.g. amount or date differ)."""
    pr_gstin = str(pr_record.get("gstin_supplier", ""))
    igst = _safe_float(pr_record.get("igst"))
    cgst = _safe_float(pr_record.get("cgst"))
    sgst = _safe_float(pr_record.get("sgst"))
    itc_impact = igst + cgst + sgst

    # Determine dominant mismatch
    variance_fields = [v.get("field") for v in field_variances]
    
    if "taxable_value" in variance_fields or "igst" in variance_fields or "cgst" in variance_fields:
        category = MismatchCategory.AMOUNT_DIFFERENCE
        severity = Severity.HIGH
        reason = "Mismatch in taxable value or tax amounts."
        recommended_action = "Issue Debit/Credit note to reconcile the financial difference."
    elif "invoice_date" in variance_fields:
        category = MismatchCategory.DATE_DIFFERENCE
        severity = Severity.LOW
        reason = "Dates differ between PR and GSTR-2B."
        recommended_action = "Accept match if financial values align perfectly."
        itc_impact = 0.0 # Date mismatches usually don't block ITC if amounts match
    else:
        category = MismatchCategory.INVOICE_NUMBER_DIFFERENCE
        severity = Severity.MEDIUM
        reason = "Slight variation in invoice numbering format."
        recommended_action = "Accept match. Train OCR/Entry team on standardizing formats."
        itc_impact = 0.0

    return ClassifiedRecord(
        category=category,
        severity=severity,
        reason=reason,
        recommended_action=recommended_action,
        source="PURCHASE_REGISTER",
        row_hash=str(pr_record.get("row_hash", "")),
        gstin_supplier=pr_gstin,
        invoice_number=str(pr_record.get("invoice_number", "")),
        taxable_value=_safe_float(pr_record.get("taxable_value")),
        itc_impact=itc_impact,
        mismatch_fields=variance_fields,
        raw=pr_record,
    )


def classify_duplicate(record: dict[str, Any], dtype: str) -> ClassifiedRecord:
    """Classify an exact or fuzzy duplicate entry."""
    igst = _safe_float(record.get("igst"))
    cgst = _safe_float(record.get("cgst"))
    sgst = _safe_float(record.get("sgst"))
    itc_impact = igst + cgst + sgst

    if dtype == "exact":
        severity = Severity.HIGH
        reason = "Exact duplicate found within the same source file."
        recommended_action = "Delete duplicate entry from internal ERP to prevent double accounting."
    else:
        severity = Severity.MEDIUM
        reason = "Suspected fuzzy duplicate (similar invoice/amount)."
        recommended_action = "Manually review and void the duplicate if confirmed."

    return ClassifiedRecord(
        category=MismatchCategory.DUPLICATE_ENTRY,
        severity=severity,
        reason=reason,
        recommended_action=recommended_action,
        source=str(record.get("source", "UNKNOWN")),
        row_hash=str(record.get("row_hash", "")),
        gstin_supplier=str(record.get("gstin_supplier", "")),
        invoice_number=str(record.get("invoice_number", "")),
        taxable_value=_safe_float(record.get("taxable_value")),
        itc_impact=itc_impact,
        mismatch_fields=[],
        raw=record,
    )


def classify_potential_match(pr_record: dict[str, Any], sim_score: float) -> ClassifiedRecord:
    """Classify an engine-flagged potential match."""
    igst = _safe_float(pr_record.get("igst"))
    cgst = _safe_float(pr_record.get("cgst"))
    sgst = _safe_float(pr_record.get("sgst"))
    
    if sim_score >= 0.90:
        severity = Severity.LOW
        reason = f"High similarity match ({sim_score * 100:.1f}%)."
        recommended_action = "Review briefly and accept."
    else:
        severity = Severity.MEDIUM
        reason = f"Moderate similarity match ({sim_score * 100:.1f}%)."
        recommended_action = "Carefully review field differences before accepting."

    return ClassifiedRecord(
        category=MismatchCategory.POTENTIAL_MATCH,
        severity=severity,
        reason=reason,
        recommended_action=recommended_action,
        source="PURCHASE_REGISTER",
        row_hash=str(pr_record.get("row_hash", "")),
        gstin_supplier=str(pr_record.get("gstin_supplier", "")),
        invoice_number=str(pr_record.get("invoice_number", "")),
        taxable_value=_safe_float(pr_record.get("taxable_value")),
        itc_impact=igst + cgst + sgst,
        mismatch_fields=[],
        raw=pr_record,
    )


def batch_classify(
    unmatched_pr: list[dict[str, Any]],
    unmatched_2b: list[dict[str, Any]],
    partial_matches: list[dict[str, Any]] = None,
    duplicate_records: list[dict[str, Any]] = None,
    potential_matches: list[dict[str, Any]] = None,
    *,
    run_id: str = "",
    client_id: str = "",
) -> tuple[list[ClassifiedRecord], float]:
    """
    Classifies a batch of anomalies into structured business categories.
    
    Returns:
        (list of ClassifiedRecord, total_itc_at_risk)
    """
    classified: list[ClassifiedRecord] = []
    total_itc_at_risk = 0.0

    # 1. Missing in GSTR-2B
    for rec in unmatched_pr:
        c = classify_missing_pr(rec)
        classified.append(c)
        total_itc_at_risk += c.itc_impact

    # 2. Missing in PR
    for rec in unmatched_2b:
        classified.append(classify_missing_2b(rec))

    # 3. Partial Matches (Amount, Invoice No, Date Diff)
    if partial_matches:
        for pm in partial_matches:
            c = classify_partial_match(pm["pr"], pm["gstr2b"], pm.get("field_variances", []))
            classified.append(c)
            total_itc_at_risk += c.itc_impact

    # 4. Duplicates
    if duplicate_records:
        for dup in duplicate_records:
            # Reconstruct dummy record for mapping
            dummy = {
                "source": dup.get("source"),
                "row_hash": dup.get("record_id_a"),
                "gstin_supplier": dup.get("gstin_supplier"),
                "invoice_number": dup.get("invoice_number"),
                # Missing amounts since duplicate payload only has identifiers
            }
            c = classify_duplicate(dummy, dup.get("dtype", "exact"))
            classified.append(c)
            total_itc_at_risk += c.itc_impact

    # 5. Potential Matches
    if potential_matches:
        for pot in potential_matches:
            dummy = {
                "source": "PURCHASE_REGISTER",
                "row_hash": pot.get("pr_row_hash"),
                "gstin_supplier": pot.get("gstin_supplier"),
                "invoice_number": pot.get("pr_invoice_number"),
            }
            c = classify_potential_match(dummy, pot.get("similarity_score", 0.0))
            classified.append(c)
            total_itc_at_risk += c.itc_impact

    logger.info(
        "classifier.batch_complete",
        total_classified=len(classified),
        itc_at_risk=round(total_itc_at_risk, 2),
    )

    return classified, total_itc_at_risk
