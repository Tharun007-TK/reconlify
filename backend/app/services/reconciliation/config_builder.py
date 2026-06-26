"""
Reconlify YAML Config Builder.

Generates a reconlify CLI config dict (and optionally writes it to disk)
from the normalized column names produced by normalizer.py.

Usage (in-process):
    from app.services.reconciliation.config_builder import build_recon_config, write_config

    cfg = build_recon_config(pr_csv_path, gstr2b_csv_path)
    config_path = write_config(cfg, work_dir / "config.yaml")

Usage (custom columns):
    cfg = build_recon_config(
        pr_csv_path,
        gstr2b_csv_path,
        pr_columns=["invoice_number", "gstin_supplier", ...],
        gstr2b_columns=["invoice_number", "gstin_supplier", ...],
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


# ── Canonical column definitions ───────────────────────────────────────────────
#
# These are the column names output by normalizer.py after the column_mapper
# has applied the canonical mapping.  Any column present in BOTH source (PR)
# and target (GSTR-2B) after normalization is eligible for reconciliation.

# Composite key — uniquely identifies a GST invoice across both sides.
# GST regulation requires BOTH invoice_number AND gstin_supplier for
# unambiguous B2B matching.  A single key is never sufficient.
# This constant is immutable and must never be reduced to a single field.
COMPOSITE_KEYS: tuple[str, ...] = ("invoice_number", "gstin_supplier")

# Minimum required key set — enforced at config-build time and at CSV write time.
_REQUIRED_KEY_FIELDS: frozenset[str] = frozenset(COMPOSITE_KEYS)

# Full canonical column set (in order) produced by normalizer.py
NORMALIZED_PR_COLUMNS: list[str] = [
    "gstin_supplier",
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "document_type",
    "taxable_value",
    "igst",
    "cgst",
    "sgst",
    "cess",
    "total_tax",
    "total_value",
    "return_period",
    "fy",
    "state_code",
    "itc_availability",
    "is_amended",
    "row_hash",
]

NORMALIZED_GSTR2B_COLUMNS: list[str] = [
    "gstin_supplier",
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "document_type",
    "taxable_value",
    "igst",
    "cgst",
    "sgst",
    "cess",
    "total_tax",
    "total_value",
    "return_period",
    "fy",
    "state_code",
    "itc_availability",
    "is_amended",
    "row_hash",
]

# Columns that exist in both and should be compared (keys are excluded)
_DEFAULT_COMPARE_COLUMNS: list[str] = [
    "invoice_date",
    "document_type",
    "taxable_value",
    "igst",
    "cgst",
    "sgst",
    "cess",
    "supplier_name",
    "return_period",
]

# ── Tolerance rules (per business rules, ₹ absolute) ──────────────────────────
#
# taxable_value: ₹1.00  — rounding differences in accounting software
# igst/cgst/sgst: ₹0.50 — per-component tax rounding
# cess: ₹0.50           — cess rounding
DEFAULT_TOLERANCE: dict[str, dict[str, Any]] = {
    "taxable_value": {"type": "absolute", "value": 1.0},
    "igst":          {"type": "absolute", "value": 0.5},
    "cgst":          {"type": "absolute", "value": 0.5},
    "sgst":          {"type": "absolute", "value": 0.5},
    "cess":          {"type": "absolute", "value": 0.5},
}

# ── Comparison behaviour ───────────────────────────────────────────────────────
DEFAULT_COMPARE_OPTIONS: dict[str, Any] = {
    "trim_whitespace":  True,
    "case_insensitive": True,
    "normalize_nulls":  ["", "NULL", "null", "N/A"],
}


# ── Public dataclass ───────────────────────────────────────────────────────────

@dataclass
class ReconConfig:
    """
    Typed representation of a reconlify run config.

    Call .to_dict() to produce the YAML-serialisable mapping, or
    write_config() to serialise directly to a file.
    """
    source: str                              # Absolute path to pr_normalized.csv
    target: str                              # Absolute path to gstr2b_normalized.csv
    keys: list[str]                          # Composite match keys
    column_mapping: dict[str, str]           # PR col → GSTR-2B col
    tolerance: dict[str, dict[str, Any]]     # Per-field tolerance rules
    compare: dict[str, Any]                  # Comparison options
    type: str = "tabular"                    # reconlify dataset type

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for yaml.dump()."""
        return {
            "type":           self.type,
            "source":         self.source,
            "target":         self.target,
            "keys":           self.keys,
            "column_mapping": self.column_mapping,
            "tolerance":      self.tolerance,
            "compare":        self.compare,
        }


# ── Builder ────────────────────────────────────────────────────────────────────

def build_recon_config(
    pr_csv_path: str | Path,
    gstr2b_csv_path: str | Path,
    *,
    pr_columns: list[str] | None = None,
    gstr2b_columns: list[str] | None = None,
    tolerance: dict[str, dict[str, Any]] | None = None,
    compare: dict[str, Any] | None = None,
    keys: list[str] | None = None,
) -> ReconConfig:
    """
    Build a ReconConfig from the normalized PR and GSTR-2B CSV column names.

    Args:
        pr_csv_path:     Path to the written pr_normalized.csv
        gstr2b_csv_path: Path to the written gstr2b_normalized.csv
        pr_columns:      Override list of PR column names.
                         Defaults to NORMALIZED_PR_COLUMNS.
        gstr2b_columns:  Override list of GSTR-2B column names.
                         Defaults to NORMALIZED_GSTR2B_COLUMNS.
        tolerance:       Override per-field tolerance rules.
                         Defaults to DEFAULT_TOLERANCE.
        compare:         Override comparison options.
                         Defaults to DEFAULT_COMPARE_OPTIONS.
        keys:            Override composite match keys.
                         Defaults to COMPOSITE_KEYS.

    Returns:
        ReconConfig — call .to_dict() or write_config() to use it.

    Raises:
        ValueError: If any composite key is absent from either column list.
    """
    pr_cols    = pr_columns    or NORMALIZED_PR_COLUMNS
    b2_cols    = gstr2b_columns or NORMALIZED_GSTR2B_COLUMNS

    # ── Key enforcement: always use the full GST composite key ────────────────
    # Callers may NOT reduce the key set below the required pair.  If the
    # caller passes custom keys, we merge them with the required fields so
    # that invoice_number + gstin_supplier are always present.
    if keys is not None:
        caller_keys = list(keys)
        missing_from_caller = _REQUIRED_KEY_FIELDS - set(caller_keys)
        if missing_from_caller:
            raise ValueError(
                f"GST reconciliation requires composite keys {sorted(_REQUIRED_KEY_FIELDS)}. "
                f"The provided key list {caller_keys!r} is missing: {sorted(missing_from_caller)}. "
                f"A single key is never sufficient for B2B invoice matching."
            )
        match_keys: list[str] = caller_keys
    else:
        match_keys = list(COMPOSITE_KEYS)

    # Guard: all composite keys must exist in both column lists
    for k in match_keys:
        if k not in pr_cols:
            raise ValueError(
                f"Composite key '{k}' is missing from the PR column list. "
                f"Available PR columns: {pr_cols}"
            )
        if k not in b2_cols:
            raise ValueError(
                f"Composite key '{k}' is missing from the GSTR-2B column list. "
                f"Available GSTR-2B columns: {b2_cols}"
            )

    # Build column_mapping: for every column present in *both* files (beyond the
    # key columns), map PR column name → GSTR-2B column name.  Since both sides
    # use the same canonical names after normalization, this is a 1-to-1 identity
    # mapping — but making it explicit lets reconlify handle any future renames.
    pr_set = set(pr_cols)
    b2_set = set(b2_cols)
    key_set = set(match_keys)

    # Include all overlapping non-key columns plus tolerance/compare targets
    compare_candidates = (pr_set & b2_set) - key_set

    column_mapping: dict[str, str] = {
        col: col for col in sorted(compare_candidates)
    }

    return ReconConfig(
        source=str(pr_csv_path),
        target=str(gstr2b_csv_path),
        keys=list(match_keys),
        column_mapping=column_mapping,
        tolerance=tolerance or DEFAULT_TOLERANCE,
        compare=compare or DEFAULT_COMPARE_OPTIONS,
    )


# ── CSV key validator ──────────────────────────────────────────────────────────

def validate_csv_keys(csv_path: str | Path, label: str = "CSV") -> None:
    """
    Read the first pass of a normalized CSV and verify that every row has
    non-null, non-empty values in both composite key columns.

    This is a pre-flight check that runs before writing config.yaml so we
    surface data-quality errors early — before the CLI is even invoked.

    Args:
        csv_path: Path to the normalized CSV (pr_normalized.csv or
                  gstr2b_normalized.csv) produced by the parser pipeline.
        label:    Human-readable name used in error messages (e.g. "PR" or
                  "GSTR-2B").

    Raises:
        ValueError: If any row is missing invoice_number or gstin_supplier,
                    with a list of the offending row numbers.
        FileNotFoundError: If csv_path does not exist.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"{label} CSV not found at {path}")

    # Read only the two key columns — cheap even for large files
    try:
        df = pd.read_csv(path, usecols=list(_REQUIRED_KEY_FIELDS), dtype=str)
    except ValueError as exc:
        # usecols raises ValueError if a column is absent
        raise ValueError(
            f"{label} CSV at {path} is missing one or more composite key columns "
            f"{sorted(_REQUIRED_KEY_FIELDS)}. Parser output may be malformed."
        ) from exc

    null_values = {"", "nan", "none", "null", "n/a"}
    bad_rows: dict[str, list[int]] = {field: [] for field in _REQUIRED_KEY_FIELDS}

    for field_name in _REQUIRED_KEY_FIELDS:
        col = df[field_name].fillna("")
        bad_mask = col.str.strip().str.lower().isin(null_values) | (col.str.strip() == "")
        # +2: 1-indexed + header row
        bad_rows[field_name] = (df.index[bad_mask] + 2).tolist()

    errors = {
        field_name: rows
        for field_name, rows in bad_rows.items()
        if rows
    }

    if errors:
        details = "; ".join(
            f"'{field_name}' is null/empty on rows {rows[:10]}"
            + (" (and more...)" if len(rows) > 10 else "")
            for field_name, rows in errors.items()
        )
        raise ValueError(
            f"{label} CSV has rows with missing composite key values: {details}. "
            f"Reconciliation cannot proceed — every row must have both "
            f"invoice_number and gstin_supplier."
        )


# ── Writer ─────────────────────────────────────────────────────────────────────

def write_config(
    config: ReconConfig,
    path: str | Path,
    *,
    pr_csv: str | Path | None = None,
    gstr2b_csv: str | Path | None = None,
) -> Path:
    """
    Validate composite key integrity in the normalized CSVs, then serialise
    the ReconConfig to a YAML file and return the resolved Path.

    Args:
        config:     ReconConfig produced by build_recon_config()
        path:       Destination file path (e.g. /tmp/{job_id}/config.yaml)
        pr_csv:     If provided, validates that no row has a null/empty
                    invoice_number or gstin_supplier before writing.
        gstr2b_csv: Same validation for the GSTR-2B CSV.

    Returns:
        Resolved Path of the written file.

    Raises:
        ValueError: If CSV validation fails (null composite key values found).
        FileNotFoundError: If a provided CSV path does not exist.
    """
    # Pre-flight: validate composite key fields in both CSVs before writing
    if pr_csv is not None:
        validate_csv_keys(pr_csv, label="Purchase Register")
    if gstr2b_csv is not None:
        validate_csv_keys(gstr2b_csv, label="GSTR-2B")

    dest = Path(path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.dump(
            config.to_dict(),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,     # Preserve logical ordering
        ),
        encoding="utf-8",
    )
    return dest
