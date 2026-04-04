"""Unit tests for models.py — Invoice, LineItem, ExtractionResult."""

from datetime import date, timedelta
import pytest
from models import Invoice, LineItem, InvoiceStatus, ExtractionResult


class TestLineItem:
    def test_computed_total(self):
        li = LineItem(description="Widget", quantity=3.0, unit_price=10.0, total=30.0)
        assert li.computed_total == 30.0

    def test_computed_total_fractional(self):
        li = LineItem(description="Service", quantity=1.5, unit_price=100.0, total=150.0)
        assert li.computed_total == 150.0

    def test_total_field_independent_of_computed(self):
        # total is stored separately; computed_total derives from qty × unit_price
        li = LineItem(description="Mismatch", quantity=2.0, unit_price=10.0, total=999.0)
        assert li.total == 999.0
        assert li.computed_total == 20.0


class TestInvoice:
    def test_defaults(self):
        inv = Invoice(vendor_name="ACME", subtotal=100.0, total_amount=100.0)
        assert inv.invoice_number == "UNKNOWN"
        assert inv.currency == "USD"
        assert inv.tax_amount == 0.0
        assert inv.status == InvoiceStatus.PENDING
        assert inv.flags == []
        assert inv.line_items == []

    def test_is_overdue_when_past_due(self):
        past = date.today() - timedelta(days=1)
        inv = Invoice(
            vendor_name="ACME",
            subtotal=100.0,
            total_amount=100.0,
            due_date=past,
            status=InvoiceStatus.PENDING,
        )
        assert inv.is_overdue is True

    def test_is_not_overdue_when_future(self):
        future = date.today() + timedelta(days=30)
        inv = Invoice(
            vendor_name="ACME",
            subtotal=100.0,
            total_amount=100.0,
            due_date=future,
        )
        assert inv.is_overdue is False

    def test_is_not_overdue_when_paid(self):
        past = date.today() - timedelta(days=5)
        inv = Invoice(
            vendor_name="ACME",
            subtotal=100.0,
            total_amount=100.0,
            due_date=past,
            status=InvoiceStatus.PAID,
        )
        assert inv.is_overdue is False

    def test_is_not_overdue_without_due_date(self):
        inv = Invoice(vendor_name="ACME", subtotal=100.0, total_amount=100.0)
        assert inv.is_overdue is False

    def test_days_until_due_future(self):
        future = date.today() + timedelta(days=10)
        inv = Invoice(
            vendor_name="ACME",
            subtotal=100.0,
            total_amount=100.0,
            due_date=future,
        )
        assert inv.days_until_due == 10

    def test_days_until_due_past(self):
        past = date.today() - timedelta(days=3)
        inv = Invoice(
            vendor_name="ACME",
            subtotal=100.0,
            total_amount=100.0,
            due_date=past,
        )
        assert inv.days_until_due == -3

    def test_days_until_due_none_without_date(self):
        inv = Invoice(vendor_name="ACME", subtotal=100.0, total_amount=100.0)
        assert inv.days_until_due is None

    def test_status_enum_values(self):
        assert InvoiceStatus.PENDING.value == "pending"
        assert InvoiceStatus.FLAGGED.value == "flagged"
        assert InvoiceStatus.APPROVED.value == "approved"
        assert InvoiceStatus.PAID.value == "paid"
        assert InvoiceStatus.OVERDUE.value == "overdue"

    def test_model_dump_json_serializable(self, sample_invoice):
        import json
        data = sample_invoice.model_dump(mode="json")
        # Should be JSON-serializable (no date objects)
        json.dumps(data)
        assert data["vendor_name"] == "Acme Corp"
        assert data["invoice_number"] == "INV-001"


class TestExtractionResult:
    def test_success_result(self, sample_invoice):
        result = ExtractionResult(invoice=sample_invoice, success=True, raw_response="{}")
        assert result.success is True
        assert result.invoice is not None
        assert result.error_message is None

    def test_failure_result(self):
        result = ExtractionResult(success=False, error_message="Gemini timeout")
        assert result.success is False
        assert result.invoice is None
        assert result.error_message == "Gemini timeout"

    def test_default_success_is_true(self):
        result = ExtractionResult()
        assert result.success is True
