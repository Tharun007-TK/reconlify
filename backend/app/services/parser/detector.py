"""
File type and format auto-detector.
Determines whether a file is a Purchase Register, GSTR-2B, GSTR-2A,
and what format it is (xlsx, csv, json).

Detection strategy (ordered by reliability):
1. JSON structure fingerprint (GSTR-2B official portal format)
2. Column presence scoring (higher match = stronger signal)
3. Sheet name heuristics (Excel only)
4. Filename heuristics (last resort)
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.services.parser.schemas import FileType

# ── Signal columns for each file type ────────────────────────────────────────
# Column names that strongly indicate a specific file type.
# Each canonical name is worth 1 point; score is normalized 0-1.

PR_SIGNALS = [
    "taxable_value", "cgst", "sgst", "igst",
    "invoice_number", "invoice_date", "gstin_supplier",
    "supplier_name", "fy", "return_period",
]

GSTR2B_SIGNALS = [
    "gstin_supplier", "invoice_number", "taxable_value",
    "igst", "cgst", "sgst", "itc_availability",
    "is_amended", "place_of_supply", "document_type",
]

# JSON keys present in GSTR-2B official portal export
GSTR2B_JSON_FINGERPRINT = {"docdata", "b2b", "cdn", "rtnprd", "data"}


@dataclass
class DetectionResult:
    file_type: FileType
    file_format: str         # "xlsx" | "csv" | "json"
    confidence: float        # 0.0 – 1.0
    reason: str              # Human-readable explanation
    sheet_name: str | None = None


def _score_columns(df_columns: list[str], signals: list[str]) -> float:
    """Score how many signal columns appear in df_columns (after normalization)."""
    normalized = {c.lower().strip() for c in df_columns}
    hits = sum(
        1 for sig in signals
        if any(sig.replace("_", " ") in col or sig in col for col in normalized)
    )
    return round(hits / len(signals), 3)


def _detect_from_json(data: dict[str, Any]) -> DetectionResult:
    """Detect file type from JSON structure."""
    keys = set()
    _flatten_keys(data, keys, depth=0, max_depth=3)

    # Check for official GSTR-2B portal structure
    intersection = keys & GSTR2B_JSON_FINGERPRINT
    if len(intersection) >= 2:
        return DetectionResult(
            file_type=FileType.GSTR_2B,
            file_format="json",
            confidence=min(0.7 + 0.1 * len(intersection), 1.0),
            reason=f"JSON keys match GSTR-2B portal structure: {intersection}",
        )

    return DetectionResult(
        file_type=FileType.UNKNOWN,
        file_format="json",
        confidence=0.3,
        reason="JSON structure does not match any known GST format",
    )


def _flatten_keys(obj: Any, out: set[str], depth: int, max_depth: int) -> None:
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k.lower())
            _flatten_keys(v, out, depth + 1, max_depth)
    elif isinstance(obj, list) and obj:
        _flatten_keys(obj[0], out, depth + 1, max_depth)


def _detect_from_dataframe(
    df: pd.DataFrame,
    filename: str,
    sheet_name: str | None = None,
) -> DetectionResult:
    """Detect file type from DataFrame column names."""
    cols = list(df.columns)

    pr_score  = _score_columns(cols, PR_SIGNALS)
    b2b_score = _score_columns(cols, GSTR2B_SIGNALS)

    # Tie-break via filename heuristics
    fname_lower = filename.lower()
    if any(kw in fname_lower for kw in ("2b", "gstr2b", "gstr-2b", "gstr_2b", "2a", "gstr2a")):
        b2b_score += 0.2
    if any(kw in fname_lower for kw in ("pr", "purchase", "purchase_register", "purchases")):
        pr_score += 0.2

    # Sheet name heuristics
    if sheet_name:
        sn = sheet_name.lower()
        if any(kw in sn for kw in ("2b", "gstr", "b2b")):
            b2b_score += 0.1
        if any(kw in sn for kw in ("purchase", "pr", "register")):
            pr_score += 0.1

    if pr_score == 0 and b2b_score == 0:
        return DetectionResult(
            file_type=FileType.UNKNOWN,
            file_format="xlsx",
            confidence=0.1,
            reason=f"No recognizable GST columns found. Columns: {cols[:10]}",
            sheet_name=sheet_name,
        )

    if pr_score >= b2b_score:
        confidence = min(pr_score, 1.0)
        return DetectionResult(
            file_type=FileType.PURCHASE_REGISTER,
            file_format="xlsx",
            confidence=confidence,
            reason=f"Purchase Register signal score: {pr_score:.2f} vs GSTR-2B: {b2b_score:.2f}",
            sheet_name=sheet_name,
        )
    else:
        confidence = min(b2b_score, 1.0)
        return DetectionResult(
            file_type=FileType.GSTR_2B,
            file_format="xlsx",
            confidence=confidence,
            reason=f"GSTR-2B signal score: {b2b_score:.2f} vs PR: {pr_score:.2f}",
            sheet_name=sheet_name,
        )


def _find_header_row(df_raw: pd.DataFrame) -> int:
    """
    Find the actual header row in a raw DataFrame.
    Some files have title rows, date rows, or blank rows before the data.
    Scans first 10 rows looking for the row with the most string values.
    """
    best_row = 0
    best_score = 0

    for i in range(min(10, len(df_raw))):
        row = df_raw.iloc[i]
        str_count = sum(1 for v in row if isinstance(v, str) and len(v.strip()) > 0)
        if str_count > best_score:
            best_score = str_count
            best_row = i

    return best_row


def detect_file(
    file_bytes: bytes,
    filename: str,
    *,
    hint: FileType | None = None,
) -> tuple[DetectionResult, pd.DataFrame]:
    """
    Auto-detect file type and load into a clean DataFrame.

    Args:
        file_bytes: Raw file bytes
        filename:   Original filename (used as detection signal)
        hint:       Optional override if caller already knows the type

    Returns:
        (DetectionResult, cleaned DataFrame with normalized header)

    Raises:
        ValueError: If file format cannot be determined or parsed
    """
    buf = io.BytesIO(file_bytes)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # ── JSON ─────────────────────────────────────────────────────────────────
    if ext == "json":
        try:
            data = json.load(buf)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON file: {exc}") from exc

        detection = _detect_from_json(data)

        if hint:
            detection.file_type = hint

        # Flatten JSON to DataFrame (handled separately in gstr2b parser)
        # Return an empty DataFrame as signal to use the JSON path
        return detection, pd.DataFrame()

    # ── Excel ─────────────────────────────────────────────────────────────────
    if ext in ("xlsx", "xls"):
        try:
            xl = pd.ExcelFile(buf)
        except Exception as exc:
            raise ValueError(f"Cannot read Excel file '{filename}': {exc}") from exc

        best_result: DetectionResult | None = None
        best_df: pd.DataFrame | None = None

        for sheet in xl.sheet_names:
            try:
                # Read raw first to find header
                raw = xl.parse(sheet_name=sheet, header=None)
                if raw.empty or len(raw) < 2:
                    continue

                header_row = _find_header_row(raw)
                df = xl.parse(sheet_name=sheet, header=header_row)
                df = df.dropna(how="all").dropna(axis=1, how="all")

                if df.empty:
                    continue

                result = _detect_from_dataframe(df, filename, sheet_name=sheet)
                result.file_format = "xlsx"

                if best_result is None or result.confidence > best_result.confidence:
                    best_result = result
                    best_df = df

            except Exception:
                continue

        if best_result is None or best_df is None:
            raise ValueError(f"No usable data found in any sheet of '{filename}'")

        if hint:
            best_result.file_type = hint

        return best_result, best_df

    # ── CSV ──────────────────────────────────────────────────────────────────
    if ext == "csv":
        # Try common encodings
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                buf.seek(0)
                raw = pd.read_csv(buf, encoding=encoding, header=None, dtype=str)
                break
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        else:
            raise ValueError(f"Cannot decode CSV file '{filename}' with any common encoding")

        if raw.empty:
            raise ValueError(f"CSV file '{filename}' is empty")

        header_row = _find_header_row(raw)
        buf.seek(0)
        df = pd.read_csv(buf, encoding=encoding, header=header_row, dtype=str)
        df = df.dropna(how="all").dropna(axis=1, how="all")

        result = _detect_from_dataframe(df, filename)
        result.file_format = "csv"

        if hint:
            result.file_type = hint

        return result, df

    raise ValueError(
        f"Unsupported file extension '.{ext}'. "
        f"Accepted formats: xlsx, xls, csv, json"
    )
