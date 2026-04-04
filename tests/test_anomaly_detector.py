"""Unit tests for anomaly_detector.py — all 7 anomaly checks."""

from datetime import date, timedelta
import pytest
from models import Invoice, LineItem, InvoiceStatus
from anomaly_detector import AnomalyDetector


def make_clean_invoice(**overrides) -> Invoice:
    """Return a valid invoice with no anomalies by default."""
    defaults = dict(
        invoice_number="INV-999",
        vendor_name="Good Corp",
        subtotal=500.0,
        tax_amount=50.0,
        total_amount=550.0,
        confidence_score=0.95,
        due_date=date.today() + timedelta(days=30),
        line_items=[
            LineItem(description="Service", quantity=1.0, unit_price=500.0, total=500.0)
        ],
    )
    defaults.update(overrides)
    return Invoice(**defaults)


class TestAnomalyDetector:
    def setup_method(self):
        self.detector = AnomalyDetector()

    # ── 1. Duplicate detection ────────────────────────────────────────────
    def test_duplicate_invoice_number(self):
        detector = AnomalyDetector(existing_invoice_numbers={"INV-001"})
        inv = make_clean_invoice(invoice_number="INV-001")
        result = detector.check(inv)
        assert any("DUPLICATE" in f for f in result.flags)
        assert result.status == InvoiceStatus.FLAGGED

    def test_no_duplicate_when_number_unique(self):
        detector = AnomalyDetector(existing_invoice_numbers={"INV-001"})
        inv = make_clean_invoice(invoice_number="INV-002")
        result = detector.check(inv)
        assert not any("DUPLICATE" in f for f in result.flags)

    # ── 2. High amount ────────────────────────────────────────────────────
    def test_high_amount_flagged(self):
        inv = make_clean_invoice(total_amount=15000.0, subtotal=15000.0, tax_amount=0.0)
        result = self.detector.check(inv)
        assert any("HIGH_AMOUNT" in f for f in result.flags)

    def test_high_amount_custom_threshold(self):
        detector = AnomalyDetector(high_amount_threshold=5000)
        inv = make_clean_invoice(total_amount=6000.0, subtotal=6000.0, tax_amount=0.0)
        result = detector.check(inv)
        assert any("HIGH_AMOUNT" in f for f in result.flags)

    def test_amount_below_threshold_not_flagged(self):
        inv = make_clean_invoice(total_amount=9999.0, subtotal=9999.0, tax_amount=0.0)
        result = self.detector.check(inv)
        assert not any("HIGH_AMOUNT" in f for f in result.flags)

    # ── 3. Low confidence ─────────────────────────────────────────────────
    def test_low_confidence_flagged(self):
        inv = make_clean_invoice(confidence_score=0.5)
        result = self.detector.check(inv)
        assert any("LOW_CONFIDENCE" in f for f in result.flags)

    def test_confidence_at_threshold_not_flagged(self):
        inv = make_clean_invoice(confidence_score=0.7)
        result = self.detector.check(inv)
        assert not any("LOW_CONFIDENCE" in f for f in result.flags)

    def test_custom_confidence_threshold(self):
        detector = AnomalyDetector(low_confidence_threshold=0.9)
        inv = make_clean_invoice(confidence_score=0.85)
        result = detector.check(inv)
        assert any("LOW_CONFIDENCE" in f for f in result.flags)

    # ── 4. Line items math mismatch ───────────────────────────────────────
    def test_line_items_sum_mismatch(self):
        inv = make_clean_invoice(
            subtotal=600.0,  # wrong — line items sum to 500
            tax_amount=60.0,
            total_amount=660.0,
            line_items=[
                LineItem(description="Service", quantity=1.0, unit_price=500.0, total=500.0)
            ],
        )
        result = self.detector.check(inv)
        assert any("MATH_MISMATCH" in f for f in result.flags)

    def test_line_items_match_within_tolerance(self):
        inv = make_clean_invoice(
            subtotal=500.01,  # within 2 cents
            tax_amount=49.99,
            total_amount=550.0,
            line_items=[
                LineItem(description="Service", quantity=1.0, unit_price=500.0, total=500.0)
            ],
        )
        result = self.detector.check(inv)
        assert not any("MATH_MISMATCH" in f for f in result.flags)

    # ── 5. Total mismatch ─────────────────────────────────────────────────
    def test_total_mismatch(self):
        inv = make_clean_invoice(
            subtotal=500.0,
            tax_amount=50.0,
            total_amount=600.0,  # should be 550
        )
        result = self.detector.check(inv)
        assert any("TOTAL_MISMATCH" in f for f in result.flags)

    def test_total_matches_subtotal_plus_tax(self):
        inv = make_clean_invoice(subtotal=500.0, tax_amount=50.0, total_amount=550.0)
        result = self.detector.check(inv)
        assert not any("TOTAL_MISMATCH" in f for f in result.flags)

    # ── 6. Missing critical fields ────────────────────────────────────────
    def test_missing_invoice_number_unknown(self):
        inv = make_clean_invoice(invoice_number="UNKNOWN")
        result = self.detector.check(inv)
        assert "MISSING_INVOICE_NUMBER" in result.flags

    def test_missing_invoice_number_empty(self):
        inv = make_clean_invoice(invoice_number="")
        result = self.detector.check(inv)
        assert "MISSING_INVOICE_NUMBER" in result.flags

    def test_missing_vendor_name(self):
        inv = make_clean_invoice(vendor_name="UNKNOWN")
        result = self.detector.check(inv)
        assert "MISSING_VENDOR_NAME" in result.flags

    def test_missing_due_date(self):
        inv = make_clean_invoice(due_date=None)
        result = self.detector.check(inv)
        assert "NO_DUE_DATE" in result.flags

    # ── 7. Overdue detection ──────────────────────────────────────────────
    def test_overdue_flagged(self):
        inv = make_clean_invoice(due_date=date.today() - timedelta(days=5))
        result = self.detector.check(inv)
        assert any("OVERDUE" in f for f in result.flags)

    def test_not_overdue_future_date(self):
        inv = make_clean_invoice(due_date=date.today() + timedelta(days=5))
        result = self.detector.check(inv)
        assert not any("OVERDUE" in f for f in result.flags)

    # ── Clean invoice ─────────────────────────────────────────────────────
    def test_clean_invoice_no_flags(self):
        inv = make_clean_invoice()
        result = self.detector.check(inv)
        assert result.flags == []
        assert result.status == InvoiceStatus.PENDING

    def test_multiple_flags_set_flagged_status(self):
        inv = make_clean_invoice(
            invoice_number="UNKNOWN",
            confidence_score=0.3,
            total_amount=99999.0,
            subtotal=99999.0,
            tax_amount=0.0,
        )
        result = self.detector.check(inv)
        assert len(result.flags) >= 2
        assert result.status == InvoiceStatus.FLAGGED
