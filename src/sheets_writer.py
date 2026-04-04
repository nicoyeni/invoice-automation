"""Google Sheets writer - pushes extracted invoice data to a spreadsheet."""

from typing import Optional
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from rich.console import Console

from models import Invoice

console = Console()


class SheetsWriter:
    """Writes extracted invoice data to Google Sheets."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    # Default column layout
    HEADERS = [
        "Date",
        "Invoice #",
        "Vendor",
        "Amount",
        "Currency",
        "Tax",
        "Subtotal",
        "Due Date",
        "Terms",
        "Status",
        "AI Confidence",
        "Flags",
        "Source File",
        "Line Items",
    ]

    def __init__(
        self,
        credentials_file: str,
        token_file: str,
        spreadsheet_id: str,
        sheet_name: str = "Invoices",
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.service = None

    def authenticate(self) -> None:
        """Authenticate with Google Sheets API."""
        creds = None
        token_path = Path(self.token_file)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(token_path), self.SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None
            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        self.service = build("sheets", "v4", credentials=creds)
        console.print("[green]✓ Google Sheets authenticated[/green]")

    def ensure_headers(self) -> None:
        """Make sure the header row exists in the sheet."""
        if not self.service:
            self.authenticate()

        range_name = f"{self.sheet_name}!A1:{chr(64 + len(self.HEADERS))}1"

        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )

        existing = result.get("values", [])
        if not existing or existing[0] != self.HEADERS:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [self.HEADERS]},
            ).execute()
            console.print("[blue]Headers written to sheet[/blue]")

    def write_invoice(self, invoice: Invoice) -> None:
        """Append a single invoice as a new row."""
        if not self.service:
            self.authenticate()

        # Format line items as a readable string
        line_items_str = "; ".join(
            f"{li.description} (x{li.quantity} @ ${li.unit_price:.2f} = ${li.total:.2f})"
            for li in invoice.line_items
        )

        row = [
            invoice.invoice_date.isoformat(),
            invoice.invoice_number,
            invoice.vendor_name,
            invoice.total_amount,
            invoice.currency,
            invoice.tax_amount,
            invoice.subtotal,
            invoice.due_date.isoformat() if invoice.due_date else "",
            invoice.payment_terms or "",
            invoice.status.value,
            f"{invoice.confidence_score:.0%}",
            ", ".join(invoice.flags) if invoice.flags else "",
            invoice.source_file,
            line_items_str,
        ]

        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A:N",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

        console.print(
            f"[green]✓ Written to sheet:[/green] {invoice.vendor_name} - "
            f"${invoice.total_amount:.2f}"
        )

    def write_invoices(self, invoices: list[Invoice]) -> None:
        """Write multiple invoices at once (batch)."""
        self.ensure_headers()
        for invoice in invoices:
            self.write_invoice(invoice)

    def get_existing_invoice_numbers(self) -> set[str]:
        """Fetch all invoice numbers already in the sheet (for duplicate detection)."""
        if not self.service:
            self.authenticate()

        # Invoice # is column B
        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.sheet_name}!B:B",
            )
            .execute()
        )

        values = result.get("values", [])
        # Skip header row
        return {row[0] for row in values[1:] if row}
