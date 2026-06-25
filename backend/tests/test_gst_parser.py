"""
Unit tests for the GST parser service.
Tests each module independently (no DB, no network, no Supabase).
"""
from __future__ import annotations

import io
import json
from datetime import date

import pandas as pd
import pytest

from app.services.parser.column_mapper import map_columns
from app.services.parser.detector import detect_file
from app.services.parser.gst_parser import parse_gst_file
from app.services.parser.normalizer import NormalizationConfig, normalize_dataframe
from app.services.parser.schemas import FileType, ParseSeverity
from app.services.parser.validators import (
    validate_amount,
    validate_date,
    validate_gstin,
    validate_invoice_number,
    validate_return_period,
)


# ═══════════════════════════════════════════════════════════════════
# VALIDATOR TESTS
# ═══════════════════════════════════════════════════════════════════

class TestGSTINValidator:
    def test_valid_gstin(self):
        gstin, valid, msg = validate_gstin("27AAPFU0939F1ZV")
        assert valid is True
        assert gstin == "27AAPFU0939F1ZV"
        assert msg is None

    def test_normalizes_lowercase(self):
        gstin, valid, _ = validate_gstin("27aapfu0939f1zv")
        assert gstin == "27AAPFU0939F1ZV"

    def test_strips_spaces(self):
        gstin, valid, _ = validate_gstin("  27AAPFU0939F1ZV  ")
        assert gstin == "27AAPFU0939F1ZV"

    def test_short_gstin_invalid(self):
        _, valid, msg = validate_gstin("27AAPFU0939F")
        assert valid is False
        assert "characters" in msg

    def test_wrong_format(self):
        _, valid, msg = validate_gstin("99ZZZZZ9999Z9ZZ")
        assert valid is False

    def test_missing_gstin(self):
        gstin, valid, msg = validate_gstin(None)
        assert valid is False
        assert "missing" in msg

    def test_empty_string(self):
        _, valid, msg = validate_gstin("")
        assert valid is False

    def test_invalid_state_code(self):
        _, valid, msg = validate_gstin("00AAPFU0939F1ZV")
        assert valid is False
        assert "state code" in msg


class TestDateValidator:
    @pytest.mark.parametrize("raw,expected", [
        ("01/04/2024",  date(2024, 4, 1)),
        ("01-04-2024",  date(2024, 4, 1)),
        ("01.04.2024",  date(2024, 4, 1)),
        ("2024-04-01",  date(2024, 4, 1)),
        ("01 Apr 2024", date(2024, 4, 1)),
        ("Apr 01, 2024",date(2024, 4, 1)),
        ("01-Apr-2024", date(2024, 4, 1)),
        ("20240401",    date(2024, 4, 1)),
    ])
    def test_date_formats(self, raw: str, expected: date):
        parsed, valid, msg = validate_date(raw)
        assert parsed == expected, f"Failed for input '{raw}': {msg}"

    def test_pandas_timestamp(self):
        ts = pd.Timestamp("2024-04-01")
        parsed, valid, _ = validate_date(ts)
        assert parsed == date(2024, 4, 1)

    def test_missing_date(self):
        parsed, valid, msg = validate_date(None)
        assert valid is False
        assert "missing" in msg

    def test_pre_gst_date_warns(self):
        parsed, valid, msg = validate_date("01/01/2015")
        assert valid is False
        assert "2017" in msg

    def test_null_like_strings(self):
        for val in ("nan", "None", "N/A", "-"):
            parsed, valid, msg = validate_date(val)
            assert valid is False


class TestAmountValidator:
    @pytest.mark.parametrize("raw,expected", [
        (1000.00,       1000.00),
        ("1,00,000.00", 100000.00),
        ("₹1000",       1000.00),
        ("Rs 500",      500.00),
        ("INR 250.50",  250.50),
        ("(100.00)",    100.00),   # Parenthetical negative → absolute
        (0,             0.0),
        (None,          0.0),
    ])
    def test_amount_coercion(self, raw, expected):
        val, valid, _ = validate_amount(raw, "test_field")
        assert val == expected

    def test_negative_amount_flagged(self):
        val, valid, msg = validate_amount(-500.0, "igst")
        assert val == 500.0
        assert valid is False
        assert "negative" in msg

    def test_invalid_string(self):
        val, valid, msg = validate_amount("abc", "cgst")
        assert val == 0.0
        assert valid is False


class TestInvoiceNumberValidator:
    def test_valid_invoice(self):
        inv, valid, msg = validate_invoice_number("INV-2024-001")
        assert valid is True
        assert inv == "INV-2024-001"

    def test_uppercase_normalisation(self):
        inv, _, _ = validate_invoice_number("inv-001")
        assert inv == "INV-001"

    def test_too_long_truncated(self):
        long_inv = "A" * 20
        inv, valid, msg = validate_invoice_number(long_inv)
        assert len(inv) == 16
        assert valid is False
        assert "16 characters" in msg

    def test_missing_invoice(self):
        _, valid, msg = validate_invoice_number(None)
        assert valid is False


class TestReturnPeriodValidator:
    @pytest.mark.parametrize("raw,expected", [
        ("042024",     "042024"),
        ("Apr-2024",   "042024"),
        ("April 2024", "042024"),
        ("04/2024",    "042024"),
    ])
    def test_return_period_formats(self, raw, expected):
        period, valid, msg = validate_return_period(raw)
        assert period == expected, f"Failed for input '{raw}'"

    def test_none_is_ok(self):
        period, valid, msg = validate_return_period(None)
        assert valid is True
        assert period is None


# ═══════════════════════════════════════════════════════════════════
# COLUMN MAPPER TESTS
# ═══════════════════════════════════════════════════════════════════

class TestColumnMapper:
    def test_exact_match(self):
        result = map_columns(["GSTIN", "Invoice No", "Taxable Value"])
        assert result["GSTIN"].canonical == "gstin_supplier"
        assert result["GSTIN"].method == "exact"

    def test_fuzzy_match(self):
        result = map_columns(["Vendor GSTIN Number"])
        assert result["Vendor GSTIN Number"].canonical == "gstin_supplier"
        assert result["Vendor GSTIN Number"].method == "fuzzy"

    def test_unmapped_column(self):
        result = map_columns(["Random Junk Column"])
        assert result["Random Junk Column"].method == "unmapped"

    def test_no_duplicate_canonical_mapping(self):
        """Two columns that both match the same canonical should not both be mapped."""
        result = map_columns(["GSTIN", "Supplier GSTIN"])
        canonicals = [r.canonical for r in result.values() if r.method != "unmapped"]
        assert len(canonicals) == len(set(canonicals)), "Duplicate canonical assignments"

    def test_portal_column_names(self):
        """Test columns as they appear in the GST portal."""
        cols = ["GSTIN of supplier", "Invoice Number", "Invoice date",
                "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax"]
        result = map_columns(cols)
        mapped = {r.canonical for r in result.values() if r.method != "unmapped"}
        assert "gstin_supplier" in mapped
        assert "invoice_number" in mapped
        assert "igst" in mapped
        assert "cgst" in mapped
        assert "sgst" in mapped


# ═══════════════════════════════════════════════════════════════════
# DETECTOR TESTS
# ═══════════════════════════════════════════════════════════════════

class TestDetector:
    def _make_csv(self, rows: list[dict]) -> bytes:
        df = pd.DataFrame(rows)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue().encode()

    def test_detects_purchase_register(self):
        data = self._make_csv([{
            "GSTIN": "27AAPFU0939F1ZV",
            "Invoice No": "INV-001",
            "Invoice Date": "01/04/2024",
            "Taxable Value": 10000,
            "CGST": 900, "SGST": 900, "IGST": 0,
        }])
        detection, df = detect_file(data, "purchase_register.csv")
        assert detection.file_type == FileType.PURCHASE_REGISTER
        assert detection.confidence > 0.4

    def test_detects_gstr2b_excel(self):
        data = self._make_csv([{
            "GSTIN of supplier": "27AAPFU0939F1ZV",
            "Invoice Number": "INV-001",
            "Invoice date": "01/04/2024",
            "Taxable Value": 10000, "Integrated Tax": 0,
            "Central Tax": 900, "State/UT Tax": 900,
            "ITC Availability": "Y",
        }])
        detection, df = detect_file(data, "gstr2b_april2024.csv")
        # Filename hint pushes confidence toward 2B
        assert detection.confidence > 0.3

    def test_filename_heuristic_2b(self):
        data = self._make_csv([{"Invoice No": "X", "GSTIN": "27AAPFU0939F1ZV"}])
        detection, _ = detect_file(data, "GSTR2B_Q1_2024.csv")
        assert detection.file_type == FileType.GSTR_2B

    def test_detects_gstr2b_json(self):
        payload = json.dumps({
            "data": {
                "rtnprd": "042024",
                "docdata": {"b2b": [], "cdn": []},
            }
        }).encode()
        detection, df = detect_file(payload, "gstr2b.json")
        assert detection.file_type == FileType.GSTR_2B
        assert detection.file_format == "json"


# ═══════════════════════════════════════════════════════════════════
# END-TO-END PARSER TESTS
# ═══════════════════════════════════════════════════════════════════

class TestGSTParser:
    PR_ROWS = [
        {
            "GSTIN": "27AAPFU0939F1ZV",
            "Supplier Name": "ABC Traders",
            "Invoice No": "INV-001",
            "Invoice Date": "01/04/2024",
            "Taxable Value": 100000,
            "CGST": 9000, "SGST": 9000, "IGST": 0, "Cess": 0,
            "Return Period": "042024",
        },
        {
            "GSTIN": "29GGGGG1314R9Z6",
            "Supplier Name": "XYZ Corp",
            "Invoice No": "INV-002",
            "Invoice Date": "15/04/2024",
            "Taxable Value": 50000,
            "CGST": 0, "SGST": 0, "IGST": 9000, "Cess": 500,
            "Return Period": "042024",
        },
        # Empty row — should be skipped
        {"GSTIN": None, "Invoice No": None, "Taxable Value": None},
        # Invalid GSTIN — should be included as warning
        {
            "GSTIN": "INVALID",
            "Supplier Name": "Bad Vendor",
            "Invoice No": "INV-003",
            "Invoice Date": "20/04/2024",
            "Taxable Value": 5000,
            "CGST": 450, "SGST": 450, "IGST": 0,
        },
    ]

    def _make_csv_bytes(self, rows: list[dict]) -> bytes:
        df = pd.DataFrame(rows)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue().encode()

    def _make_excel_bytes(self, rows: list[dict]) -> bytes:
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Purchase Register")
        return buf.getvalue()

    def test_parse_csv_pr(self):
        content = self._make_csv_bytes(self.PR_ROWS)
        result = parse_gst_file(
            content, "pr.csv",
            run_id="test-run", client_id="test-client",
        )
        assert result.detected_file_type == FileType.PURCHASE_REGISTER
        assert result.detected_format == "csv"
        assert result.parsed_rows >= 2
        assert result.skipped_rows >= 1   # Empty row skipped

    def test_parse_excel_pr(self):
        content = self._make_excel_bytes(self.PR_ROWS)
        result = parse_gst_file(
            content, "purchase_register.xlsx",
            run_id="test-run", client_id="test-client",
        )
        assert result.detected_format == "xlsx"
        assert result.parsed_rows >= 2

    def test_financial_totals_correct(self):
        rows = [{
            "GSTIN": "27AAPFU0939F1ZV", "Invoice No": "INV-001",
            "Invoice Date": "01/04/2024", "Taxable Value": 100000,
            "CGST": 9000, "SGST": 9000, "IGST": 0,
        }]
        content = self._make_csv_bytes(rows)
        result = parse_gst_file(
            content, "pr.csv", run_id="r", client_id="c"
        )
        assert result.total_taxable_value == 100000.0
        assert result.total_cgst == 9000.0
        assert result.total_sgst == 9000.0
        assert result.total_tax == 18000.0

    def test_row_hash_deterministic(self):
        rows = [{
            "GSTIN": "27AAPFU0939F1ZV", "Invoice No": "INV-001",
            "Invoice Date": "01/04/2024", "Taxable Value": 10000,
            "CGST": 900, "SGST": 900, "IGST": 0,
        }]
        content = self._make_csv_bytes(rows)
        r1 = parse_gst_file(content, "pr.csv", run_id="r", client_id="c", salt="fixed-salt")
        r2 = parse_gst_file(content, "pr.csv", run_id="r", client_id="c", salt="fixed-salt")
        assert r1.records[0].row_hash == r2.records[0].row_hash

    def test_invalid_gstin_flagged(self):
        rows = [{
            "GSTIN": "BADGSTIN", "Invoice No": "INV-001",
            "Invoice Date": "01/04/2024", "Taxable Value": 5000,
            "CGST": 450, "SGST": 450, "IGST": 0,
        }]
        content = self._make_csv_bytes(rows)
        result = parse_gst_file(
            content, "pr.csv", run_id="r", client_id="c"
        )
        warnings = [i for i in result.issues if i.severity == "warning"]
        assert any("GSTIN" in i.message for i in warnings)

    def test_parse_gstr2b_json(self):
        payload = {
            "data": {
                "rtnprd": "042024",
                "docdata": {
                    "b2b": [{
                        "ctin": "27AAPFU0939F1ZV",
                        "trdnm": "ABC Traders",
                        "inv": [{
                            "inum": "INV-001",
                            "idt": "01-04-2024",
                            "itcavl": "Y",
                            "items": [{"txval": 100000, "igst": 18000, "cgst": 0, "sgst": 0}],
                        }]
                    }],
                    "cdn": [],
                }
            }
        }
        content = json.dumps(payload).encode()
        result = parse_gst_file(
            content, "gstr2b.json",
            run_id="r", client_id="c",
            file_type_hint=FileType.GSTR_2B,
        )
        assert result.detected_file_type == FileType.GSTR_2B
        assert result.parsed_rows == 1
        assert result.records[0].gstin_supplier == "27AAPFU0939F1ZV"
        assert result.records[0].igst == 18000.0
        assert result.records[0].invoice_number == "INV-001"

    def test_column_alias_variations(self):
        """Parser should handle any of these column name variations."""
        alias_rows = [{
            "Vendor GSTIN": "27AAPFU0939F1ZV",
            "Bill No": "INV-001",
            "Transaction Date": "01/04/2024",
            "Net Amount": 10000,
            "Central Tax": 900,
            "State Tax": 900,
            "Integrated Tax": 0,
        }]
        content = self._make_csv_bytes(alias_rows)
        result = parse_gst_file(
            content, "pr.csv", run_id="r", client_id="c"
        )
        assert result.parsed_rows == 1
        assert result.records[0].taxable_value == 10000.0

    def test_date_format_variations(self):
        """All supported date formats should parse correctly."""
        formats = [
            ("01/04/2024", date(2024, 4, 1)),
            ("01-04-2024", date(2024, 4, 1)),
            ("2024-04-01", date(2024, 4, 1)),
            ("01 Apr 2024", date(2024, 4, 1)),
            ("20240401", date(2024, 4, 1)),
        ]
        for raw_date, expected in formats:
            rows = [{
                "GSTIN": "27AAPFU0939F1ZV",
                "Invoice No": "INV-001",
                "Invoice Date": raw_date,
                "Taxable Value": 1000,
                "CGST": 90, "SGST": 90, "IGST": 0,
            }]
            content = self._make_csv_bytes(rows)
            result = parse_gst_file(content, "pr.csv", run_id="r", client_id="c")
            assert result.records[0].invoice_date == expected, f"Failed for date format '{raw_date}'"

    def test_missing_optional_columns(self):
        """Parser should not fail when non-required columns are absent."""
        rows = [{
            "GSTIN": "27AAPFU0939F1ZV",
            "Invoice No": "INV-001",
            "Taxable Value": 5000,
        }]
        content = self._make_csv_bytes(rows)
        result = parse_gst_file(content, "pr.csv", run_id="r", client_id="c")
        assert result.parsed_rows == 1
        assert result.records[0].igst == 0.0
        assert result.records[0].invoice_date is None

    def test_summary_has_no_records(self):
        rows = [{
            "GSTIN": "27AAPFU0939F1ZV",
            "Invoice No": "INV-001",
            "Taxable Value": 1000,
            "CGST": 90, "SGST": 90,
        }]
        content = self._make_csv_bytes(rows)
        result = parse_gst_file(content, "pr.csv", run_id="r", client_id="c")
        summary = result.to_summary()
        assert "records" not in summary
        assert "detected_file_type" in summary
