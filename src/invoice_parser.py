"""Invoice parser using Gemini AI for structured data extraction."""

import json
import base64
from pathlib import Path
from datetime import date

from google import genai
from google.genai import types
from rich.console import Console

from models import Invoice, LineItem, ExtractionResult, InvoiceStatus

console = Console()

# The extraction prompt - this is the core of the whole system.
# Tuned for accuracy on real-world invoices.
EXTRACTION_PROMPT = """You are an expert invoice data extractor. Analyze this invoice document and extract ALL data into the exact JSON structure below.

RULES:
- Extract every field you can find. Use null for fields truly not present.
- For dates, use ISO format: YYYY-MM-DD
- For amounts, use numbers only (no currency symbols). Use the decimal format (e.g., 1234.56)
- invoice_number: look for "Invoice #", "Invoice No.", "Inv", "Bill #", etc.
- If payment terms say "Net 30", calculate due_date = invoice_date + 30 days
- Extract ALL line items, not just a summary
- confidence_score: rate 0.0-1.0 how confident you are in the overall extraction

Return ONLY valid JSON, no markdown, no explanation:

{
  "invoice_number": "string",
  "vendor_name": "string",
  "vendor_address": "string or null",
  "vendor_email": "string or null",
  "subtotal": 0.00,
  "tax_amount": 0.00,
  "total_amount": 0.00,
  "currency": "USD",
  "line_items": [
    {
      "description": "string",
      "quantity": 1.0,
      "unit_price": 0.00,
      "total": 0.00
    }
  ],
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "payment_terms": "string or null",
  "confidence_score": 0.0
}"""


class InvoiceParser:
    """Extracts structured invoice data from images/PDFs using Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-pro"):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def parse_file(self, file_path: str) -> ExtractionResult:
        """Parse an invoice file (PDF, PNG, JPG) and extract structured data."""
        path = Path(file_path)

        if not path.exists():
            return ExtractionResult(
                success=False,
                error_message=f"File not found: {file_path}"
            )

        console.print(f"[blue]Parsing invoice:[/blue] {path.name}")

        try:
            # Read and encode the file
            file_bytes = path.read_bytes()
            mime_type = self._get_mime_type(path)

            # Call Gemini with the document
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    types.Content(
                        parts=[
                            types.Part.from_bytes(
                                data=file_bytes,
                                mime_type=mime_type,
                            ),
                            types.Part(text=EXTRACTION_PROMPT),
                        ]
                    )
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,  # Low temperature for accuracy
                    max_output_tokens=4096,
                ),
            )

            raw_text = response.text.strip()

            # Clean up response (strip markdown fences if present)
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3].strip()

            # Parse JSON response into our model
            data = json.loads(raw_text)
            invoice = Invoice(
                invoice_number=data.get("invoice_number", "UNKNOWN"),
                vendor_name=data.get("vendor_name", "UNKNOWN"),
                vendor_address=data.get("vendor_address"),
                vendor_email=data.get("vendor_email"),
                subtotal=float(data.get("subtotal", 0)),
                tax_amount=float(data.get("tax_amount", 0)),
                total_amount=float(data.get("total_amount", 0)),
                currency=data.get("currency", "USD"),
                line_items=[
                    LineItem(**item)
                    for item in data.get("line_items", [])
                ],
                invoice_date=self._parse_date(data.get("invoice_date")),
                due_date=self._parse_date(data.get("due_date")),
                payment_terms=data.get("payment_terms"),
                source_file=path.name,
                confidence_score=float(data.get("confidence_score", 0)),
                status=InvoiceStatus.PENDING,
            )

            console.print(
                f"[green]✓ Extracted:[/green] {invoice.vendor_name} - "
                f"${invoice.total_amount:.2f} "
                f"(confidence: {invoice.confidence_score:.0%})"
            )

            return ExtractionResult(
                invoice=invoice,
                raw_response=raw_text,
                success=True,
            )

        except json.JSONDecodeError as e:
            console.print(f"[red]✗ JSON parse error:[/red] {e}")
            return ExtractionResult(
                success=False,
                raw_response=raw_text if 'raw_text' in dir() else "",
                error_message=f"Failed to parse AI response as JSON: {e}",
            )
        except Exception as e:
            console.print(f"[red]✗ Extraction failed:[/red] {e}")
            return ExtractionResult(
                success=False,
                error_message=str(e),
            )

    def _parse_date(self, date_str: str | None) -> date | None:
        """Safely parse a date string."""
        if not date_str or not isinstance(date_str, str):
            return None
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            return None

    def parse_bytes(self, data: bytes, filename: str, mime_type: str) -> ExtractionResult:
        """Parse invoice from raw bytes (e.g., email attachment)."""
        # Write to temp file and parse
        import tempfile
        suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            return self.parse_file(f.name)

    @staticmethod
    def _get_mime_type(path: Path) -> str:
        """Get MIME type for supported file types."""
        suffix = path.suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
        }
        if suffix not in mime_map:
            raise ValueError(f"Unsupported file type: {suffix}")
        return mime_map[suffix]
