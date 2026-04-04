"""Unit tests for invoice_parser.py — mocks the Gemini API."""

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from models import Invoice, InvoiceStatus
from invoice_parser import InvoiceParser, EXTRACTION_PROMPT


FAKE_GEMINI_RESPONSE = json.dumps({
    "invoice_number": "INV-2026-42",
    "vendor_name": "Mock Vendor LLC",
    "vendor_address": "42 Test Ave, Pytest City",
    "vendor_email": "billing@mockvendor.com",
    "subtotal": 1000.0,
    "tax_amount": 100.0,
    "total_amount": 1100.0,
    "currency": "USD",
    "line_items": [
        {"description": "Consulting", "quantity": 10.0, "unit_price": 100.0, "total": 1000.0}
    ],
    "invoice_date": "2026-01-15",
    "due_date": "2026-02-14",
    "payment_terms": "Net 30",
    "confidence_score": 0.92,
})


def make_parser() -> InvoiceParser:
    """Return a parser with a mocked Gemini client."""
    with patch("invoice_parser.genai.Client"):
        parser = InvoiceParser(api_key="fake-key", model="gemini-2.5-pro")
    return parser


def mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


class TestInvoiceParserMimeType:
    def test_pdf(self):
        assert InvoiceParser._get_mime_type(Path("invoice.pdf")) == "application/pdf"

    def test_png(self):
        assert InvoiceParser._get_mime_type(Path("scan.png")) == "image/png"

    def test_jpg(self):
        assert InvoiceParser._get_mime_type(Path("photo.jpg")) == "image/jpeg"

    def test_jpeg(self):
        assert InvoiceParser._get_mime_type(Path("photo.jpeg")) == "image/jpeg"

    def test_webp(self):
        assert InvoiceParser._get_mime_type(Path("doc.webp")) == "image/webp"

    def test_tiff(self):
        assert InvoiceParser._get_mime_type(Path("scan.tiff")) == "image/tiff"

    def test_tif(self):
        assert InvoiceParser._get_mime_type(Path("scan.tif")) == "image/tiff"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            InvoiceParser._get_mime_type(Path("doc.docx"))


class TestInvoiceParserDateParsing:
    def setup_method(self):
        with patch("invoice_parser.genai.Client"):
            self.parser = InvoiceParser(api_key="fake-key")

    def test_valid_iso_date(self):
        assert self.parser._parse_date("2026-03-15") == date(2026, 3, 15)

    def test_none_returns_none(self):
        assert self.parser._parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert self.parser._parse_date("") is None

    def test_invalid_date_returns_none(self):
        assert self.parser._parse_date("not-a-date") is None

    def test_non_string_returns_none(self):
        assert self.parser._parse_date(20260315) is None  # type: ignore


class TestInvoiceParserParseFile:
    def setup_method(self):
        with patch("invoice_parser.genai.Client"):
            self.parser = InvoiceParser(api_key="fake-key")

    def _set_response(self, text: str):
        self.parser.client.models.generate_content.return_value = mock_response(text)

    def test_successful_extraction(self):
        self._set_response(FAKE_GEMINI_RESPONSE)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            tmp_path = f.name

        result = self.parser.parse_file(tmp_path)

        assert result.success is True
        assert result.invoice is not None
        inv = result.invoice
        assert inv.invoice_number == "INV-2026-42"
        assert inv.vendor_name == "Mock Vendor LLC"
        assert inv.total_amount == 1100.0
        assert inv.confidence_score == 0.92
        assert inv.invoice_date == date(2026, 1, 15)
        assert len(inv.line_items) == 1

    def test_strips_markdown_fences(self):
        wrapped = "```json\n" + FAKE_GEMINI_RESPONSE + "\n```"
        self._set_response(wrapped)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            tmp_path = f.name

        result = self.parser.parse_file(tmp_path)
        assert result.success is True
        assert result.invoice.vendor_name == "Mock Vendor LLC"

    def test_file_not_found(self):
        result = self.parser.parse_file("/nonexistent/path/invoice.pdf")
        assert result.success is False
        assert "not found" in result.error_message.lower()

    def test_invalid_json_response(self):
        self._set_response("this is not json at all")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            tmp_path = f.name

        result = self.parser.parse_file(tmp_path)
        assert result.success is False
        assert "JSON" in result.error_message or "json" in result.error_message.lower()

    def test_gemini_exception_handled(self):
        self.parser.client.models.generate_content.side_effect = Exception("API quota exceeded")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            tmp_path = f.name

        result = self.parser.parse_file(tmp_path)
        assert result.success is False
        assert "quota" in result.error_message.lower()

    def test_invoice_status_defaults_to_pending(self):
        self._set_response(FAKE_GEMINI_RESPONSE)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            tmp_path = f.name

        result = self.parser.parse_file(tmp_path)
        assert result.invoice.status == InvoiceStatus.PENDING


class TestInvoiceParserParseBytes:
    def test_parse_bytes_delegates_to_parse_file(self):
        with patch("invoice_parser.genai.Client"):
            parser = InvoiceParser(api_key="fake-key")
        parser.client.models.generate_content.return_value = mock_response(FAKE_GEMINI_RESPONSE)

        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = parser.parse_bytes(data=data, filename="test.png", mime_type="image/png")

        assert result.success is True
        assert result.invoice.vendor_name == "Mock Vendor LLC"
