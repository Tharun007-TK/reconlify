"""
Purchase Register parser using Pandas.
Supports .xlsx, .xls, .csv formats.
Normalizes column names to canonical schema.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import structlog

from app.core.exceptions import ParseError

logger = structlog.get_logger(__name__)

# ── Column aliases ────────────────────────────────────────────────────────────
# Maps various real-world column names to canonical names.
# Add more aliases as you encounter different PR formats.

COLUMN_ALIASES: dict[str, list[str]] = {
    "invoice_number": [
        "invoice no", "invoice number", "bill no", "bill number",
        "voucher no", "inv no", "invoice_no", "invoice_number",
    ],
    "gstin_supplier": [
        "gstin", "supplier gstin", "vendor gstin", "party gstin",
        "gstin of supplier", "gstin_supplier", "party_gstin",
    ],
    "supplier_name": [
        "supplier name", "vendor name", "party name", "vendor",
        "supplier", "party", "name", "supplier_name",
    ],
    "invoice_date": [
        "invoice date", "bill date", "date", "invoice_date",
        "voucher date", "transaction date",
    ],
    "taxable_value": [
        "taxable value", "taxable amount", "base amount", "value",
        "taxable_value", "net amount", "basic value",
    ],
    "igst": ["igst", "integrated tax", "igst amount", "igst_amount"],
    "cgst": ["cgst", "central tax", "cgst amount", "cgst_amount"],
    "sgst": ["sgst", "state tax", "sgst amount", "sgst_amount", "utgst"],
    "cess": ["cess", "cess amount", "cess_amount"],
    "return_period": ["return period", "filing period", "return_period"],
}


@dataclass
class ParseResult:
    records: list[dict]  # type: ignore[type-arg]
    total_rows: int
    parsed_rows: int
    error_rows: int
    errors: list[dict] = field(default_factory=list)  # type: ignore[type-arg]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw column names to canonical names."""
    col_map: dict[str, str] = {}
    raw_cols_lower = {c.strip().lower(): c for c in df.columns}

    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in raw_cols_lower:
                col_map[raw_cols_lower[alias.lower()]] = canonical
                break

    df = df.rename(columns=col_map)
    return df


def _validate_gstin(gstin: str) -> bool:
    import re
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    return bool(re.match(pattern, str(gstin).strip().upper()))


def _coerce_date(val: object) -> date | None:
    if pd.isna(val):  # type: ignore[arg-type]
        return None
    if isinstance(val, (date, pd.Timestamp)):
        return pd.Timestamp(val).date()
    try:
        return pd.to_datetime(str(val), dayfirst=True).date()
    except Exception:
        return None


def _coerce_numeric(val: object) -> float:
    if pd.isna(val):  # type: ignore[arg-type]
        return 0.0
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _compute_row_hash(row: dict, salt: str) -> str:  # type: ignore[type-arg]
    """Deterministic SHA-256 hash of canonical row fields."""
    key = "|".join([
        str(row.get("gstin_supplier", "")).upper().strip(),
        str(row.get("invoice_number", "")).upper().strip(),
        str(row.get("invoice_date", "")),
        str(round(row.get("taxable_value", 0.0), 2)),
        salt,
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def parse_purchase_register(
    file_bytes: bytes,
    filename: str,
    salt: str,
    *,
    job_id: str,
    client_id: str,
) -> ParseResult:
    """
    Parse a Purchase Register file and return normalized records.

    Args:
        file_bytes: Raw file content
        filename:   Original filename (used to detect format)
        salt:       Row hash salt (per-upload UUID)
        job_id:     Reconciliation run ID
        client_id:  Tenant client ID

    Returns:
        ParseResult with normalized records ready for DB insertion
    """
    logger.info("parser.pr.start", filename=filename, job_id=job_id)

    try:
        buf = io.BytesIO(file_bytes)
        ext = filename.lower().rsplit(".", 1)[-1]

        if ext in ("xlsx", "xls"):
            # Find the first sheet with data
            xl = pd.ExcelFile(buf)
            df = None
            for sheet in xl.sheet_names:
                candidate = xl.parse(sheet_name=sheet, header=None)
                if len(candidate) > 1:
                    df = xl.parse(sheet_name=sheet)
                    break
            if df is None:
                raise ParseError("No data found in any sheet of the Excel file.")
        elif ext == "csv":
            df = pd.read_csv(buf)
        elif ext == "json":
            df = pd.read_json(buf)
        else:
            raise ParseError(f"Unsupported file format: .{ext}")

    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to read file '{filename}': {exc}") from exc

    logger.info("parser.pr.loaded", rows=len(df), cols=list(df.columns), job_id=job_id)

    # Normalize column names
    df = _normalize_columns(df)

    # Check required columns
    required = {"invoice_number", "gstin_supplier", "taxable_value"}
    missing = required - set(df.columns)
    if missing:
        raise ParseError(
            f"Required columns not found: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    # Drop fully empty rows
    df = df.dropna(how="all")
    total_rows = len(df)

    records: list[dict] = []  # type: ignore[type-arg]
    errors: list[dict] = []  # type: ignore[type-arg]

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # 1-indexed, account for header

        gstin = str(row.get("gstin_supplier", "")).strip().upper()
        invoice_no = str(row.get("invoice_number", "")).strip()

        if not gstin or not invoice_no:
            errors.append({"row": row_num, "reason": "Missing GSTIN or invoice number"})
            continue

        if not _validate_gstin(gstin):
            errors.append({"row": row_num, "reason": f"Invalid GSTIN format: {gstin}"})
            # Don't skip — still include with flag

        record: dict = {  # type: ignore[type-arg]
            "run_id": job_id,
            "client_id": client_id,
            "invoice_number": invoice_no,
            "gstin_supplier": gstin,
            "supplier_name": str(row.get("supplier_name", "")).strip() or None,
            "invoice_date": _coerce_date(row.get("invoice_date")),
            "taxable_value": _coerce_numeric(row.get("taxable_value")),
            "igst": _coerce_numeric(row.get("igst", 0)),
            "cgst": _coerce_numeric(row.get("cgst", 0)),
            "sgst": _coerce_numeric(row.get("sgst", 0)),
            "cess": _coerce_numeric(row.get("cess", 0)),
            "return_period": str(row.get("return_period", "")).strip() or None,
        }
        record["row_hash"] = _compute_row_hash(record, salt)
        records.append(record)

    error_rows = len(errors)
    parsed_rows = total_rows - error_rows

    logger.info(
        "parser.pr.complete",
        job_id=job_id,
        total=total_rows,
        parsed=parsed_rows,
        errors=error_rows,
    )

    return ParseResult(
        records=records,
        total_rows=total_rows,
        parsed_rows=parsed_rows,
        error_rows=error_rows,
        errors=errors,
    )
