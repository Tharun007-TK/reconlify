"""
Duplicate detection engine using an optimized Pandas implementation.

Categories:
1. Exact Duplicate: Same GSTIN, Same Invoice Number, Same Amount
2. Suspected Duplicate: Same GSTIN, Similar Invoice Number, Similar Amount, Date diff <= 3 days

Returns confidence_score, duplicate_type, and reason.
"""
from __future__ import annotations

import itertools
from typing import Any

import pandas as pd
import structlog
from rapidfuzz import fuzz

logger = structlog.get_logger(__name__)

# Constants for thresholds
AMOUNT_TOLERANCE = 10.0      # ₹ max difference for "Similar Amount"
FUZZY_INV_THRESHOLD = 80.0   # min score for "Similar Invoice Number"


def detect_duplicates_pandas(
    records: list[dict[str, Any]], source: str
) -> list[dict[str, Any]]:
    """
    Optimized Pandas duplicate detection engine.
    Finds Exact and Suspected duplicates within a single source.
    """
    if not records:
        return []

    # 1. Load into DataFrame
    df = pd.DataFrame(records)
    
    # Ensure required columns exist
    required_cols = ["row_hash", "gstin_supplier", "invoice_number", "taxable_value", "invoice_date"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = None

    # Clean and normalize columns
    df["gstin_supplier"] = df["gstin_supplier"].astype(str).str.upper().str.strip()
    df["invoice_number"] = df["invoice_number"].astype(str).str.upper().str.strip()
    df["taxable_value"] = pd.to_numeric(df["taxable_value"], errors="coerce").fillna(0.0)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce")

    results: list[dict[str, Any]] = []

    # ──────────────────────────────────────────────────────────────────────────
    # CATEGORY 1: EXACT DUPLICATES
    # Same GSTIN, Same Invoice Number, Same Amount
    # ──────────────────────────────────────────────────────────────────────────
    
    # Group by the exact match criteria
    exact_groups = df.groupby(["gstin_supplier", "invoice_number", "taxable_value"])
    
    # Track indices that are already part of an exact match to skip them in suspected
    exact_matched_indices = set()

    for _, group in exact_groups:
        if len(group) > 1:
            # Generate pairs for the group (combinations of 2)
            for idx_a, idx_b in itertools.combinations(group.index, 2):
                row_a = group.loc[idx_a]
                row_b = group.loc[idx_b]
                
                exact_matched_indices.add(idx_a)
                exact_matched_indices.add(idx_b)

                results.append({
                    "source": source,
                    "record_id_a": row_a["row_hash"],
                    "record_id_b": row_b["row_hash"],
                    "invoice_number": row_a["invoice_number"],
                    "gstin_supplier": row_a["gstin_supplier"],
                    "duplicate_type": "exact",
                    "confidence_score": 1.0,
                    "reason": "Exact match on GSTIN, Invoice Number, and Amount",
                })

    # ──────────────────────────────────────────────────────────────────────────
    # CATEGORY 2: SUSPECTED DUPLICATES
    # Same GSTIN, Similar Invoice, Similar Amount, Date Diff <= 3 days
    # ──────────────────────────────────────────────────────────────────────────
    
    # Filter out records already matched exactly to reduce work
    suspect_df = df.drop(index=list(exact_matched_indices))
    
    if suspect_df.empty:
        return results

    # Group by GSTIN to limit the Cartesian product size
    suspect_groups = suspect_df.groupby("gstin_supplier")
    
    suspect_comparisons = 0

    for gstin, group in suspect_groups:
        n_rows = len(group)
        if n_rows < 2:
            continue
            
        # Optional safeguard: If a single vendor has 10,000+ invoices, O(N^2) is 100M. 
        # For production robustness, we should cap this or chunk it.
        # But for standard workloads, itertools is fast in Python.
        if n_rows > 5000:
            logger.warning("duplicate_detector.large_vendor_group", gstin=gstin, rows=n_rows)
            # A more advanced approach would use a blocking algorithm (e.g. TF-IDF block)
            # but we proceed with combinations.
            
        for idx_a, idx_b in itertools.combinations(group.index, 2):
            suspect_comparisons += 1
            row_a = group.loc[idx_a]
            row_b = group.loc[idx_b]
            
            # Condition A: Similar Amount
            amount_diff = abs(row_a["taxable_value"] - row_b["taxable_value"])
            if amount_diff > AMOUNT_TOLERANCE:
                continue
                
            # Condition B: Date diff <= 3 days
            date_a = row_a["invoice_date"]
            date_b = row_b["invoice_date"]
            if pd.notnull(date_a) and pd.notnull(date_b):
                date_diff = abs((date_a - date_b).days)
                if date_diff > 3:
                    continue
            
            # Condition C: Similar Invoice Number (fuzz ratio)
            inv_a = row_a["invoice_number"]
            inv_b = row_b["invoice_number"]
            # Skip if they are exactly identical (otherwise it would have been an exact duplicate, 
            # unless dates were different, which is still a suspect duplicate)
            
            score = fuzz.ratio(inv_a, inv_b)
            if score >= FUZZY_INV_THRESHOLD:
                # We have a suspected duplicate!
                confidence = round(score / 100.0, 4)
                
                reasons = [f"Similar invoice numbers ({score}%)"]
                if amount_diff == 0:
                    reasons.append("Exact same amount")
                else:
                    reasons.append(f"Amount diff is ₹{amount_diff:.2f}")
                    
                if pd.notnull(date_a) and pd.notnull(date_b):
                    reasons.append(f"Dates differ by {date_diff} days")
                else:
                    reasons.append("One or both dates missing")

                results.append({
                    "source": source,
                    "record_id_a": row_a["row_hash"],
                    "record_id_b": row_b["row_hash"],
                    "invoice_number": inv_a,
                    "gstin_supplier": gstin,
                    "duplicate_type": "suspected",
                    "confidence_score": confidence,
                    "reason": "; ".join(reasons),
                })

    logger.info(
        "duplicate_detector.complete",
        source=source,
        exact=len([r for r in results if r["duplicate_type"] == "exact"]),
        suspected=len([r for r in results if r["duplicate_type"] == "suspected"]),
        suspect_comparisons=suspect_comparisons,
    )
    
    return results


def detect_all_duplicates(
    pr_records: list[dict[str, Any]],
    gstr2b_records: list[dict[str, Any]],
    run_id: str,
    client_id: str,
) -> list[dict[str, Any]]:
    """
    Public entry point for the duplicate detection engine.
    Processes both Purchase Register and GSTR-2B.
    Formats the output to match the expected DB schema in recon_task.
    """
    logger.info("duplicate_detector.start", run_id=run_id)

    raw_pairs = []
    if pr_records:
        raw_pairs.extend(detect_duplicates_pandas(pr_records, "PURCHASE_REGISTER"))
    if gstr2b_records:
        raw_pairs.extend(detect_duplicates_pandas(gstr2b_records, "GSTR_2B"))

    # Format for DB insertion (duplicate_records table expects certain fields)
    db_pairs = []
    for p in raw_pairs:
        db_pairs.append({
            "run_id": run_id,
            "client_id": client_id,
            "source": p["source"],
            "record_id_a": p["record_id_a"],
            "record_id_b": p["record_id_b"],
            "invoice_number": p["invoice_number"],
            "gstin_supplier": p["gstin_supplier"],
            "dtype": p["duplicate_type"],               # Mapped to duplicate_records.dtype
            "similarity_score": p["confidence_score"],  # Mapped to duplicate_records.similarity_score
            "diff_fields": {"reason": p["reason"]},     # Mapped to duplicate_records.diff_fields
            "status": "flagged",
        })

    return db_pairs
