"""Payment reminder sender - sends email reminders for upcoming/overdue invoices."""

from datetime import date
from models import Invoice, InvoiceStatus
from rich.console import Console

console = Console()

REMINDER_TEMPLATE = """
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
        <h2 style="color: #333;">Payment {reminder_type}</h2>
        <p>This is a friendly reminder about the following invoice:</p>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <tr style="background: #e9ecef;">
                <td style="padding: 8px; font-weight: bold;">Invoice #</td>
                <td style="padding: 8px;">{invoice_number}</td>
            </tr>
            <tr>
                <td style="padding: 8px; font-weight: bold;">Vendor</td>
                <td style="padding: 8px;">{vendor_name}</td>
            </tr>
            <tr style="background: #e9ecef;">
                <td style="padding: 8px; font-weight: bold;">Amount</td>
                <td style="padding: 8px;">${total_amount:.2f} {currency}</td>
            </tr>
            <tr>
                <td style="padding: 8px; font-weight: bold;">Due Date</td>
                <td style="padding: 8px;">{due_date}</td>
            </tr>
            <tr style="background: #e9ecef;">
                <td style="padding: 8px; font-weight: bold;">Status</td>
                <td style="padding: 8px; color: {status_color}; font-weight: bold;">{status_text}</td>
            </tr>
        </table>
        <p style="color: #666; font-size: 12px;">
            This is an automated reminder from your invoice processing system.
        </p>
    </div>
</body>
</html>
"""


class ReminderSender:
    """Checks invoices and sends payment reminders via Gmail."""

    def __init__(
        self,
        gmail_watcher,  # GmailWatcher instance (reuse for sending)
        first_reminder_days: int = 7,
        overdue_reminder_days: int = 1,
        notify_email: str = "",  # Who to notify (e.g., the business owner)
    ):
        self.gmail = gmail_watcher
        self.first_reminder_days = first_reminder_days
        self.overdue_reminder_days = overdue_reminder_days
        self.notify_email = notify_email

    def check_and_send(self, invoices: list[Invoice]) -> list[str]:
        """Check all invoices and send reminders as needed. Returns list of actions taken."""
        actions = []
        today = date.today()

        for inv in invoices:
            if inv.status == InvoiceStatus.PAID or not inv.due_date:
                continue

            days_left = inv.days_until_due

            if days_left is not None and days_left < 0:
                # Overdue
                self._send_reminder(inv, reminder_type="Overdue Notice")
                actions.append(
                    f"OVERDUE reminder sent: {inv.vendor_name} #{inv.invoice_number} "
                    f"({abs(days_left)} days overdue)"
                )

            elif days_left is not None and days_left <= self.first_reminder_days:
                # Coming due soon
                self._send_reminder(inv, reminder_type="Reminder")
                actions.append(
                    f"UPCOMING reminder sent: {inv.vendor_name} #{inv.invoice_number} "
                    f"(due in {days_left} days)"
                )

        if actions:
            for action in actions:
                console.print(f"[yellow]📧 {action}[/yellow]")
        else:
            console.print("[dim]No reminders needed[/dim]")

        return actions

    def _send_reminder(self, invoice: Invoice, reminder_type: str) -> None:
        """Send a single reminder email."""
        if not self.notify_email:
            return

        is_overdue = invoice.is_overdue
        html = REMINDER_TEMPLATE.format(
            reminder_type=reminder_type,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            total_amount=invoice.total_amount,
            currency=invoice.currency,
            due_date=invoice.due_date.isoformat() if invoice.due_date else "N/A",
            status_color="#dc3545" if is_overdue else "#ffc107",
            status_text="OVERDUE" if is_overdue else f"Due in {invoice.days_until_due} days",
        )

        subject = (
            f"{'⚠️ OVERDUE' if is_overdue else '📋 Upcoming'}: "
            f"Invoice #{invoice.invoice_number} from {invoice.vendor_name} "
            f"- ${invoice.total_amount:.2f}"
        )

        self.gmail.send_email(
            to=self.notify_email,
            subject=subject,
            html_body=html,
        )
