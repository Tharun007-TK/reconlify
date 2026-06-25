"""
Column alias mapping engine.
Maps arbitrary raw column names (from any GST tool, CA software, or portal)
to canonical internal field names.

Strategy:
1. Exact match (normalized lowercase + stripped)
2. Fuzzy match via RapidFuzz token_sort_ratio
3. Fallback: column marked as unmapped
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

# ── Canonical field definitions ───────────────────────────────────────────────
# Keys = canonical internal names
# Values = list of known aliases (all lowercased)

CANONICAL_ALIASES: dict[str, list[str]] = {
    # ── Identity ────────────────────────────────────────────────────────────
    "gstin_supplier": [
        "gstin", "gstin of supplier", "supplier gstin", "vendor gstin",
        "party gstin", "gstin_supplier", "gstin no", "gst no",
        "gst number", "gstn", "gstin number", "party gst",
        "supplier gst no", "gstin of the supplier",
        "counterparty gstin", "ctin", "recipient gstin",
    ],
    "supplier_name": [
        "supplier name", "vendor name", "party name", "supplier",
        "vendor", "party", "name", "supplier_name", "trade name",
        "legal name", "trade_name", "legal_name", "party trade name",
        "supplier trade name", "company name", "firm name",
        "registered name", "trdnm",
    ],
    "invoice_number": [
        "invoice no", "invoice number", "invoice_no", "invoice_number",
        "bill no", "bill number", "voucher no", "voucher number",
        "inv no", "inv number", "document no", "doc no",
        "reference no", "ref no", "challan no", "inum",
        "invoice reference", "bill reference", "note number",
        "credit note no", "debit note no", "note no",
    ],
    "invoice_date": [
        "invoice date", "invoice_date", "bill date", "bill_date",
        "date", "voucher date", "transaction date", "doc date",
        "document date", "idt", "date of invoice", "inv date",
        "tax invoice date", "credit note date", "debit note date",
        "note date", "ntdt",
    ],
    "document_type": [
        "document type", "doc type", "type", "inv type",
        "document_type", "note type", "transaction type",
        "supply type", "typ",
    ],

    # ── Financial ───────────────────────────────────────────────────────────
    "taxable_value": [
        "taxable value", "taxable amount", "taxable_value",
        "taxable_amount", "base amount", "basic value",
        "value of taxable supply", "net value", "net amount",
        "assessable value", "supply value", "value", "txval",
        "invoice value (taxable)", "chargeable value",
        "basic amount", "amount before tax", "gross taxable value",
    ],
    "igst": [
        "igst", "integrated gst", "igst amount", "igst_amount",
        "integrated tax", "igst tax", "igst paid",
        "integrated goods and services tax", "igst value",
        "central gst (igst)", "igst (₹)", "igst rs",
    ],
    "cgst": [
        "cgst", "central gst", "cgst amount", "cgst_amount",
        "central tax", "cgst tax", "cgst paid",
        "central goods and services tax", "cgst value",
        "cgst (₹)", "cgst rs",
    ],
    "sgst": [
        "sgst", "state gst", "sgst amount", "sgst_amount",
        "state tax", "sgst tax", "sgst paid",
        "state goods and services tax", "sgst value",
        "utgst", "ut tax", "union territory gst",
        "sgst/utgst", "sgst (₹)", "sgst rs",
    ],
    "cess": [
        "cess", "cess amount", "cess_amount", "compensation cess",
        "cess paid", "cess value", "cess tax", "cess (₹)",
    ],
    "total_value": [
        "total", "total value", "total amount", "invoice total",
        "total invoice value", "gross amount", "gross value",
        "invoice amount", "total (₹)",
    ],

    # ── GST Metadata ────────────────────────────────────────────────────────
    "return_period": [
        "return period", "return_period", "filing period",
        "tax period", "period", "rtnprd", "gst period",
        "return month", "filing month",
    ],
    "fy": [
        "financial year", "fy", "fiscal year", "fin year",
        "year", "financial_year", "accounting year",
    ],
    "itc_availability": [
        "itc availability", "itc eligible", "input tax credit",
        "itc_availability", "eligibility", "itc",
        "input credit availability",
    ],
    "is_amended": [
        "amended", "is amended", "is_amended", "amendment",
        "amendment flag", "original/amended",
    ],

    # ── Additional identifiers (GSTR-2B specific) ────────────────────────
    "place_of_supply": [
        "place of supply", "pos", "supply state", "state of supply",
        "place_of_supply",
    ],
    "hsn_sac": [
        "hsn", "sac", "hsn/sac", "hsn code", "sac code",
        "hsn_sac", "tariff code",
    ],
}

# Fuzzy match threshold — below this score, a column is left unmapped
FUZZY_THRESHOLD = 72.0


@dataclass
class MappingResult:
    canonical: str
    raw_column: str
    confidence: float    # 0.0 – 1.0
    method: str          # "exact" | "fuzzy" | "unmapped"


def _normalize_col(name: str) -> str:
    """Normalize column name: lowercase, strip whitespace, collapse inner spaces."""
    return " ".join(name.lower().strip().split())


def _build_alias_index() -> dict[str, str]:
    """Build a flat dict: normalized_alias → canonical_name for O(1) exact lookup."""
    index: dict[str, str] = {}
    for canonical, aliases in CANONICAL_ALIASES.items():
        for alias in aliases:
            norm = _normalize_col(alias)
            index[norm] = canonical
    return index


_ALIAS_INDEX: dict[str, str] = _build_alias_index()


def map_columns(raw_columns: list[str]) -> dict[str, MappingResult]:
    """
    Map a list of raw column names to canonical names.

    Args:
        raw_columns: Column headers as they appear in the raw file

    Returns:
        Dict mapping raw_column → MappingResult.
        Unmapped columns still appear with method="unmapped".
    """
    results: dict[str, MappingResult] = {}
    used_canonicals: set[str] = set()

    for raw in raw_columns:
        norm = _normalize_col(raw)

        # ── 1. Exact match ──────────────────────────────────────────────────
        if norm in _ALIAS_INDEX:
            canonical = _ALIAS_INDEX[norm]
            if canonical not in used_canonicals:
                results[raw] = MappingResult(
                    canonical=canonical,
                    raw_column=raw,
                    confidence=1.0,
                    method="exact",
                )
                used_canonicals.add(canonical)
                continue

        # ── 2. Fuzzy match ──────────────────────────────────────────────────
        best_score = 0.0
        best_canonical = ""
        best_alias = ""

        for canonical, aliases in CANONICAL_ALIASES.items():
            if canonical in used_canonicals:
                continue
            for alias in aliases:
                score = fuzz.token_sort_ratio(norm, _normalize_col(alias))
                if score > best_score:
                    best_score = score
                    best_canonical = canonical
                    best_alias = alias

        if best_score >= FUZZY_THRESHOLD and best_canonical not in used_canonicals:
            results[raw] = MappingResult(
                canonical=best_canonical,
                raw_column=raw,
                confidence=round(best_score / 100.0, 3),
                method="fuzzy",
            )
            used_canonicals.add(best_canonical)
        else:
            # ── 3. Unmapped ─────────────────────────────────────────────────
            results[raw] = MappingResult(
                canonical=raw,  # Keep original name
                raw_column=raw,
                confidence=0.0,
                method="unmapped",
            )

    return results


def apply_mapping(
    df_columns: list[str],
    mapping: dict[str, MappingResult],
) -> dict[str, str]:
    """
    Build a rename dict for use with DataFrame.rename(columns=...).
    Only renames columns that were successfully mapped.
    """
    return {
        raw: result.canonical
        for raw, result in mapping.items()
        if result.method != "unmapped"
    }
