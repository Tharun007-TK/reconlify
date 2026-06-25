"""
GSTR-2B Parser.
Supports both:
  - JSON format (as downloaded from GST Portal)
  - Excel format (auto-converted by CA tools)
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import structlog

from app.core.exceptions import ParseError
from app.services.parser.purchase_register import (
    ParseResult,
    _coerce_date,
    _coerce_numeric,
    _normalize_columns,
    _validate_gstin,
)

logger = structlog.get_logger(__name__)


def _compute_2b_row_hash(row: dict, salt: str) -> str:  # type: ignore[type-arg]
    key = "|".join([
        str(row.get("gstin_supplier", "")).upper().strip(),
        str(row.get("invoice_number", "")).upper().strip(),
        str(row.get("invoice_date", "")),
        str(round(row.get("taxable_value", 0.0), 2)),
        str(row.get("document_type", "INV")).upper(),
        salt,
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def _parse_gstr2b_json(data: dict, run_id: str, client_id: str, salt: str) -> list[dict]:  # type: ignore[type-arg]
    """
    Parse the official GST Portal GSTR-2B JSON structure.

    GST Portal structure:
    data.docdata.b2b[]  → B2B invoices
    data.docdata.cdn[]  → Credit/Debit notes
    data.docdata.impg[] → IGST on imports
    """
    records: list[dict] = []  # type: ignore[type-arg]

    doc_data = data.get("data", data).get("docdata", {})

    # ── B2B Invoices ──────────────────────────────────────────────────────────
    for supplier in doc_data.get("b2b", []):
        gstin = str(supplier.get("ctin", "")).strip().upper()
        supplier_name = str(supplier.get("trdnm", "")).strip()

        for inv in supplier.get("inv", []):
            items = inv.get("items", [{"rt": 0, "txval": 0, "igst": 0, "cgst": 0, "sgst": 0}])
            # Aggregate tax across items
            taxable = sum(_coerce_numeric(i.get("txval", 0)) for i in items)
            igst = sum(_coerce_numeric(i.get("igst", 0)) for i in items)
            cgst = sum(_coerce_numeric(i.get("cgst", 0)) for i in items)
            sgst = sum(_coerce_numeric(i.get("sgst", 0)) for i in items)
            cess = sum(_coerce_numeric(i.get("cess", 0)) for i in items)

            record: dict = {  # type: ignore[type-arg]
                "run_id": run_id,
                "client_id": client_id,
                "invoice_number": str(inv.get("inum", "")).strip(),
                "gstin_supplier": gstin,
                "supplier_name": supplier_name or None,
                "invoice_date": _coerce_date(inv.get("idt")),
                "taxable_value": taxable,
                "igst": igst,
                "cgst": cgst,
                "sgst": sgst,
                "cess": cess,
                "document_type": "INV",
                "is_amended": False,
                "return_period": str(data.get("data", data).get("rtnprd", "")).strip() or None,
            }
            record["row_hash"] = _compute_2b_row_hash(record, salt)
            records.append(record)

    # ── Credit/Debit Notes ────────────────────────────────────────────────────
    for supplier in doc_data.get("cdn", []):
        gstin = str(supplier.get("ctin", "")).strip().upper()
        supplier_name = str(supplier.get("trdnm", "")).strip()

        for note in supplier.get("nt", []):
            items = note.get("items", [{}])
            record = {
                "run_id": run_id,
                "client_id": client_id,
                "invoice_number": str(note.get("ntnum", "")).strip(),
                "gstin_supplier": gstin,
                "supplier_name": supplier_name or None,
                "invoice_date": _coerce_date(note.get("ntdt")),
                "taxable_value": sum(_coerce_numeric(i.get("txval", 0)) for i in items),
                "igst": sum(_coerce_numeric(i.get("igst", 0)) for i in items),
                "cgst": sum(_coerce_numeric(i.get("cgst", 0)) for i in items),
                "sgst": sum(_coerce_numeric(i.get("sgst", 0)) for i in items),
                "cess": sum(_coerce_numeric(i.get("cess", 0)) for i in items),
                "document_type": str(note.get("typ", "CDN")).upper(),
                "is_amended": False,
                "return_period": None,
            }
            record["row_hash"] = _compute_2b_row_hash(record, salt)
            records.append(record)

    return records


def parse_gstr2b(
    file_bytes: bytes,
    filename: str,
    salt: str,
    *,
    job_id: str,
    client_id: str,
) -> ParseResult:
    """
    Parse a GSTR-2B file (JSON or Excel) and return normalized records.
    """
    logger.info("parser.gstr2b.start", filename=filename, job_id=job_id)

    ext = filename.lower().rsplit(".", 1)[-1]
    records: list[dict] = []  # type: ignore[type-arg]
    errors: list[dict] = []  # type: ignore[type-arg]

    try:
        buf = io.BytesIO(file_bytes)

        if ext == "json":
            data = json.load(buf)
            records = _parse_gstr2b_json(data, job_id, client_id, salt)
            total_rows = len(records)
            parsed_rows = total_rows

        elif ext in ("xlsx", "xls"):
            # Excel export from GSTN / CA tool
            df = pd.read_excel(buf)
            df = _normalize_columns(df)

            required = {"invoice_number", "gstin_supplier"}
            missing = required - set(df.columns)
            if missing:
                raise ParseError(
                    f"GSTR-2B Excel missing required columns: {missing}. "
                    f"Found: {list(df.columns)}"
                )

            df = df.dropna(how="all")
            total_rows = len(df)

            for idx, row in df.iterrows():
                row_num = int(idx) + 2
                gstin = str(row.get("gstin_supplier", "")).strip().upper()
                invoice_no = str(row.get("invoice_number", "")).strip()

                if not gstin or not invoice_no:
                    errors.append({"row": row_num, "reason": "Missing GSTIN or invoice number"})
                    continue

                record = {
                    "run_id": job_id,
                    "client_id": client_id,
                    "invoice_number": invoice_no,
                    "gstin_supplier": gstin,
                    "supplier_name": str(row.get("supplier_name", "")).strip() or None,
                    "invoice_date": _coerce_date(row.get("invoice_date")),
                    "taxable_value": _coerce_numeric(row.get("taxable_value", 0)),
                    "igst": _coerce_numeric(row.get("igst", 0)),
                    "cgst": _coerce_numeric(row.get("cgst", 0)),
                    "sgst": _coerce_numeric(row.get("sgst", 0)),
                    "cess": _coerce_numeric(row.get("cess", 0)),
                    "document_type": str(row.get("document_type", "INV")).upper(),
                    "is_amended": bool(row.get("is_amended", False)),
                    "return_period": str(row.get("return_period", "")).strip() or None,
                }
                record["row_hash"] = _compute_2b_row_hash(record, salt)
                records.append(record)

            parsed_rows = len(records)
        else:
            raise ParseError(f"Unsupported GSTR-2B format: .{ext}")

    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse GSTR-2B '{filename}': {exc}") from exc

    logger.info(
        "parser.gstr2b.complete",
        job_id=job_id,
        total=total_rows,
        parsed=len(records),
    )

    return ParseResult(
        records=records,
        total_rows=total_rows,
        parsed_rows=len(records),
        error_rows=len(errors),
        errors=errors,
    )
