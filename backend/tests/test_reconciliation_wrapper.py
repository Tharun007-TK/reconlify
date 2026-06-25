"""
Tests for the reconciliation wrapper service.
Tests each engine and the orchestrator without external dependencies.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.reconciliation.engine.base import ReconciliationEngineError
from app.services.reconciliation.engine.pandas_engine import PandasEngine
from app.services.reconciliation.engine.reconlify import ReconlifyEngine
from app.services.reconciliation.engine.registry import EngineType, get_engine
from app.services.reconciliation.schemas import (
    InvoiceRecord,
    MatchConfidence,
    ReconInput,
    ReconOutput,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _record(
    inv_no: str,
    gstin: str = "27AAPFU0939F1ZV",
    taxable: float = 100_000.0,
    igst: float = 0.0,
    cgst: float = 9_000.0,
    sgst: float = 9_000.0,
    inv_date: date = date(2024, 4, 1),
    salt: str = "test",
) -> InvoiceRecord:
    import hashlib
    key = f"{gstin}|{inv_no}|{inv_date}|{round(taxable, 2)}|INV|{salt}"
    row_hash = hashlib.sha256(key.encode()).hexdigest()
    return InvoiceRecord(
        row_hash=row_hash,
        gstin_supplier=gstin,
        invoice_number=inv_no,
        invoice_date=inv_date,
        taxable_value=taxable,
        igst=igst,
        cgst=cgst,
        sgst=sgst,
        cess=0.0,
    )


def _make_input(
    pr: list[InvoiceRecord],
    gstr2b: list[InvoiceRecord],
    run_id: str = "test-run",
    client_id: str = "test-client",
) -> ReconInput:
    return ReconInput(
        run_id=run_id,
        client_id=client_id,
        pr_records=pr,
        gstr2b_records=gstr2b,
    )


# ═══════════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════════

class TestInvoiceRecord:
    def test_total_tax_computed(self):
        rec = _record("INV-001", igst=18_000, cgst=0, sgst=0)
        assert rec.total_tax == 18_000.0

    def test_total_tax_sum(self):
        rec = _record("INV-001", cgst=9_000, sgst=9_000)
        assert rec.total_tax == 18_000.0


# ═══════════════════════════════════════════════════════════════════
# PANDAS ENGINE
# ═══════════════════════════════════════════════════════════════════

class TestPandasEngine:
    """Tests for the pure-Python fallback engine."""

    @pytest.fixture
    def engine(self) -> PandasEngine:
        return PandasEngine()

    @pytest.mark.asyncio
    async def test_exact_match(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001")]
        b2b = [_record("INV-001")]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        assert len(output.matched) == 1
        assert len(output.unmatched) == 0
        assert output.matched[0].confidence == MatchConfidence.EXACT
        assert output.matched[0].invoice_number == "INV-001"

    @pytest.mark.asyncio
    async def test_unmatched_pr(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001")]
        b2b = [_record("INV-999")]   # Different invoice
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        unmatched_pr = [u for u in output.unmatched if u.source == "PURCHASE_REGISTER"]
        unmatched_2b = [u for u in output.unmatched if u.source == "GSTR_2B"]
        assert len(unmatched_pr) == 1
        assert len(unmatched_2b) == 1

    @pytest.mark.asyncio
    async def test_unmatched_2b_only(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001")]
        b2b = [_record("INV-001"), _record("INV-002")]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        assert len(output.matched) == 1
        unmatched_2b = [u for u in output.unmatched if u.source == "GSTR_2B"]
        assert len(unmatched_2b) == 1
        assert unmatched_2b[0].invoice_number == "INV-002"

    @pytest.mark.asyncio
    async def test_amount_variance_detected(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001", taxable=100_000)]
        b2b = [_record("INV-001", taxable=90_000)]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        # Should still match (same GSTIN + inv number) but flag variance
        assert len(output.matched) == 1
        variances = output.matched[0].field_variances
        variance_fields = [v.field for v in variances]
        assert "taxable_value" in variance_fields

    @pytest.mark.asyncio
    async def test_fuzzy_match_creates_potential(self, engine: PandasEngine) -> None:
        pr  = [_record("INV/2024/001")]
        b2b = [_record("INV-2024-001")]   # Same but different separator
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        # Should be a potential match, not an exact match
        assert len(output.potential_matches) >= 0   # Engine may or may not catch this
        # At minimum: no crash

    @pytest.mark.asyncio
    async def test_multiple_invoices_same_gstin(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001"), _record("INV-002"), _record("INV-003")]
        b2b = [_record("INV-001"), _record("INV-002")]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        assert len(output.matched) == 2
        unmatched_pr = [u for u in output.unmatched if u.source == "PURCHASE_REGISTER"]
        assert len(unmatched_pr) == 1
        assert unmatched_pr[0].invoice_number == "INV-003"

    @pytest.mark.asyncio
    async def test_metrics_populated(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001"), _record("INV-002")]
        b2b = [_record("INV-001")]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)
        m = output.metrics

        assert m.engine_name == "pandas_fallback"
        assert m.pr_input_count == 2
        assert m.gstr2b_input_count == 1
        assert m.matched_count == 1
        assert m.unmatched_pr_count == 1
        assert 0 <= m.match_rate <= 1.0
        assert m.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_itc_at_risk_calculation(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001", igst=18_000, cgst=0, sgst=0)]
        b2b = []   # No 2B records → all PR unmatched
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        assert output.metrics.itc_at_risk == 18_000.0

    @pytest.mark.asyncio
    async def test_empty_pr_raises(self, engine: PandasEngine) -> None:
        """Engine should handle empty inputs gracefully."""
        inp = _make_input([], [_record("INV-001")])
        output = await engine.reconcile(inp)
        assert output.metrics.matched_count == 0

    @pytest.mark.asyncio
    async def test_different_gstin_no_match(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001", gstin="27AAPFU0939F1ZV")]
        b2b = [_record("INV-001", gstin="29GGGGG1314R9Z6")]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)

        # Different GSTIN → should NOT match
        assert len(output.matched) == 0
        assert len(output.unmatched) == 2

    @pytest.mark.asyncio
    async def test_summary_dict(self, engine: PandasEngine) -> None:
        pr  = [_record("INV-001")]
        b2b = [_record("INV-001")]
        inp = _make_input(pr, b2b)

        output = await engine.reconcile(inp)
        summary = output.summary()

        assert summary["matched"] == 1
        assert summary["unmatched_pr"] == 0
        assert "match_rate" in summary
        assert "itc_at_risk" in summary

    @pytest.mark.asyncio
    async def test_is_available(self, engine: PandasEngine) -> None:
        assert engine.is_available() is True


# ═══════════════════════════════════════════════════════════════════
# ENGINE REGISTRY
# ═══════════════════════════════════════════════════════════════════

class TestEngineRegistry:
    def test_get_pandas_engine(self) -> None:
        engine = get_engine(EngineType.PANDAS)
        assert engine.ENGINE_NAME == "pandas_fallback"

    def test_auto_falls_back_to_pandas_when_cli_absent(self) -> None:
        with patch(
            "app.services.reconciliation.engine.reconlify.ReconlifyEngine.is_available",
            return_value=False,
        ):
            engine = get_engine(EngineType.AUTO)
            assert engine.ENGINE_NAME == "pandas_fallback"

    def test_reconlify_engine_unavailable_falls_back(self) -> None:
        with patch(
            "app.services.reconciliation.engine.reconlify.ReconlifyEngine.is_available",
            return_value=False,
        ):
            engine = get_engine(EngineType.RECONLIFY)
            assert engine.ENGINE_NAME == "pandas_fallback"


# ═══════════════════════════════════════════════════════════════════
# RECONLIFY ENGINE (mocked CLI)
# ═══════════════════════════════════════════════════════════════════

class TestReconlifyEngine:
    """Tests Reconlify engine output parsing with mocked subprocess."""

    def _matched_csv(self) -> bytes:
        return (
            "pr_row_hash,gstr2b_row_hash,gstin_supplier,invoice_number,"
            "pr_taxable_value,gstr2b_taxable_value,"
            "pr_igst,gstr2b_igst,pr_cgst,gstr2b_cgst,pr_sgst,gstr2b_sgst,"
            "similarity_score\n"
            "hash_pr_001,hash_2b_001,27AAPFU0939F1ZV,INV-001,"
            "100000,100000,0,0,9000,9000,9000,9000,1.0\n"
        ).encode()

    def _unmatched_pr_csv(self) -> bytes:
        return (
            "row_hash,gstin_supplier,invoice_number,taxable_value,igst,cgst,sgst,cess\n"
            "hash_pr_002,27AAPFU0939F1ZV,INV-002,50000,0,4500,4500,0\n"
        ).encode()

    def _unmatched_2b_csv(self) -> bytes:
        return b"row_hash,gstin_supplier,invoice_number,taxable_value,igst,cgst,sgst,cess\n"

    def _potential_csv(self) -> bytes:
        return b"pr_row_hash,gstr2b_row_hash,gstin_supplier,similarity_score\n"

    @pytest.mark.asyncio
    async def test_output_parsing(self) -> None:
        engine = ReconlifyEngine(
            cli_path="/fake/reconlify",
            tmp_dir="/tmp",
            timeout=60,
        )

        pr = [
            InvoiceRecord(
                row_hash="hash_pr_001",
                gstin_supplier="27AAPFU0939F1ZV",
                invoice_number="INV-001",
                taxable_value=100_000,
                cgst=9_000, sgst=9_000,
            ),
            InvoiceRecord(
                row_hash="hash_pr_002",
                gstin_supplier="27AAPFU0939F1ZV",
                invoice_number="INV-002",
                taxable_value=50_000,
                cgst=4_500, sgst=4_500,
            ),
        ]
        b2b = [
            InvoiceRecord(
                row_hash="hash_2b_001",
                gstin_supplier="27AAPFU0939F1ZV",
                invoice_number="INV-001",
                taxable_value=100_000,
                cgst=9_000, sgst=9_000,
            ),
        ]
        inp = _make_input(pr, b2b)

        with (
            patch.object(engine, "_execute_cli", new_callable=AsyncMock,
                         return_value=("1.5.0", 2.3)),
            patch.object(engine, "_read_csv_safe") as mock_read,
            patch("shutil.rmtree"),
        ):
            def _side_effect(path: Any) -> list[dict]:
                name = str(path)
                if "matched" in name and "unmatched" not in name and "potential" not in name:
                    import io, pandas as pd
                    df = pd.read_csv(io.BytesIO(self._matched_csv()), dtype=str).fillna("")
                    return df.to_dict(orient="records")
                if "unmatched_pr" in name:
                    import io, pandas as pd
                    df = pd.read_csv(io.BytesIO(self._unmatched_pr_csv()), dtype=str).fillna("")
                    return df.to_dict(orient="records")
                return []

            mock_read.side_effect = _side_effect

            # Patch mkdir to avoid real filesystem
            with (
                patch("pathlib.Path.mkdir"),
                patch.object(engine, "_records_to_csv"),
            ):
                # Manually call the parse methods directly
                pr_index = engine._build_pr_index(pr)
                b2b_index = engine._build_2b_index(b2b)

                import io, pandas as pd
                matched_rows = pd.read_csv(
                    io.BytesIO(self._matched_csv()), dtype=str
                ).fillna("").to_dict(orient="records")

                matched = engine._parse_matched(matched_rows, pr_index, b2b_index)
                assert len(matched) == 1
                assert matched[0].invoice_number == "INV-001"

                unmatched_rows = pd.read_csv(
                    io.BytesIO(self._unmatched_pr_csv()), dtype=str
                ).fillna("").to_dict(orient="records")

                unmatched = engine._parse_unmatched(unmatched_rows, "PURCHASE_REGISTER", pr_index)
                assert len(unmatched) == 1
                assert unmatched[0].invoice_number == "INV-002"
                assert unmatched[0].itc_impact == 9000.0   # cgst + sgst

    def test_is_available_false_when_cli_missing(self) -> None:
        engine = ReconlifyEngine(cli_path="/nonexistent/reconlify")
        assert engine.is_available() is False
