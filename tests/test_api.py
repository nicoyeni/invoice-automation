"""Integration tests for the FastAPI backend (src/api.py)."""

import sys
import json
import io
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Patch genai.Client before importing api (avoids needing real credentials at import time)
with patch("invoice_parser.genai.Client"):
    import api
    from api import app, _invoices

from models import Invoice, InvoiceStatus, ExtractionResult, LineItem


# ── Fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clear_store():
    """Reset in-memory invoice store before each test."""
    _invoices.clear()
    api._status.update({"running": False, "last_run": None, "last_count": 0, "error": None})
    yield
    _invoices.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_invoice():
    return Invoice(
        invoice_number="INV-TEST-1",
        vendor_name="Test Vendor",
        subtotal=500.0,
        tax_amount=50.0,
        total_amount=550.0,
        currency="USD",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        confidence_score=0.90,
        status=InvoiceStatus.PENDING,
        line_items=[LineItem(description="Service", quantity=1.0, unit_price=500.0, total=500.0)],
    )


FAKE_CONFIG = {
    "gemini": {"api_key": "fake-key", "model": "gemini-2.5-pro"},
    "google": {"credentials_file": "creds.json", "token_file": "token.json"},
    "sheets": {"spreadsheet_id": "sheet-123", "sheet_name": "Invoices"},
    "gmail": {"search_query": "has:attachment", "poll_interval_seconds": 300},
    "anomaly": {"high_amount_threshold": 10000, "low_confidence_threshold": 0.7},
}


# ── Health ────────────────────────────────────────────────────────────────
class TestHealth:
    def test_health_ok(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "invoice_count" in data

    def test_health_reflects_config_missing(self, client):
        with patch("api._load_config", return_value=None):
            res = client.get("/api/health")
        assert res.json()["config_loaded"] is False

    def test_health_reflects_config_present(self, client):
        with patch("api._load_config", return_value=FAKE_CONFIG):
            res = client.get("/api/health")
        assert res.json()["config_loaded"] is True


# ── Stats ─────────────────────────────────────────────────────────────────
class TestStats:
    def test_empty_stats(self, client):
        res = client.get("/api/stats")
        assert res.status_code == 200
        data = res.json()
        assert data["total_invoices"] == 0
        assert data["total_amount"] == 0.0
        assert data["flagged"] == 0

    def test_stats_with_invoices(self, client, sample_invoice):
        _invoices.append(sample_invoice)
        flagged = sample_invoice.model_copy()
        flagged.status = InvoiceStatus.FLAGGED
        _invoices.append(flagged)

        res = client.get("/api/stats")
        data = res.json()
        assert data["total_invoices"] == 2
        assert data["flagged"] == 1
        assert data["total_amount"] == pytest.approx(1100.0)


# ── List Invoices ─────────────────────────────────────────────────────────
class TestListInvoices:
    def test_empty_list(self, client):
        res = client.get("/api/invoices")
        assert res.status_code == 200
        assert res.json() == []

    def test_returns_all_invoices(self, client, sample_invoice):
        _invoices.append(sample_invoice)
        res = client.get("/api/invoices")
        data = res.json()
        assert len(data) == 1
        assert data[0]["invoice_number"] == "INV-TEST-1"

    def test_filter_by_status_pending(self, client, sample_invoice):
        _invoices.append(sample_invoice)
        flagged = sample_invoice.model_copy(update={"invoice_number": "INV-F", "status": InvoiceStatus.FLAGGED})
        _invoices.append(flagged)

        res = client.get("/api/invoices?status=pending")
        data = res.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"

    def test_filter_by_status_flagged(self, client, sample_invoice):
        flagged = sample_invoice.model_copy(update={"invoice_number": "INV-F", "status": InvoiceStatus.FLAGGED})
        _invoices.append(flagged)
        _invoices.append(sample_invoice)

        res = client.get("/api/invoices?status=flagged")
        data = res.json()
        assert len(data) == 1
        assert data[0]["status"] == "flagged"


# ── Upload ────────────────────────────────────────────────────────────────
class TestUpload:
    def _make_fake_result(self, invoice: Invoice) -> ExtractionResult:
        return ExtractionResult(invoice=invoice, success=True, raw_response="{}")

    def test_upload_no_config_returns_503(self, client):
        with patch("api._load_config", return_value=None):
            res = client.post("/api/upload", files={"file": ("inv.png", b"data", "image/png")})
        assert res.status_code == 503

    def test_upload_success(self, client, sample_invoice):
        with patch("api._load_config", return_value=FAKE_CONFIG):
            with patch("api.InvoiceParser") as MockParser:
                instance = MockParser.return_value
                instance.parse_file.return_value = self._make_fake_result(sample_invoice)

                res = client.post(
                    "/api/upload",
                    files={"file": ("invoice.png", b"\x89PNG\r\n\x1a\n" + b"\x00"*50, "image/png")},
                )

        assert res.status_code == 200
        data = res.json()
        assert data["vendor_name"] == "Test Vendor"
        assert len(_invoices) == 1

    def test_upload_failed_extraction_returns_422(self, client):
        with patch("api._load_config", return_value=FAKE_CONFIG):
            with patch("api.InvoiceParser") as MockParser:
                instance = MockParser.return_value
                instance.parse_file.return_value = ExtractionResult(
                    success=False, error_message="Could not parse"
                )

                res = client.post(
                    "/api/upload",
                    files={"file": ("bad.png", b"not-an-image", "image/png")},
                )

        assert res.status_code == 422

    def test_upload_adds_to_store(self, client, sample_invoice):
        assert len(_invoices) == 0
        with patch("api._load_config", return_value=FAKE_CONFIG):
            with patch("api.InvoiceParser") as MockParser:
                MockParser.return_value.parse_file.return_value = self._make_fake_result(sample_invoice)
                client.post("/api/upload", files={"file": ("inv.png", b"\x89PNG" + b"\x00"*50, "image/png")})
        assert len(_invoices) == 1


# ── Process ───────────────────────────────────────────────────────────────
class TestProcess:
    def test_process_no_config_returns_503(self, client):
        with patch("api._load_config", return_value=None):
            res = client.post("/api/process")
        assert res.status_code == 503

    def test_process_starts_background_task(self, client):
        with patch("api._load_config", return_value=FAKE_CONFIG):
            res = client.post("/api/process")
        assert res.status_code == 200
        assert res.json()["status"] == "running"

    def test_process_409_when_already_running(self, client):
        api._status["running"] = True
        with patch("api._load_config", return_value=FAKE_CONFIG):
            res = client.post("/api/process")
        assert res.status_code == 409


# ── Status ────────────────────────────────────────────────────────────────
class TestStatus:
    def test_initial_status(self, client):
        res = client.get("/api/status")
        data = res.json()
        assert data["running"] is False
        assert data["last_run"] is None

    def test_status_reflects_error(self, client):
        api._status["error"] = "something broke"
        res = client.get("/api/status")
        assert res.json()["error"] == "something broke"


# ── Delete ────────────────────────────────────────────────────────────────
class TestDelete:
    def test_delete_existing(self, client, sample_invoice):
        _invoices.append(sample_invoice)
        res = client.delete(f"/api/invoices/{sample_invoice.invoice_number}")
        assert res.status_code == 200
        assert len(_invoices) == 0

    def test_delete_nonexistent_404(self, client):
        res = client.delete("/api/invoices/DOES-NOT-EXIST")
        assert res.status_code == 404

    def test_clear_all(self, client, sample_invoice):
        _invoices.append(sample_invoice)
        res = client.delete("/api/invoices")
        assert res.status_code == 200
        assert len(_invoices) == 0
