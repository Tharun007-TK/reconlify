"""
GST File Parser — Main orchestrator.

Public API:
    parse_gst_file(file_bytes, filename, ...) -> ParseResult

This module ties together:
  - detector.py     → auto-detect file type and format
  - column_mapper.py → map raw columns to canonical names
  - normalizer.py    → validate and normalize each row
  - schemas.py       → typed output models

Also handles the GSTR-2B JSON path (official GST portal format)
which bypasses the DataFrame pipeline entirely.
"""
from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd
import structlog

from app.services.parser.column_mapper import (
    ColumnMapping,
    MappingResult,
    apply_mapping,
    map_columns,
)
from app.services.parser.detector import DetectionResult, detect_file
from app.services.parser.normalizer import NormalizationConfig, normalize_dataframe
from app.services.parser.schemas import (
    ColumnMapping as ColumnMappingSchema,
    FileType,
    NormalizedRecord,
    ParseIssue,
    ParseResult,
    ParseSeverity,
)
from app.services.parser.validators import (
    validate_amount,
    validate_date,
    validate_gstin,
    validate_invoice_number,
    validate_return_period,
)

logger = structlog.get_logger(__name__)


# ── GSTR-2B JSON (official portal format) parser ─────────────────────────────

def _parse_gstr2b_json_native(
    data: dict[str, Any],
    run_id: str,
    client_id: str,
    salt: str,
) -> list[NormalizedRecord]:
    """
    Parse the official GSTN portal GSTR-2B JSON structure directly,
    without going through the DataFrame path.

    Supports:
    - data.docdata.b2b[]  → B2B invoices
    - data.docdata.cdn[]  → Credit/Debit notes
    """
    from app.services.parser.normalizer import _compute_row_hash
    from app.services.parser.schemas import DocumentType

    records: list[NormalizedRecord] = []
    doc_data = data.get("data", data).get("docdata", {})
    return_period = str(data.get("data", data).get("rtnprd", "")).strip() or None
    source_row = 1

    # ── B2B Invoices ──────────────────────────────────────────────────────────
    for supplier in doc_data.get("b2b", []):
        gstin_raw = supplier.get("ctin", "")
        gstin, gstin_valid, _ = validate_gstin(gstin_raw)
        supplier_name = supplier.get("trdnm", None)

        for inv in supplier.get("inv", []):
            items = inv.get("items", [{}])
            taxable = sum(float(i.get("txval", 0) or 0) for i in items)
            igst    = sum(float(i.get("igst", 0) or 0) for i in items)
            cgst    = sum(float(i.get("cgst", 0) or 0) for i in items)
            sgst    = sum(float(i.get("sgst", 0) or 0) for i in items)
            cess    = sum(float(i.get("cess", 0) or 0) for i in items)

            inv_no, _, _  = validate_invoice_number(inv.get("inum"))
            inv_date, _, _ = validate_date(inv.get("idt"))
            itc = inv.get("itcavl")

            record = NormalizedRecord(
                gstin_supplier=gstin or gstin_raw,
                supplier_name=str(supplier_name).strip() if supplier_name else None,
                invoice_number=inv_no or "",
                invoice_date=inv_date,
                document_type=DocumentType.INVOICE,
                taxable_value=round(taxable, 2),
                igst=round(igst, 2),
                cgst=round(cgst, 2),
                sgst=round(sgst, 2),
                cess=round(cess, 2),
                total_tax=0.0,
                total_value=0.0,
                return_period=return_period,
                fy=None,
                state_code=None,
                gstin_valid=gstin_valid,
                source_row=source_row,
                row_hash=None,
                is_amended=False,
                itc_availability=str(itc) if itc else None,
            )
            record.row_hash = _compute_row_hash(record, salt)
            records.append(record)
            source_row += 1

    # ── Credit / Debit Notes ──────────────────────────────────────────────────
    for supplier in doc_data.get("cdn", []):
        gstin_raw = supplier.get("ctin", "")
        gstin, gstin_valid, _ = validate_gstin(gstin_raw)
        supplier_name = supplier.get("trdnm", None)

        for note in supplier.get("nt", []):
            items = note.get("items", [{}])
            taxable = sum(float(i.get("txval", 0) or 0) for i in items)
            igst    = sum(float(i.get("igst", 0) or 0) for i in items)
            cgst    = sum(float(i.get("cgst", 0) or 0) for i in items)
            sgst    = sum(float(i.get("sgst", 0) or 0) for i in items)
            cess    = sum(float(i.get("cess", 0) or 0) for i in items)

            note_type = str(note.get("typ", "CR")).upper()
            from app.services.parser.schemas import DocumentType as DT
            doc_type = DT.CREDIT_NOTE if "cr" in note_type.lower() else DT.DEBIT_NOTE

            inv_no, _, _ = validate_invoice_number(note.get("ntnum"))
            inv_date, _, _ = validate_date(note.get("ntdt"))

            record = NormalizedRecord(
                gstin_supplier=gstin or gstin_raw,
                supplier_name=str(supplier_name).strip() if supplier_name else None,
                invoice_number=inv_no or "",
                invoice_date=inv_date,
                document_type=doc_type,
                taxable_value=round(taxable, 2),
                igst=round(igst, 2),
                cgst=round(cgst, 2),
                sgst=round(sgst, 2),
                cess=round(cess, 2),
                total_tax=0.0,
                total_value=0.0,
                return_period=return_period,
                fy=None,
                state_code=None,
                gstin_valid=gstin_valid,
                source_row=source_row,
                row_hash=None,
                is_amended=False,
                itc_availability=None,
            )
            record.row_hash = _compute_row_hash(record, salt)
            records.append(record)
            source_row += 1

    return records


# ── Financial summary helpers ─────────────────────────────────────────────────

def _compute_totals(records: list[NormalizedRecord]) -> dict[str, float]:
    return {
        "total_taxable_value": round(sum(r.taxable_value for r in records), 2),
        "total_igst":          round(sum(r.igst          for r in records), 2),
        "total_cgst":          round(sum(r.cgst          for r in records), 2),
        "total_sgst":          round(sum(r.sgst          for r in records), 2),
        "total_cess":          round(sum(r.cess          for r in records), 2),
        "total_tax":           round(sum(r.total_tax     for r in records), 2),
    }


# ── Main public entry point ───────────────────────────────────────────────────

def parse_gst_file(
    file_bytes: bytes,
    filename: str,
    *,
    run_id: str,
    client_id: str,
    salt: str = "",
    file_type_hint: FileType | None = None,
    normalization_config: NormalizationConfig | None = None,
) -> ParseResult:
    """
    Parse a GST file (Purchase Register or GSTR-2B) from raw bytes.

    Args:
        file_bytes:           Raw file content (any supported format)
        filename:             Original filename (used for format + type detection)
        run_id:               Reconciliation run ID for traceability
        client_id:            Tenant client ID
        salt:                 Per-upload hash salt for dedup
        file_type_hint:       Override auto-detection (pass FileType enum value)
        normalization_config: Normalization behaviour overrides

    Returns:
        ParseResult — fully normalized, validated, and structured output

    Raises:
        ValueError: If file cannot be read or has no recognizable structure
    """
    logger.info("gst_parser.start", filename=filename, run_id=run_id)

    norm_config = normalization_config or NormalizationConfig(row_hash_salt=salt)
    norm_config.row_hash_salt = salt

    # ── Step 1: Detect file type and load DataFrame ──────────────────────────
    detection, df = detect_file(file_bytes, filename, hint=file_type_hint)
    logger.info(
        "gst_parser.detected",
        file_type=detection.file_type,
        format=detection.file_format,
        confidence=detection.confidence,
        reason=detection.reason,
    )

    all_issues: list[ParseIssue] = []

    # Detection confidence warning
    if detection.confidence < 0.5:
        all_issues.append(ParseIssue(
            severity=ParseSeverity.WARNING,
            message=(
                f"Low detection confidence ({detection.confidence:.0%}): {detection.reason}. "
                f"Consider specifying file_type_hint."
            ),
        ))

    # ── Step 2: GSTR-2B JSON native path ─────────────────────────────────────
    if detection.file_format == "json" and detection.file_type == FileType.GSTR_2B:
        buf = io.BytesIO(file_bytes)
        data = json.load(buf)
        records = _parse_gstr2b_json_native(data, run_id, client_id, salt)
        totals = _compute_totals(records)
        invalid_gstin_count = sum(1 for r in records if not r.gstin_valid)

        logger.info("gst_parser.done", records=len(records), path="json_native")

        return ParseResult(
            detected_file_type=detection.file_type,
            detected_format=detection.file_format,
            detection_confidence=detection.confidence,
            column_mappings=[],
            unmapped_columns=[],
            total_rows=len(records),
            parsed_rows=len(records),
            skipped_rows=0,
            warning_rows=sum(1 for r in records if not r.gstin_valid),
            records=records,
            issues=all_issues,
            total_itc_at_risk=invalid_gstin_count,
            **totals,  # type: ignore[arg-type]
        )

    # ── Step 3: DataFrame path (Excel / CSV) ──────────────────────────────────
    if df.empty:
        raise ValueError(f"No data could be extracted from '{filename}'")

    raw_columns = list(df.columns)

    # ── Step 4: Map columns ───────────────────────────────────────────────────
    mapping_results: dict[str, MappingResult] = map_columns(raw_columns)
    rename_map = apply_mapping(raw_columns, mapping_results)
    df = df.rename(columns=rename_map)

    mapped_schemas: list[ColumnMappingSchema] = []
    unmapped: list[str] = []

    for raw_col, mr in mapping_results.items():
        if mr.method != "unmapped":
            mapped_schemas.append(ColumnMappingSchema(
                raw_column=raw_col,
                canonical_column=mr.canonical,
                confidence=mr.confidence,
            ))
            if mr.confidence < 1.0:
                all_issues.append(ParseIssue(
                    severity=ParseSeverity.INFO,
                    field=raw_col,
                    message=(
                        f"Column '{raw_col}' fuzzy-matched to '{mr.canonical}' "
                        f"with {mr.confidence:.0%} confidence"
                    ),
                ))
        else:
            unmapped.append(raw_col)

    logger.info(
        "gst_parser.columns_mapped",
        mapped=len(mapped_schemas),
        unmapped=len(unmapped),
    )

    # Check for required columns after mapping
    required_after_mapping = {"gstin_supplier", "invoice_number"}
    present = set(df.columns)
    missing_required = required_after_mapping - present

    if missing_required:
        all_issues.append(ParseIssue(
            severity=ParseSeverity.ERROR,
            message=(
                f"Required columns could not be mapped: {missing_required}. "
                f"Available columns: {sorted(raw_columns)}"
            ),
        ))
        # We'll still try to normalize; the normalizer will handle the failures per-row

    # ── Step 5: Normalize rows ────────────────────────────────────────────────
    norm_result = normalize_dataframe(
        df,
        run_id=run_id,
        client_id=client_id,
        config=norm_config,
    )

    all_issues.extend(norm_result.issues)

    totals = _compute_totals(norm_result.records)
    invalid_gstin_count = sum(1 for r in norm_result.records if not r.gstin_valid)

    total_rows = len(df.dropna(how="all"))

    logger.info(
        "gst_parser.done",
        total_rows=total_rows,
        parsed=len(norm_result.records),
        skipped=norm_result.skipped_rows,
        warnings=norm_result.warning_rows,
        itc_at_risk=invalid_gstin_count,
    )

    return ParseResult(
        detected_file_type=detection.file_type,
        detected_format=detection.file_format,
        detection_confidence=detection.confidence,
        column_mappings=mapped_schemas,
        unmapped_columns=unmapped,
        total_rows=total_rows,
        parsed_rows=len(norm_result.records),
        skipped_rows=norm_result.skipped_rows,
        warning_rows=norm_result.warning_rows,
        records=norm_result.records,
        issues=all_issues,
        total_itc_at_risk=invalid_gstin_count,
        **totals,  # type: ignore[arg-type]
    )
