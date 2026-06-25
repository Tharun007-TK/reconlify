"""
Pydantic schemas for the GST parser service.
These are the canonical normalized output models returned by the parser.
"""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

class FileType(StrEnum):
    PURCHASE_REGISTER = "purchase_register"
    GSTR_2B           = "gstr_2b"
    GSTR_2A           = "gstr_2a"
    UNKNOWN           = "unknown"


class DocumentType(StrEnum):
    INVOICE       = "INV"
    CREDIT_NOTE   = "CR"
    DEBIT_NOTE    = "DR"
    AMENDED       = "AMD"


class ParseSeverity(StrEnum):
    ERROR   = "error"    # Record skipped / invalid
    WARNING = "warning"  # Record included but has an issue
    INFO    = "info"     # Informational notice


# ── Row-level models ───────────────────────────────────────────────────────────

class NormalizedRecord(BaseModel):
    """
    Canonical GST invoice record after normalization.
    Used for both PR and GSTR-2B records (field subset varies).
    """
    # Identity
    gstin_supplier: str = Field(..., description="Normalized 15-char GSTIN")
    supplier_name: str | None = Field(None, description="Trade name of supplier")
    invoice_number: str = Field(..., description="Normalized invoice number")
    invoice_date: date | None = Field(None, description="ISO date of invoice")
    document_type: DocumentType = Field(DocumentType.INVOICE, description="Invoice / CDN type")

    # Financial (all in INR, 2 decimal places)
    taxable_value: float = Field(0.0, ge=0, description="Taxable base amount (₹)")
    igst: float          = Field(0.0, ge=0, description="Integrated GST (₹)")
    cgst: float          = Field(0.0, ge=0, description="Central GST (₹)")
    sgst: float          = Field(0.0, ge=0, description="State/UT GST (₹)")
    cess: float          = Field(0.0, ge=0, description="Cess (₹)")
    total_tax: float     = Field(0.0, ge=0, description="IGST + CGST + SGST + Cess")
    total_value: float   = Field(0.0, ge=0, description="Taxable Value + Total Tax")

    # GST metadata
    return_period: str | None = Field(None, description="Return period (MMYYYY)")
    fy: str | None            = Field(None, description="Financial year e.g. 2024-25")
    state_code: str | None    = Field(None, description="2-digit state code from GSTIN")
    gstin_valid: bool         = Field(True, description="Whether the GSTIN passes format check")

    # Source traceability
    source_row: int           = Field(..., description="1-indexed row in the source file")
    row_hash: str | None      = Field(None, description="SHA-256 of canonical fields")

    # GSTR-2B specific
    is_amended: bool          = Field(False, description="Whether this is an amended entry")
    itc_availability: str | None = Field(None, description="ITC eligibility as per 2B")

    @field_validator("gstin_supplier", mode="before")
    @classmethod
    def normalize_gstin(cls, v: Any) -> str:
        return str(v).strip().upper()

    @field_validator("invoice_number", mode="before")
    @classmethod
    def normalize_invoice(cls, v: Any) -> str:
        return str(v).strip().upper()

    @model_validator(mode="after")
    def compute_derived_fields(self) -> "NormalizedRecord":
        self.total_tax = round(self.igst + self.cgst + self.sgst + self.cess, 2)
        self.total_value = round(self.taxable_value + self.total_tax, 2)
        if self.gstin_supplier and len(self.gstin_supplier) >= 2:
            self.state_code = self.gstin_supplier[:2]
        return self


# ── Parse issue model ─────────────────────────────────────────────────────────

class ParseIssue(BaseModel):
    """Represents a single data quality issue found during parsing."""
    row: int | None = Field(None, description="Source file row number (1-indexed, with header)")
    field: str | None = Field(None, description="Which field caused the issue")
    value: str | None = Field(None, description="Raw value that caused the issue")
    severity: ParseSeverity
    message: str

    class Config:
        use_enum_values = True


# ── Column mapping result ─────────────────────────────────────────────────────

class ColumnMapping(BaseModel):
    """Documents how raw columns were mapped to canonical names."""
    raw_column: str
    canonical_column: str
    confidence: float = Field(..., ge=0.0, le=1.0)


# ── Top-level parse result ────────────────────────────────────────────────────

class ParseResult(BaseModel):
    """Complete result of parsing a GST file."""

    # Detection metadata
    detected_file_type: FileType
    detected_format: str            # "xlsx", "csv", "json"
    detection_confidence: float     # 0.0 – 1.0

    # Column mapping applied
    column_mappings: list[ColumnMapping]
    unmapped_columns: list[str]     # Raw columns with no canonical match

    # Parse statistics
    total_rows: int
    parsed_rows: int
    skipped_rows: int
    warning_rows: int

    # Financial totals (quick summary)
    total_taxable_value: float
    total_igst: float
    total_cgst: float
    total_sgst: float
    total_cess: float
    total_tax: float
    total_itc_at_risk: int          # Count of records with invalid GSTIN

    # Normalized records
    records: list[NormalizedRecord]

    # Issues log
    issues: list[ParseIssue]

    class Config:
        use_enum_values = True

    def to_summary(self) -> dict[str, Any]:
        """Lightweight summary without individual records."""
        return {
            "detected_file_type": self.detected_file_type,
            "detected_format": self.detected_format,
            "detection_confidence": self.detection_confidence,
            "total_rows": self.total_rows,
            "parsed_rows": self.parsed_rows,
            "skipped_rows": self.skipped_rows,
            "warning_rows": self.warning_rows,
            "total_taxable_value": self.total_taxable_value,
            "total_tax": self.total_tax,
            "total_itc_at_risk": self.total_itc_at_risk,
            "issue_count": len(self.issues),
            "error_count": sum(1 for i in self.issues if i.severity == "error"),
            "warning_count": sum(1 for i in self.issues if i.severity == "warning"),
        }
