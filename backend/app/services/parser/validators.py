"""
Validators for GST data fields.
Each validator returns a (cleaned_value, is_valid, issue_message) tuple.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

# ── GSTIN ──────────────────────────────────────────────────────────────────────

GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
)

# Valid Indian state/UT codes
VALID_STATE_CODES = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09",
    "10", "11", "12", "13", "14", "15", "16", "17", "18",
    "19", "20", "21", "22", "23", "24", "25", "26", "27",
    "28", "29", "30", "31", "32", "33", "34", "35", "36",
    "37", "38", "97", "99",
}

GSTIN_CHECKSUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin_checksum(gstin: str) -> bool:
    """
    Validate GSTIN check digit (last character).
    Uses the GSTN checksum algorithm.
    """
    if len(gstin) != 15:
        return False
    try:
        factor = 2
        total = 0
        for char in gstin[:-1]:
            idx = GSTIN_CHECKSUM_CHARS.index(char)
            addend = factor * idx
            factor = 1 if factor == 2 else 2
            addend = (addend // 36) + (addend % 36)
            total += addend
        remainder = total % 36
        expected = GSTIN_CHECKSUM_CHARS[(36 - remainder) % 36]
        return gstin[-1] == expected
    except (ValueError, IndexError):
        return False


def validate_gstin(raw: Any) -> tuple[str, bool, str | None]:
    """
    Validate and normalize a GSTIN value.

    Returns:
        (normalized_gstin, is_valid, issue_message_or_None)
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "", False, "GSTIN is missing"

    gstin = str(raw).strip().upper().replace(" ", "").replace("-", "")

    if len(gstin) == 0:
        return "", False, "GSTIN is empty"

    if len(gstin) != 15:
        return gstin, False, f"GSTIN '{gstin}' has {len(gstin)} characters (expected 15)"

    if not GSTIN_RE.match(gstin):
        return gstin, False, f"GSTIN '{gstin}' does not match the expected format"

    state_code = gstin[:2]
    if state_code not in VALID_STATE_CODES:
        return gstin, False, f"GSTIN '{gstin}' has invalid state code '{state_code}'"

    if not _gstin_checksum(gstin):
        return gstin, False, f"GSTIN '{gstin}' has an invalid check digit"

    return gstin, True, None


# ── Date ──────────────────────────────────────────────────────────────────────

DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",   # DD/MM/YYYY
    "%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d",   # YYYY/MM/DD (ISO)
    "%d/%m/%y", "%d-%m-%y",               # DD/MM/YY (2-digit year)
    "%m/%d/%Y", "%m-%d-%Y",               # MM/DD/YYYY (US format)
    "%d %b %Y", "%d %B %Y",              # 01 Jan 2024
    "%b %d, %Y", "%B %d, %Y",            # Jan 01, 2024
    "%d-%b-%Y", "%d-%B-%Y",              # 01-Jan-2024
    "%Y%m%d",                             # 20240101 (compact)
    "%d%m%Y",                             # 01012024
]


def validate_date(raw: Any) -> tuple[date | None, bool, str | None]:
    """
    Parse and validate an invoice date from any common format.

    Returns:
        (parsed_date_or_None, is_valid, issue_message_or_None)
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, False, "Invoice date is missing"

    # Already a Python date
    if isinstance(raw, date):
        return raw, True, None

    # Pandas Timestamp
    if isinstance(raw, pd.Timestamp):
        return raw.date(), True, None

    raw_str = str(raw).strip()

    if not raw_str or raw_str.lower() in ("nan", "none", "null", "n/a", "-"):
        return None, False, f"Invoice date is empty or null-like: '{raw_str}'"

    # Try parsing with each format
    for fmt in DATE_FORMATS:
        try:
            parsed = pd.to_datetime(raw_str, format=fmt, dayfirst=True)
            dt = parsed.date()
            # Sanity check: GST was introduced in July 2017
            if dt.year < 2017:
                return dt, False, f"Invoice date {dt} is before GST implementation (July 2017)"
            if dt.year > 2030:
                return dt, False, f"Invoice date {dt} is suspiciously far in the future"
            return dt, True, None
        except (ValueError, TypeError):
            continue

    # Last-resort: Pandas flexible parser
    try:
        parsed = pd.to_datetime(raw_str, dayfirst=True)
        dt = parsed.date()
        return dt, True, f"Date '{raw_str}' parsed with inferred format (verify correctness)"
    except Exception:
        return None, False, f"Cannot parse invoice date: '{raw_str}'"


# ── Amounts ───────────────────────────────────────────────────────────────────

def validate_amount(raw: Any, field_name: str) -> tuple[float, bool, str | None]:
    """
    Parse and validate a monetary amount.

    Returns:
        (cleaned_amount, is_valid, issue_message_or_None)
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0, True, None   # Missing amount = 0 (not an error, just zero)

    if isinstance(raw, (int, float)):
        val = float(raw)
        if val < 0:
            return abs(val), False, f"{field_name} is negative ({val}); using absolute value"
        return round(val, 2), True, None

    raw_str = str(raw).strip()

    if not raw_str or raw_str.lower() in ("nan", "none", "null", "n/a", "-", ""):
        return 0.0, True, None

    # Strip currency symbols, commas, spaces
    cleaned = (
        raw_str
        .replace("₹", "")
        .replace("Rs", "")
        .replace("rs", "")
        .replace("INR", "")
        .replace(",", "")
        .replace(" ", "")
        .strip()
    )

    # Handle parenthetical negatives: (1234.56) → -1234.56
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]

    try:
        val = float(cleaned)
        if val < 0:
            return abs(val), False, f"{field_name} is negative ({val}); using absolute value"
        return round(val, 2), True, None
    except ValueError:
        return 0.0, False, f"Cannot parse {field_name} amount: '{raw_str}'"


# ── Invoice Number ────────────────────────────────────────────────────────────

def validate_invoice_number(raw: Any) -> tuple[str, bool, str | None]:
    """
    Clean and validate an invoice number.

    Returns:
        (cleaned_invoice_no, is_valid, issue_message_or_None)
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "", False, "Invoice number is missing"

    inv = str(raw).strip()

    if not inv or inv.lower() in ("nan", "none", "null", "n/a", "-"):
        return "", False, f"Invoice number is empty or null-like: '{inv}'"

    # Max length per GST portal: 16 alphanumeric characters
    if len(inv) > 16:
        # Trim and warn (some systems send longer refs)
        return inv[:16].upper(), False, (
            f"Invoice number '{inv}' exceeds 16 characters (GST portal limit); truncated"
        )

    return inv.upper(), True, None


# ── Return Period ─────────────────────────────────────────────────────────────

PERIOD_RE = re.compile(r"^(0[1-9]|1[0-2])(\d{4})$")   # MMYYYY


def validate_return_period(raw: Any) -> tuple[str | None, bool, str | None]:
    """
    Validate and normalize a GST return period to MMYYYY format.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, True, None  # Optional field

    period = str(raw).strip().replace("-", "").replace("/", "").replace(" ", "")

    if PERIOD_RE.match(period):
        return period, True, None

    # Common alternate: "Apr-2024", "April 2024", "04/2024"
    try:
        parsed = pd.to_datetime(period, format="%b-%Y")
        return parsed.strftime("%m%Y"), True, None
    except Exception:
        pass
    try:
        parsed = pd.to_datetime(period, format="%B %Y")
        return parsed.strftime("%m%Y"), True, None
    except Exception:
        pass
    try:
        parsed = pd.to_datetime(period, format="%m/%Y")
        return parsed.strftime("%m%Y"), True, None
    except Exception:
        pass

    return period, False, f"Cannot normalize return period: '{raw}'"


# ── Document Type ─────────────────────────────────────────────────────────────

DOC_TYPE_MAP: dict[str, str] = {
    "inv": "INV", "invoice": "INV", "tax invoice": "INV",
    "b2b": "INV", "regular": "INV",
    "cr": "CR", "crn": "CR", "credit note": "CR", "credit": "CR",
    "dr": "DR", "drn": "DR", "debit note": "DR", "debit": "DR",
    "amd": "AMD", "amended": "AMD", "amendment": "AMD",
}


def validate_document_type(raw: Any) -> tuple[str, bool, str | None]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "INV", True, None  # Default to invoice

    raw_str = str(raw).strip().lower()
    mapped = DOC_TYPE_MAP.get(raw_str)
    if mapped:
        return mapped, True, None

    return "INV", False, f"Unknown document type '{raw}'; defaulting to INV"
