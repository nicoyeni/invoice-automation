"""Data models for invoice processing."""

from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Optional
from enum import Enum


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    FLAGGED = "flagged"
    PAID = "paid"
    OVERDUE = "overdue"


class LineItem(BaseModel):
    description: str
    quantity: float = 1.0
    unit_price: float
    total: float

    @property
    def computed_total(self) -> float:
        return self.quantity * self.unit_price


class Invoice(BaseModel):
    """Structured invoice data extracted by AI."""

    # Core fields
    invoice_number: Optional[str] = "UNKNOWN"
    vendor_name: str
    vendor_address: Optional[str] = None
    vendor_email: Optional[str] = None

    # Financial
    subtotal: float
    tax_amount: float = 0.0
    total_amount: float
    currency: str = "USD"
    line_items: list[LineItem] = Field(default_factory=list)

    # Dates
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    payment_terms: Optional[str] = None  # e.g., "Net 30"

    # Metadata
    source_file: str = ""  # original filename
    source_email_id: Optional[str] = None
    extracted_at: datetime = Field(default_factory=datetime.now)
    confidence_score: float = 0.0  # 0-1, how confident the AI was
    status: InvoiceStatus = InvoiceStatus.PENDING
    flags: list[str] = Field(default_factory=list)  # anomaly flags
    categories: list[str] = Field(default_factory=list)  # user-defined tags

    @property
    def is_overdue(self) -> bool:
        if self.due_date and self.status not in (InvoiceStatus.PAID,):
            return date.today() > self.due_date
        return False

    @property
    def days_until_due(self) -> Optional[int]:
        if self.due_date:
            return (self.due_date - date.today()).days
        return None


class ExtractionResult(BaseModel):
    """Result from the AI extraction step."""
    invoice: Optional[Invoice] = None
    raw_response: str = ""
    success: bool = True
    error_message: Optional[str] = None
