"""Anomaly detector — flags suspicious or problematic invoices."""

from datetime import date
from rich.console import Console
from models import Invoice, InvoiceStatus

console = Console()


class AnomalyDetector:
    """Checks extracted invoices for anomalies and flags issues."""

    def __init__(
        self,
        high_amount_threshold: float = 10000,
        low_confidence_threshold: float = 0.7,
        existing_invoice_numbers: set[str] | None = None,
        max_future_days: int = 30,
        high_tax_rate: float = 0.30,
        existing_invoices: list | None = None,
        recurring_window_days: int = 45,
    ):
        self.high_amount_threshold    = high_amount_threshold
        self.low_confidence_threshold = low_confidence_threshold
        self.existing_invoice_numbers = existing_invoice_numbers or set()
        self.max_future_days          = max_future_days
        self.high_tax_rate            = high_tax_rate
        self.existing_invoices        = existing_invoices or []
        self.recurring_window_days    = recurring_window_days

    def check(self, invoice: Invoice) -> Invoice:
        """Run all anomaly checks. Populates invoice.flags and sets status."""
        flags = []

        # 1. Duplicate invoice number
        if invoice.invoice_number and invoice.invoice_number not in ("UNKNOWN", ""):
            if invoice.invoice_number in self.existing_invoice_numbers:
                flags.append(f"DUPLICATE: Invoice #{invoice.invoice_number} already exists")

        # 2. High amount
        if invoice.total_amount > self.high_amount_threshold:
            flags.append(
                f"HIGH_AMOUNT: ${invoice.total_amount:,.2f} exceeds "
                f"${self.high_amount_threshold:,.2f} threshold"
            )

        # 3. Zero or negative amount
        if invoice.total_amount <= 0:
            flags.append(f"ZERO_AMOUNT: Total is ${invoice.total_amount:.2f}")

        # 4. Low AI confidence
        if invoice.confidence_score < self.low_confidence_threshold:
            flags.append(
                f"LOW_CONFIDENCE: {invoice.confidence_score:.0%} "
                f"(threshold: {self.low_confidence_threshold:.0%})"
            )

        # 5. Line item math mismatch
        if invoice.line_items:
            computed_subtotal = sum(li.total for li in invoice.line_items)
            if abs(computed_subtotal - invoice.subtotal) > 0.02:
                flags.append(
                    f"MATH_MISMATCH: Line items sum to ${computed_subtotal:.2f} "
                    f"but subtotal is ${invoice.subtotal:.2f}"
                )

        # 6. Subtotal + tax != total
        expected_total = invoice.subtotal + invoice.tax_amount
        if abs(expected_total - invoice.total_amount) > 0.02:
            flags.append(
                f"TOTAL_MISMATCH: ${invoice.subtotal:.2f} + ${invoice.tax_amount:.2f} tax "
                f"= ${expected_total:.2f}, but total is ${invoice.total_amount:.2f}"
            )

        # 7. Unusually high tax rate
        if invoice.subtotal > 0 and invoice.tax_amount > 0:
            tax_rate = invoice.tax_amount / invoice.subtotal
            if tax_rate > self.high_tax_rate:
                flags.append(
                    f"HIGH_TAX_RATE: {tax_rate:.0%} (${invoice.tax_amount:.2f} tax "
                    f"on ${invoice.subtotal:.2f} subtotal)"
                )

        # 8. Missing critical fields
        if invoice.invoice_number in ("UNKNOWN", "", None):
            flags.append("MISSING_INVOICE_NUMBER")

        if not invoice.vendor_name or invoice.vendor_name in ("UNKNOWN", ""):
            flags.append("MISSING_VENDOR_NAME")

        if not invoice.due_date:
            flags.append("NO_DUE_DATE")

        # 9. Future-dated invoice (beyond allowed window)
        if invoice.invoice_date:
            days_ahead = (invoice.invoice_date - date.today()).days
            if days_ahead > self.max_future_days:
                flags.append(
                    f"FUTURE_DATE: Invoice dated {days_ahead} days in the future "
                    f"({invoice.invoice_date})"
                )

        # 10. Suspiciously round number (possible estimate rather than actual)
        if invoice.total_amount > 0 and invoice.total_amount % 100 == 0 and invoice.total_amount >= 1000:
            if not invoice.line_items:
                flags.append(
                    f"ROUND_AMOUNT: ${invoice.total_amount:,.0f} is a suspiciously round number "
                    f"with no line items — may be an estimate"
                )

        # 11. Overdue at time of processing
        if invoice.is_overdue:
            flags.append(f"OVERDUE: {abs(invoice.days_until_due)} days past due")

        # 12. Recurring invoice — same vendor + similar amount within window
        if self.existing_invoices and invoice.invoice_date and invoice.vendor_name not in ("UNKNOWN", ""):
            from datetime import timedelta
            window_start = invoice.invoice_date - timedelta(days=self.recurring_window_days)
            threshold_pct = 0.05  # within 5%
            for existing in self.existing_invoices:
                if (
                    existing.vendor_name == invoice.vendor_name
                    and existing.invoice_date
                    and window_start <= existing.invoice_date < invoice.invoice_date
                    and invoice.total_amount > 0
                    and abs(existing.total_amount - invoice.total_amount) / invoice.total_amount <= threshold_pct
                ):
                    flags.append(
                        f"RECURRING: Similar invoice from {invoice.vendor_name} "
                        f"(${existing.total_amount:,.2f} on {existing.invoice_date}) "
                        f"within {self.recurring_window_days} days"
                    )
                    break

        # Apply
        invoice.flags = flags
        if flags:
            invoice.status = InvoiceStatus.FLAGGED
            console.print(
                f"[yellow]⚠ Flagged {invoice.vendor_name}:[/yellow] "
                f"{', '.join(f.split(':')[0] for f in flags)}"
            )
        else:
            console.print(f"[green]✓ No anomalies:[/green] {invoice.vendor_name}")

        return invoice
