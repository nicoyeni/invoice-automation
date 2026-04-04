"""Shared pytest fixtures for invoice-automation tests."""

import sys
from pathlib import Path
from datetime import date

import pytest

# Add src/ to path so tests can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models import Invoice, LineItem, InvoiceStatus


@pytest.fixture
def sample_line_items():
    return [
        LineItem(description="Widget A", quantity=2.0, unit_price=500.0, total=1000.0),
        LineItem(description="Widget B", quantity=1.0, unit_price=200.0, total=200.0),
    ]


@pytest.fixture
def sample_invoice(sample_line_items):
    return Invoice(
        invoice_number="INV-001",
        vendor_name="Acme Corp",
        vendor_address="123 Main St, Springfield",
        vendor_email="billing@acme.com",
        subtotal=1200.0,
        tax_amount=120.0,
        total_amount=1320.0,
        currency="USD",
        line_items=sample_line_items,
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        payment_terms="Net 30",
        source_file="acme_inv_001.pdf",
        confidence_score=0.95,
        status=InvoiceStatus.PENDING,
    )


@pytest.fixture
def flagged_invoice(sample_invoice):
    """An invoice that has already been flagged."""
    sample_invoice.status = InvoiceStatus.FLAGGED
    sample_invoice.flags = ["HIGH_AMOUNT: $99999 exceeds threshold"]
    return sample_invoice
