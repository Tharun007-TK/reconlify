"""
Data normalization pipeline.
Transforms a raw Pandas DataFrame (after column mapping) into
a list of validated NormalizedRecord objects.

Handles:
- Empty rows
- Missing required fields (per configured policy)
- Type coercion for all GST fields
- Row hash computation for dedup
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.services.parser.schemas import (
    DocumentType,
    NormalizedRecord,
    ParseIssue,
    ParseSeverity,
)
from app.services.parser.validators import (
    validate_amount,
    validate_date,
    validate_document_type,
    validate_gstin,
    validate_invoice_number,
    validate_return_period,
)


@dataclass
class NormalizationConfig:
    """Controls normalization behaviour."""
    skip_on_missing_gstin: bool        = True    # Skip rows where GSTIN is absent
    skip_on_missing_invoice_no: bool   = True    # Skip rows where invoice_no is absent
    skip_on_invalid_gstin: bool        = False   # Include rows with invalid GSTIN (but flag)
    row_hash_salt: str                 = ""      # Per-upload salt for dedup


@dataclass
class NormalizationResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    issues:  list[ParseIssue]       = field(default_factory=list)
    skipped_rows: int               = 0
    warning_rows: int               = 0


def _safe_str(val: Any) -> str | None:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("nan", "none", "null", "n/a") else None


def _compute_row_hash(record: NormalizedRecord, salt: str) -> str:
    key = "|".join([
        record.gstin_supplier,
        record.invoice_number,
        str(record.invoice_date or ""),
        str(round(record.taxable_value, 2)),
        record.document_type,
        salt,
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _is_empty_row(row: pd.Series) -> bool:  # type: ignore[type-arg]
    """True if all meaningful fields are null or blank."""
    meaningful = [
        "gstin_supplier", "invoice_number", "taxable_value",
        "igst", "cgst", "sgst",
    ]
    for field_name in meaningful:
        val = row.get(field_name)
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            if str(val).strip() not in ("", "nan", "none", "null"):
                return False
    return True


def normalize_dataframe(
    df: pd.DataFrame,
    *,
    run_id: str,
    client_id: str,
    config: NormalizationConfig | None = None,
) -> NormalizationResult:
    """
    Normalize a mapped DataFrame into structured NormalizedRecord objects.

    Args:
        df:        DataFrame with canonical column names already applied
        run_id:    Reconciliation run ID (for traceability)
        client_id: Client/tenant ID
        config:    Normalization behaviour config

    Returns:
        NormalizationResult with records and issues lists
    """
    cfg = config or NormalizationConfig()
    result = NormalizationResult()
    row_issues_seen: set[int] = set()  # Rows that already have a warning

    for idx, row in df.iterrows():
        # 1-indexed row number accounting for header
        row_num = int(idx) + 2  # type: ignore[arg-type]

        # ── Skip fully empty rows ─────────────────────────────────────────────
        if _is_empty_row(row):
            continue

        has_error = False
        has_warning = False
        row_issues: list[ParseIssue] = []

        def add_issue(
            severity: ParseSeverity,
            message: str,
            field_name: str | None = None,
            raw_value: Any = None,
        ) -> None:
            nonlocal has_error, has_warning
            row_issues.append(ParseIssue(
                row=row_num,
                field=field_name,
                value=str(raw_value) if raw_value is not None else None,
                severity=severity,
                message=message,
            ))
            if severity == ParseSeverity.ERROR:
                has_error = True
            elif severity == ParseSeverity.WARNING:
                has_warning = True

        # ── GSTIN ─────────────────────────────────────────────────────────────
        raw_gstin = row.get("gstin_supplier")
        gstin, gstin_valid, gstin_msg = validate_gstin(raw_gstin)

        if not gstin and cfg.skip_on_missing_gstin:
            add_issue(ParseSeverity.ERROR, "GSTIN is missing — row skipped", "gstin_supplier", raw_gstin)
            result.issues.extend(row_issues)
            result.skipped_rows += 1
            continue

        if gstin_msg:
            severity = ParseSeverity.ERROR if (not gstin_valid and cfg.skip_on_invalid_gstin) else ParseSeverity.WARNING
            add_issue(severity, gstin_msg, "gstin_supplier", raw_gstin)
            if severity == ParseSeverity.ERROR:
                result.issues.extend(row_issues)
                result.skipped_rows += 1
                continue

        # ── Invoice Number ─────────────────────────────────────────────────────
        raw_inv = row.get("invoice_number")
        invoice_no, inv_valid, inv_msg = validate_invoice_number(raw_inv)

        if not invoice_no and cfg.skip_on_missing_invoice_no:
            add_issue(ParseSeverity.ERROR, "Invoice number is missing — row skipped", "invoice_number", raw_inv)
            result.issues.extend(row_issues)
            result.skipped_rows += 1
            continue

        if inv_msg:
            add_issue(ParseSeverity.WARNING, inv_msg, "invoice_number", raw_inv)

        # ── Invoice Date ───────────────────────────────────────────────────────
        raw_date = row.get("invoice_date")
        invoice_date, date_valid, date_msg = validate_date(raw_date)
        if date_msg:
            severity = ParseSeverity.WARNING if invoice_date else ParseSeverity.WARNING
            add_issue(severity, date_msg, "invoice_date", raw_date)

        # ── Document Type ──────────────────────────────────────────────────────
        raw_doc_type = row.get("document_type")
        doc_type_str, _, doc_msg = validate_document_type(raw_doc_type)
        if doc_msg:
            add_issue(ParseSeverity.WARNING, doc_msg, "document_type", raw_doc_type)
        doc_type = DocumentType(doc_type_str)

        # ── Amounts ───────────────────────────────────────────────────────────
        taxable_value, _, tv_msg = validate_amount(row.get("taxable_value"), "taxable_value")
        if tv_msg:
            add_issue(ParseSeverity.WARNING, tv_msg, "taxable_value", row.get("taxable_value"))

        igst, _, igst_msg = validate_amount(row.get("igst"), "igst")
        if igst_msg:
            add_issue(ParseSeverity.WARNING, igst_msg, "igst", row.get("igst"))

        cgst, _, cgst_msg = validate_amount(row.get("cgst"), "cgst")
        if cgst_msg:
            add_issue(ParseSeverity.WARNING, cgst_msg, "cgst", row.get("cgst"))

        sgst, _, sgst_msg = validate_amount(row.get("sgst"), "sgst")
        if sgst_msg:
            add_issue(ParseSeverity.WARNING, sgst_msg, "sgst", row.get("sgst"))

        cess, _, cess_msg = validate_amount(row.get("cess", 0), "cess")
        if cess_msg:
            add_issue(ParseSeverity.WARNING, cess_msg, "cess", row.get("cess"))

        # ── Return Period + FY ────────────────────────────────────────────────
        raw_period = row.get("return_period")
        return_period, _, period_msg = validate_return_period(raw_period)
        if period_msg:
            add_issue(ParseSeverity.WARNING, period_msg, "return_period", raw_period)

        fy = _safe_str(row.get("fy"))

        # Auto-compute FY from invoice date if missing
        if not fy and invoice_date:
            year = invoice_date.year
            month = invoice_date.month
            if month >= 4:
                fy = f"{year}-{str(year + 1)[2:]}"
            else:
                fy = f"{year - 1}-{str(year)[2:]}"

        # ── Optional fields ───────────────────────────────────────────────────
        supplier_name = _safe_str(row.get("supplier_name"))
        itc_availability = _safe_str(row.get("itc_availability"))
        is_amended_raw = row.get("is_amended")
        is_amended = (
            str(is_amended_raw).strip().lower() in ("true", "yes", "1", "y")
            if is_amended_raw is not None
            else False
        )

        # ── Build NormalizedRecord ────────────────────────────────────────────
        record = NormalizedRecord(
            gstin_supplier=gstin or "",
            supplier_name=supplier_name,
            invoice_number=invoice_no or "",
            invoice_date=invoice_date,
            document_type=doc_type,
            taxable_value=taxable_value,
            igst=igst,
            cgst=cgst,
            sgst=sgst,
            cess=cess,
            total_tax=0.0,      # Computed by model validator
            total_value=0.0,    # Computed by model validator
            return_period=return_period,
            fy=fy,
            state_code=None,    # Computed by model validator
            gstin_valid=gstin_valid,
            source_row=row_num,
            row_hash=None,      # Set below
            is_amended=is_amended,
            itc_availability=itc_availability,
        )

        record.row_hash = _compute_row_hash(record, cfg.row_hash_salt)

        result.records.append(record)
        result.issues.extend(row_issues)

        if has_warning:
            result.warning_rows += 1

    return result
