"""
Invoice Processing Automation - Main Orchestrator
===================================================
Ties together Gmail watching, AI extraction, anomaly detection,
Google Sheets output, and payment reminders into a single loop.

Usage:
    python src/main.py                    # Run once
    python src/main.py --daemon           # Run continuously (poll mode)
    python src/main.py --test invoice.pdf # Test with a single file
"""

import argparse
import time
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from invoice_parser import InvoiceParser
from gmail_watcher import GmailWatcher
from drive_watcher import DriveWatcher
from sheets_writer import SheetsWriter
from anomaly_detector import AnomalyDetector
from reminder_sender import ReminderSender
from models import Invoice, ExtractionResult

console = Console()


def load_config(config_path: str = "config/config.yaml") -> dict:
    """Load configuration from YAML file."""
    path = Path(config_path)
    if not path.exists():
        console.print(
            f"[red]Config not found: {config_path}[/red]\n"
            "Copy config/config.example.yaml to config/config.yaml and fill in your values."
        )
        sys.exit(1)

    with open(path) as f:
        return yaml.safe_load(f)


def _service_token(base_token_file: str, service: str) -> str:
    """Derive a per-service token path, e.g. config/token.json -> config/token_gmail.json."""
    p = Path(base_token_file)
    return str(p.parent / f"{p.stem}_{service}{p.suffix}")


def run_once(config: dict) -> list[Invoice]:
    """Run a single processing cycle. Returns list of processed invoices."""
    cfg = config

    console.print(Panel("🧾 Invoice Processing Cycle", style="bold blue"))

    base_token = cfg["google"]["token_file"]
    creds_file = cfg["google"]["credentials_file"]

    # --- 1. Initialize components ---
    parser = InvoiceParser(
        api_key=cfg["gemini"]["api_key"],
        model=cfg["gemini"].get("model", "gemini-2.5-pro"),
    )

    sheets = SheetsWriter(
        credentials_file=creds_file,
        token_file=_service_token(base_token, "sheets"),
        spreadsheet_id=cfg["sheets"]["spreadsheet_id"],
        sheet_name=cfg["sheets"].get("sheet_name", "Invoices"),
    )
    sheets.authenticate()
    sheets.ensure_headers()

    # Get existing invoice numbers for duplicate detection
    existing_numbers = sheets.get_existing_invoice_numbers()

    anomaly = AnomalyDetector(
        high_amount_threshold=cfg["anomaly"].get("high_amount_threshold", 10000),
        low_confidence_threshold=cfg["anomaly"].get("low_confidence_threshold", 0.7),
        existing_invoice_numbers=existing_numbers,
    )

    processed_invoices: list[Invoice] = []

    # --- 2. Fetch invoices from Gmail ---
    gmail = GmailWatcher(
        credentials_file=creds_file,
        token_file=_service_token(base_token, "gmail"),
        search_query=cfg["gmail"]["search_query"],
    )
    gmail.authenticate()

    emails = gmail.fetch_new_invoices()
    for email_data in emails:
        for attachment in email_data["attachments"]:
            # Extract data using AI
            result: ExtractionResult = parser.parse_bytes(
                data=attachment["data"],
                filename=attachment["filename"],
                mime_type=attachment["mime_type"],
            )

            if result.success and result.invoice:
                invoice = result.invoice
                invoice.source_email_id = email_data["email_id"]
                invoice = anomaly.check(invoice)
                sheets.write_invoice(invoice)
                processed_invoices.append(invoice)
                email_success = True

            else:
                console.print(
                    f"[red]✗ Failed to parse {attachment['filename']}:[/red] "
                    f"{result.error_message}"
                )
                email_success = False

        # Mark email as processed ONLY if successful
        if email_success and cfg["gmail"].get("mark_as_processed", True):
            gmail.mark_as_processed(email_data["email_id"])
        elif not email_success:
            console.print(f"[yellow]⚠ Skipping mark-as-processed for {email_data['subject']} due to errors[/yellow]")

    # --- 3. Fetch from Google Drive (if enabled) ---
    if cfg.get("drive", {}).get("enabled", False):
        drive = DriveWatcher(
            credentials_file=creds_file,
            token_file=_service_token(base_token, "drive"),
            folder_id=cfg["drive"]["folder_id"],
        )
        drive.authenticate()

        for file_info in drive.fetch_new_files():
            data, filename = drive.download_file(file_info["id"])
            result = parser.parse_bytes(
                data=data,
                filename=filename,
                mime_type=file_info["mimeType"],
            )

            if result.success and result.invoice:
                invoice = anomaly.check(result.invoice)
                sheets.write_invoice(invoice)
                processed_invoices.append(invoice)
                drive.mark_as_processed(file_info["id"])

    # --- 4. Send payment reminders ---
    if cfg.get("reminders", {}).get("enabled", False):
        reminder = ReminderSender(
            gmail_watcher=gmail,
            first_reminder_days=cfg["reminders"].get("first_reminder_days", 7),
            overdue_reminder_days=cfg["reminders"].get("overdue_reminder_days", 1),
            notify_email=cfg["reminders"].get("notify_email", ""),
        )
        reminder.check_and_send(processed_invoices)

    # --- 5. Print summary ---
    print_summary(processed_invoices)

    return processed_invoices


def test_file(config: dict, file_path: str) -> None:
    """Test parsing a single invoice file (no Gmail/Sheets needed)."""
    console.print(Panel(f"🧪 Test Mode: {file_path}", style="bold yellow"))

    parser = InvoiceParser(
        api_key=config["gemini"]["api_key"],
        model=config["gemini"].get("model", "gemini-2.5-pro"),
    )

    result = parser.parse_file(file_path)

    if result.success and result.invoice:
        inv = result.invoice
        table = Table(title="Extracted Invoice Data")
        table.add_column("Field", style="bold")
        table.add_column("Value")

        table.add_row("Invoice #", inv.invoice_number)
        table.add_row("Vendor", inv.vendor_name)
        table.add_row("Address", inv.vendor_address or "—")
        table.add_row("Email", inv.vendor_email or "—")
        table.add_row("Date", str(inv.invoice_date))
        table.add_row("Due Date", str(inv.due_date) if inv.due_date else "—")
        table.add_row("Terms", inv.payment_terms or "—")
        table.add_row("Subtotal", f"${inv.subtotal:.2f}")
        table.add_row("Tax", f"${inv.tax_amount:.2f}")
        table.add_row("Total", f"${inv.total_amount:.2f} {inv.currency}")
        table.add_row("Confidence", f"{inv.confidence_score:.0%}")
        table.add_row("Line Items", str(len(inv.line_items)))

        console.print(table)

        if inv.line_items:
            li_table = Table(title="Line Items")
            li_table.add_column("Description")
            li_table.add_column("Qty", justify="right")
            li_table.add_column("Unit Price", justify="right")
            li_table.add_column("Total", justify="right")
            for li in inv.line_items:
                li_table.add_row(
                    li.description,
                    f"{li.quantity}",
                    f"${li.unit_price:.2f}",
                    f"${li.total:.2f}",
                )
            console.print(li_table)
    else:
        console.print(f"[red]Failed:[/red] {result.error_message}")
        if result.raw_response:
            console.print(f"[dim]Raw response:[/dim]\n{result.raw_response}")


def print_summary(invoices: list[Invoice]) -> None:
    """Print a summary table of processed invoices."""
    if not invoices:
        console.print("[dim]No invoices processed this cycle[/dim]")
        return

    table = Table(title=f"📊 Processed {len(invoices)} Invoice(s)")
    table.add_column("Vendor")
    table.add_column("Invoice #")
    table.add_column("Amount", justify="right")
    table.add_column("Due", justify="center")
    table.add_column("Status")
    table.add_column("Flags")

    total = 0.0
    for inv in invoices:
        status_style = (
            "green" if inv.status.value == "pending"
            else "yellow" if inv.status.value == "flagged"
            else "red"
        )
        table.add_row(
            inv.vendor_name,
            inv.invoice_number,
            f"${inv.total_amount:.2f}",
            str(inv.due_date) if inv.due_date else "—",
            f"[{status_style}]{inv.status.value}[/{status_style}]",
            ", ".join(inv.flags[:2]) if inv.flags else "✓",
        )
        total += inv.total_amount

    console.print(table)
    console.print(f"[bold]Total: ${total:,.2f}[/bold]")


def main():
    parser = argparse.ArgumentParser(description="Invoice Processing Automation")
    parser.add_argument(
        "--config", default="config/config.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--daemon", action="store_true", help="Run continuously in polling mode"
    )
    parser.add_argument(
        "--test", type=str, help="Test with a single invoice file"
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.test:
        test_file(config, args.test)
        return

    if args.daemon:
        interval = config["gmail"].get("poll_interval_seconds", 300)
        console.print(
            f"[bold green]Starting daemon mode[/bold green] "
            f"(polling every {interval}s)"
        )
        while True:
            try:
                run_once(config)
            except Exception as e:
                console.print(f"[red]Error in cycle:[/red] {e}")
            console.print(f"[dim]Sleeping {interval}s...[/dim]\n")
            time.sleep(interval)
    else:
        run_once(config)


if __name__ == "__main__":
    main()
