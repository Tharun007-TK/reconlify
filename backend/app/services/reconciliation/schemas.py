"""
Reconciliation engine I/O schemas.
Defines the canonical data contracts between the orchestrator and any engine.
All engines receive ReconInput and must return ReconOutput.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

class MatchStatus(StrEnum):
    MATCHED          = "matched"
    UNMATCHED_PR     = "unmatched_pr"      # In PR, absent from GSTR-2B
    UNMATCHED_2B     = "unmatched_2b"      # In GSTR-2B, absent from PR
    POTENTIAL_MATCH  = "potential_match"   # Likely match with discrepancies


class MatchConfidence(StrEnum):
    EXACT   = "exact"    # All fields match perfectly
    HIGH    = "high"     # Key fields match; minor variation
    MEDIUM  = "medium"   # Invoice number matches; amounts differ
    LOW     = "low"      # Fuzzy invoice + GSTIN match only


class MismatchField(StrEnum):
    GSTIN          = "gstin_supplier"
    INVOICE_NUMBER = "invoice_number"
    INVOICE_DATE   = "invoice_date"
    TAXABLE_VALUE  = "taxable_value"
    IGST           = "igst"
    CGST           = "cgst"
    SGST           = "sgst"
    TOTAL_TAX      = "total_tax"


# ── Input records ─────────────────────────────────────────────────────────────

class InvoiceRecord(BaseModel):
    """Canonical invoice record fed to any reconciliation engine."""
    row_hash: str                = Field(..., description="SHA-256 dedup key")
    gstin_supplier: str          = Field(..., description="Normalized 15-char GSTIN")
    invoice_number: str          = Field(..., description="Normalized invoice number")
    invoice_date: date | None    = Field(None)
    taxable_value: float         = Field(0.0, ge=0)
    igst: float                  = Field(0.0, ge=0)
    cgst: float                  = Field(0.0, ge=0)
    sgst: float                  = Field(0.0, ge=0)
    cess: float                  = Field(0.0, ge=0)
    total_tax: float             = Field(0.0, ge=0)
    supplier_name: str | None    = None
    return_period: str | None    = None
    fy: str | None               = None

    # Optional passthrough fields (not used in matching logic)
    source_row: int | None       = None
    extra: dict[str, Any]        = Field(default_factory=dict)

    @model_validator(mode="after")
    def compute_total_tax(self) -> "InvoiceRecord":
        if self.total_tax == 0.0:
            self.total_tax = round(self.igst + self.cgst + self.sgst + self.cess, 2)
        return self


class ReconInput(BaseModel):
    """Input bundle for any reconciliation engine."""
    run_id: str
    client_id: str
    pr_records: list[InvoiceRecord]       = Field(..., description="Purchase Register records")
    gstr2b_records: list[InvoiceRecord]   = Field(..., description="GSTR-2B records")
    config: dict[str, Any]               = Field(default_factory=dict)


# ── Output records ─────────────────────────────────────────────────────────────

class FieldVariance(BaseModel):
    """Describes a single field-level discrepancy between PR and GSTR-2B."""
    field: MismatchField
    pr_value: Any
    gstr2b_value: Any
    variance: float | None = None          # Numeric diff (for amounts)
    variance_pct: float | None = None      # % difference


class MatchedRecord(BaseModel):
    """A PR record successfully reconciled against a GSTR-2B record."""
    pr_row_hash: str
    gstr2b_row_hash: str
    gstin_supplier: str
    invoice_number: str
    invoice_date: date | None = None
    confidence: MatchConfidence
    pr_taxable_value: float
    gstr2b_taxable_value: float
    pr_igst: float
    gstr2b_igst: float
    pr_cgst: float
    gstr2b_cgst: float
    pr_sgst: float
    gstr2b_sgst: float
    value_variance: float = Field(0.0, description="Abs diff in taxable value")
    tax_variance: float   = Field(0.0, description="Abs diff in total tax")
    field_variances: list[FieldVariance] = Field(default_factory=list)
    matched_on: list[str] = Field(default_factory=list)   # Fields used to match


class UnmatchedRecord(BaseModel):
    """A record that could not be reconciled in either source."""
    row_hash: str
    source: str                            # "PURCHASE_REGISTER" | "GSTR_2B"
    gstin_supplier: str
    invoice_number: str
    invoice_date: date | None = None
    taxable_value: float = 0.0
    igst: float          = 0.0
    cgst: float          = 0.0
    sgst: float          = 0.0
    cess: float          = 0.0
    total_tax: float     = 0.0
    supplier_name: str | None = None
    return_period: str | None = None
    itc_impact: float    = Field(0.0, description="ITC denied / at risk (₹)")


class PotentialMatch(BaseModel):
    """
    A pair of records likely representing the same invoice
    but with field-level discrepancies that prevent exact matching.
    Presented to the auditor for manual review.
    """
    pr_row_hash: str
    gstr2b_row_hash: str
    gstin_supplier: str
    pr_invoice_number: str
    gstr2b_invoice_number: str
    similarity_score: float       = Field(..., ge=0.0, le=1.0)
    confidence: MatchConfidence
    field_variances: list[FieldVariance] = Field(default_factory=list)
    suggested_action: str | None  = None   # Human-readable recommendation


class ReconMetrics(BaseModel):
    """Performance and quality metrics for one engine run."""
    engine_name: str
    engine_version: str
    duration_seconds: float
    pr_input_count: int
    gstr2b_input_count: int
    matched_count: int
    unmatched_pr_count: int
    unmatched_2b_count: int
    potential_match_count: int
    match_rate: float              # matched / pr_input_count
    total_itc_claimed: float
    itc_matched: float
    itc_at_risk: float
    itc_recovery_rate: float       # itc_matched / total_itc_claimed
    config_used: dict[str, Any]   = Field(default_factory=dict)


class ReconOutput(BaseModel):
    """Complete output from any reconciliation engine."""
    run_id: str
    client_id: str
    matched: list[MatchedRecord]
    unmatched: list[UnmatchedRecord]
    potential_matches: list[PotentialMatch]
    metrics: ReconMetrics

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "engine": self.metrics.engine_name,
            "matched": len(self.matched),
            "unmatched_pr": sum(1 for u in self.unmatched if u.source == "PURCHASE_REGISTER"),
            "unmatched_2b": sum(1 for u in self.unmatched if u.source == "GSTR_2B"),
            "potential_matches": len(self.potential_matches),
            "match_rate": round(self.metrics.match_rate * 100, 2),
            "itc_at_risk": self.metrics.itc_at_risk,
            "itc_recovery_rate": round(self.metrics.itc_recovery_rate * 100, 2),
            "duration_seconds": self.metrics.duration_seconds,
        }
