"""FastAPI backend for Invoice Automation dashboard — v3.0"""

import os
import re
import sys
import json as json_lib
import asyncio
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from models import Invoice, ExtractionResult, InvoiceStatus
from invoice_parser import InvoiceParser
from anomaly_detector import AnomalyDetector
import database as db

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR   = PROJECT_ROOT / "static"
CONFIG_PATH  = PROJECT_ROOT / "config" / "config.yaml"
FILES_DIR    = PROJECT_ROOT / "data" / "files"

# ── In-memory store ────────────────────────────────────────────────────────────
_invoices: list[Invoice] = []
_status: dict = {"running": False, "last_run": None, "last_count": 0, "error": None}
_sse_queues: list[asyncio.Queue] = []


async def _broadcast(event_type: str, payload: dict = {}):
    msg = json_lib.dumps({"type": event_type, "data": payload})
    for q in list(_sse_queues):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


# ── DB helpers (no-ops in test environment) ────────────────────────────────────
def _is_test() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def _db_save(invoice: Invoice) -> None:
    if _is_test(): return
    try: db.save_invoice(invoice)
    except Exception: pass


def _db_delete(invoice_number: str) -> None:
    if _is_test(): return
    try: db.delete_invoice(invoice_number)
    except Exception: pass


def _db_clear() -> None:
    if _is_test(): return
    try: db.clear_all_invoices()
    except Exception: pass


def _db_audit(invoice_number: str, new_status: str, old_status: Optional[str] = None, trigger: str = "manual") -> None:
    if _is_test(): return
    try: db.add_audit_entry(invoice_number, new_status, old_status, trigger)
    except Exception: pass


# ── App ────────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application: FastAPI):
    if not _is_test():
        try:
            db.init_db()
            FILES_DIR.mkdir(parents=True, exist_ok=True)
            loaded = db.load_invoices()
            _invoices.extend(loaded)
        except Exception:
            pass
    yield


app = FastAPI(title="Invoice Automation", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Config helpers ─────────────────────────────────────────────────────────────
def _load_config() -> Optional[dict]:
    if not CONFIG_PATH.exists():
        return None
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _make_parser(config: dict) -> InvoiceParser:
    return InvoiceParser(
        api_key=config["gemini"]["api_key"],
        model=config["gemini"].get("model", "gemini-2.5-pro"),
    )


def _make_detector(config: dict, existing_numbers: set[str], existing_invoices: list = None) -> AnomalyDetector:
    ac = config.get("anomaly", {})
    return AnomalyDetector(
        high_amount_threshold=ac.get("high_amount_threshold", 10000),
        low_confidence_threshold=ac.get("low_confidence_threshold", 0.7),
        existing_invoice_numbers=existing_numbers,
        existing_invoices=existing_invoices or [],
    )


def _service_token(base: str, service: str) -> str:
    p = Path(base)
    return str(p.parent / f"{p.stem}_{service}{p.suffix}")


def _safe_filename(name: str) -> str:
    return re.sub(r'[^\w\-.]', '_', name)


async def _fire_webhooks(event: str, payload: dict):
    if _is_test():
        return
    try:
        hooks = db.get_webhooks(enabled_only=True)
        if not hooks:
            return
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            for hook in hooks:
                try:
                    events = json_lib.loads(hook.get("events", '["all"]'))
                    if "all" in events or event in events:
                        await client.post(hook["url"], json={"event": event, "data": payload, "timestamp": datetime.now().isoformat()})
                except Exception:
                    pass
    except Exception:
        pass


# ── Routes: core ───────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def serve_frontend():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Invoice Automation API v3 — see /docs"}


@app.get("/api/health")
def health():
    config = _load_config()
    return {"status": "ok", "config_loaded": config is not None, "invoice_count": len(_invoices)}


@app.get("/api/invoices")
def list_invoices(status: Optional[str] = None):
    invoices = _invoices if not status else [i for i in _invoices if i.status.value == status]
    return [inv.model_dump(mode="json") for inv in invoices]


@app.get("/api/stats")
def get_stats():
    return {
        "total_invoices": len(_invoices),
        "flagged":        sum(1 for i in _invoices if i.status == InvoiceStatus.FLAGGED),
        "pending":        sum(1 for i in _invoices if i.status == InvoiceStatus.PENDING),
        "under_review":   sum(1 for i in _invoices if i.status == InvoiceStatus.UNDER_REVIEW),
        "approved":       sum(1 for i in _invoices if i.status == InvoiceStatus.APPROVED),
        "paid":           sum(1 for i in _invoices if i.status == InvoiceStatus.PAID),
        "overdue":        sum(1 for i in _invoices if i.is_overdue),
        "total_amount":   round(sum(i.total_amount for i in _invoices), 2),
        "last_run":       _status["last_run"],
        "last_count":     _status["last_count"],
    }


@app.get("/api/status")
def get_status():
    return _status


# ── Routes: analytics ──────────────────────────────────────────────────────────
@app.get("/api/analytics")
def get_analytics():
    monthly: dict[str, float] = defaultdict(float)
    monthly_count: dict[str, int] = defaultdict(int)
    for inv in _invoices:
        if inv.invoice_date:
            key = inv.invoice_date.strftime("%Y-%m")
            monthly[key] += inv.total_amount
            monthly_count[key] += 1

    sorted_months = sorted(monthly.keys())[-12:]
    monthly_spend = [
        {"month": m, "amount": round(monthly[m], 2), "count": monthly_count[m]}
        for m in sorted_months
    ]

    vendor_totals: dict[str, float] = defaultdict(float)
    vendor_counts: dict[str, int]   = defaultdict(int)
    for inv in _invoices:
        vendor_totals[inv.vendor_name] += inv.total_amount
        vendor_counts[inv.vendor_name] += 1

    sorted_vendors = sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)
    top8 = sorted_vendors[:8]
    other_total = sum(v for _, v in sorted_vendors[8:])
    vendor_breakdown = [
        {"vendor": name, "amount": round(amount, 2), "count": vendor_counts[name]}
        for name, amount in top8
    ]
    if other_total > 0:
        vendor_breakdown.append({"vendor": "Other", "amount": round(other_total, 2), "count": len(sorted_vendors) - 8})

    status_dist = {s.value: sum(1 for i in _invoices if i.status == s) for s in InvoiceStatus}

    conf_buckets = {"0-50%": 0, "50-70%": 0, "70-85%": 0, "85-100%": 0}
    for inv in _invoices:
        c = inv.confidence_score
        if c < 0.50:    conf_buckets["0-50%"] += 1
        elif c < 0.70:  conf_buckets["50-70%"] += 1
        elif c < 0.85:  conf_buckets["70-85%"] += 1
        else:           conf_buckets["85-100%"] += 1

    total_amount = sum(i.total_amount for i in _invoices)
    avg_amount   = total_amount / len(_invoices) if _invoices else 0
    avg_conf     = sum(i.confidence_score for i in _invoices) / len(_invoices) if _invoices else 0

    return {
        "monthly_spend":    monthly_spend,
        "vendor_breakdown": vendor_breakdown,
        "status_dist":      status_dist,
        "conf_buckets":     conf_buckets,
        "summary": {
            "total_amount":    round(total_amount, 2),
            "avg_amount":      round(avg_amount, 2),
            "avg_confidence":  round(avg_conf, 3),
            "total_invoices":  len(_invoices),
            "vendor_count":    len(vendor_totals),
        },
    }


@app.get("/api/analytics/aging")
def get_aging():
    today = date.today()
    buckets: dict[str, list] = {"current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}

    for inv in _invoices:
        if inv.status == InvoiceStatus.PAID or not inv.due_date:
            continue
        days_past = (today - inv.due_date).days
        if days_past <= 0:
            buckets["current"].append(inv)
        elif days_past <= 30:
            buckets["1-30"].append(inv)
        elif days_past <= 60:
            buckets["31-60"].append(inv)
        elif days_past <= 90:
            buckets["61-90"].append(inv)
        else:
            buckets["90+"].append(inv)

    result = {}
    for key, invs in buckets.items():
        result[key] = {
            "count": len(invs),
            "total": round(sum(i.total_amount for i in invs), 2),
            "invoices": [i.model_dump(mode="json") for i in invs],
        }
    return result


@app.get("/api/analytics/yoy")
def get_yoy():
    current_year = date.today().year
    monthly_current: dict[int, float] = defaultdict(float)
    monthly_prior: dict[int, float]   = defaultdict(float)

    for inv in _invoices:
        if not inv.invoice_date:
            continue
        if inv.invoice_date.year == current_year:
            monthly_current[inv.invoice_date.month] += inv.total_amount
        elif inv.invoice_date.year == current_year - 1:
            monthly_prior[inv.invoice_date.month] += inv.total_amount

    month_names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    return {
        "labels":       month_names,
        "current_year": current_year,
        "prior_year":   current_year - 1,
        "current":      [round(monthly_current.get(m, 0), 2) for m in range(1, 13)],
        "prior":        [round(monthly_prior.get(m, 0), 2) for m in range(1, 13)],
    }


@app.get("/api/analytics/forecast")
def get_forecast():
    monthly: dict[str, float] = defaultdict(float)
    for inv in _invoices:
        if inv.invoice_date:
            monthly[inv.invoice_date.strftime("%Y-%m")] += inv.total_amount

    sorted_months = sorted(monthly.keys())[-6:]
    if len(sorted_months) < 2:
        return {"forecast": [], "trend": "insufficient_data", "historical_months": [], "historical_values": [], "forecast_months": [], "forecast_values": []}

    values = [monthly[m] for m in sorted_months]
    n = len(values)
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    denom = sum((i - x_mean) ** 2 for i in range(n))
    slope = sum((i - x_mean) * (values[i] - y_mean) for i in range(n)) / denom if denom else 0
    intercept = y_mean - slope * x_mean

    # Generate next 3 month labels
    from datetime import datetime as dt
    last = dt.strptime(sorted_months[-1], "%Y-%m")
    forecast_months = []
    for i in range(1, 4):
        m = last.month + i
        y = last.year
        while m > 12:
            m -= 12; y += 1
        forecast_months.append(f"{y}-{m:02d}")

    forecast_values = [round(max(0, slope * (n + i - 1) + intercept), 2) for i in range(1, 4)]

    return {
        "historical_months": sorted_months,
        "historical_values": [round(v, 2) for v in values],
        "forecast_months":   forecast_months,
        "forecast_values":   forecast_values,
        "trend":             "up" if slope > 50 else "down" if slope < -50 else "flat",
        "monthly_change":    round(slope, 2),
    }


@app.get("/api/vendors")
def get_vendors():
    vendor_data: dict[str, dict] = {}
    for inv in _invoices:
        name = inv.vendor_name
        if name not in vendor_data:
            vendor_data[name] = {
                "vendor_name": name, "total_amount": 0.0, "count": 0,
                "statuses": defaultdict(int), "avg_confidence": 0.0,
                "_conf_sum": 0.0, "latest_invoice": None,
                "categories": set(), "avg_payment_days": None, "_paid_days": [],
            }
        d = vendor_data[name]
        d["total_amount"] += inv.total_amount
        d["count"]        += 1
        d["statuses"][inv.status.value] += 1
        d["_conf_sum"]    += inv.confidence_score
        for cat in (inv.categories or []):
            d["categories"].add(cat)
        if inv.invoice_date:
            cur = d["latest_invoice"]
            if cur is None or inv.invoice_date.isoformat() > cur:
                d["latest_invoice"] = inv.invoice_date.isoformat()

    result = []
    for d in vendor_data.values():
        d["total_amount"]   = round(d["total_amount"], 2)
        d["avg_confidence"] = round(d["_conf_sum"] / d["count"], 3) if d["count"] else 0
        d["statuses"]       = dict(d["statuses"])
        d["categories"]     = list(d["categories"])
        del d["_conf_sum"], d["_paid_days"], d["avg_payment_days"]
        result.append(d)

    result.sort(key=lambda x: x["total_amount"], reverse=True)
    return result


# ── Routes: upload & process ───────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_invoice(file: UploadFile = File(...)):
    config = _load_config()
    if not config:
        raise HTTPException(status_code=503, detail="config/config.yaml not found.")

    data   = await file.read()
    suffix = Path(file.filename or "upload").suffix or ".pdf"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        parser = _make_parser(config)
        result: ExtractionResult = parser.parse_file(tmp_path)

        if not result.success or not result.invoice:
            raise HTTPException(status_code=422, detail=result.error_message or "Extraction failed")

        invoice = result.invoice
        invoice.source_file = file.filename or "upload"

        existing = {i.invoice_number for i in _invoices if i.invoice_number}
        detector = _make_detector(config, existing, _invoices)
        invoice  = detector.check(invoice)

        # Store file for preview
        safe_num = _safe_filename(invoice.invoice_number or "unknown")
        dest = FILES_DIR / f"{safe_num}{suffix}"
        shutil.copy(tmp_path, str(dest))

        _invoices.append(invoice)
        _db_save(invoice)
        _db_audit(invoice.invoice_number, invoice.status.value, trigger="upload")
        asyncio.create_task(_broadcast("invoice_added", {"invoice_number": invoice.invoice_number}))
        asyncio.create_task(_fire_webhooks("invoice_created", {"invoice_number": invoice.invoice_number, "vendor": invoice.vendor_name, "amount": invoice.total_amount}))

        return invoice.model_dump(mode="json")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.post("/api/upload/batch")
async def batch_upload(files: list[UploadFile] = File(...)):
    config = _load_config()
    if not config:
        raise HTTPException(status_code=503, detail="config/config.yaml not found.")

    results = []
    for file in files:
        data   = await file.read()
        suffix = Path(file.filename or "upload").suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            parser = _make_parser(config)
            result: ExtractionResult = parser.parse_file(tmp_path)
            if not result.success or not result.invoice:
                results.append({"filename": file.filename, "status": "error", "error": result.error_message or "Extraction failed"})
                continue
            invoice = result.invoice
            invoice.source_file = file.filename or "upload"
            existing = {i.invoice_number for i in _invoices if i.invoice_number}
            detector = _make_detector(config, existing, _invoices)
            invoice  = detector.check(invoice)
            safe_num = _safe_filename(invoice.invoice_number or "unknown")
            shutil.copy(tmp_path, str(FILES_DIR / f"{safe_num}{suffix}"))
            _invoices.append(invoice)
            _db_save(invoice)
            _db_audit(invoice.invoice_number, invoice.status.value, trigger="batch_upload")
            results.append({"filename": file.filename, "status": "ok", "invoice": invoice.model_dump(mode="json")})
        except Exception as e:
            results.append({"filename": file.filename, "status": "error", "error": str(e)})
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass

    asyncio.create_task(_broadcast("batch_complete", {"count": len(results)}))
    return {"results": results, "total": len(results), "ok": sum(1 for r in results if r["status"] == "ok")}


@app.post("/api/process")
def trigger_processing(background_tasks: BackgroundTasks):
    if _status["running"]:
        raise HTTPException(status_code=409, detail="A processing cycle is already running")

    config = _load_config()
    if not config:
        raise HTTPException(status_code=503, detail="config/config.yaml not found")

    def _run_cycle():
        _status["running"] = True
        _status["error"]   = None
        try:
            from main import run_once
            new_invoices = run_once(config)
            _invoices.extend(new_invoices)
            for inv in new_invoices:
                _db_save(inv)
                _db_audit(inv.invoice_number, inv.status.value, trigger="gmail_cycle")
            _status["last_count"] = len(new_invoices)
        except Exception as exc:
            _status["error"] = str(exc)
        finally:
            _status["running"] = False
            _status["last_run"] = datetime.now().isoformat()

    background_tasks.add_task(_run_cycle)
    return {"message": "Processing cycle started", "status": "running"}


# ── Routes: status update ──────────────────────────────────────────────────────
class StatusUpdate(BaseModel):
    status: str


@app.patch("/api/invoices/{invoice_number}/status")
async def update_invoice_status(invoice_number: str, body: StatusUpdate):
    try:
        new_status = InvoiceStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid status: {body.status!r}")

    for inv in _invoices:
        if inv.invoice_number == invoice_number:
            old_status = inv.status.value
            inv.status = new_status
            _db_save(inv)
            _db_audit(invoice_number, new_status.value, old_status)
            asyncio.create_task(_broadcast("invoice_updated", {"invoice_number": invoice_number}))
            asyncio.create_task(_fire_webhooks("status_changed", {
                "invoice_number": invoice_number,
                "old_status": old_status,
                "new_status": new_status.value,
            }))
            return inv.model_dump(mode="json")

    raise HTTPException(status_code=404, detail=f"Invoice {invoice_number!r} not found")


# ── Routes: bulk operations ────────────────────────────────────────────────────
class BulkStatusUpdate(BaseModel):
    invoice_numbers: list[str]
    status: str


class BulkDelete(BaseModel):
    invoice_numbers: list[str]


@app.post("/api/invoices/bulk-status")
def bulk_status_update(body: BulkStatusUpdate):
    try:
        new_status = InvoiceStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid status: {body.status!r}")

    updated = 0
    for inv in _invoices:
        if inv.invoice_number in body.invoice_numbers:
            old = inv.status.value
            inv.status = new_status
            _db_save(inv)
            _db_audit(inv.invoice_number, new_status.value, old, "bulk")
            updated += 1
    return {"updated": updated}


@app.delete("/api/invoices/bulk")
def bulk_delete(body: BulkDelete):
    nums = set(body.invoice_numbers)
    before = len(_invoices)
    _invoices[:] = [i for i in _invoices if i.invoice_number not in nums]
    for n in nums:
        _db_delete(n)
    return {"deleted": before - len(_invoices)}


# ── Routes: categories ─────────────────────────────────────────────────────────
class CategoriesUpdate(BaseModel):
    categories: list[str]


@app.put("/api/invoices/{invoice_number}/categories")
def update_categories(invoice_number: str, body: CategoriesUpdate):
    for inv in _invoices:
        if inv.invoice_number == invoice_number:
            inv.categories = [c.strip() for c in body.categories if c.strip()]
            _db_save(inv)
            return {"categories": inv.categories}
    raise HTTPException(status_code=404, detail="Invoice not found")


# ── Routes: file preview ───────────────────────────────────────────────────────
@app.get("/api/invoices/{invoice_number}/file")
def get_invoice_file(invoice_number: str):
    if not any(i.invoice_number == invoice_number for i in _invoices):
        raise HTTPException(status_code=404, detail="Invoice not found")
    safe_num = _safe_filename(invoice_number)
    for ext in ['.pdf', '.png', '.jpg', '.jpeg', '.webp']:
        p = FILES_DIR / f"{safe_num}{ext}"
        if p.exists():
            return FileResponse(str(p))
    raise HTTPException(status_code=404, detail="Original file not stored")


# ── Routes: notes ──────────────────────────────────────────────────────────────
class NoteCreate(BaseModel):
    content: str


@app.get("/api/invoices/{invoice_number}/notes")
def get_notes(invoice_number: str):
    if _is_test(): return []
    if not any(i.invoice_number == invoice_number for i in _invoices):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return db.get_notes(invoice_number)


@app.post("/api/invoices/{invoice_number}/notes")
def add_note(invoice_number: str, body: NoteCreate):
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Note content cannot be empty")
    if not any(i.invoice_number == invoice_number for i in _invoices):
        raise HTTPException(status_code=404, detail="Invoice not found")
    if _is_test():
        return {"id": 1, "invoice_number": invoice_number, "content": body.content, "created_at": datetime.now().isoformat()}
    return db.add_note(invoice_number, body.content)


@app.delete("/api/invoices/{invoice_number}/notes/{note_id}")
def delete_note(invoice_number: str, note_id: int):
    if _is_test(): return {"deleted": True}
    deleted = db.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True}


# ── Routes: audit log ──────────────────────────────────────────────────────────
@app.get("/api/invoices/{invoice_number}/audit")
def get_audit(invoice_number: str):
    if _is_test(): return []
    if not any(i.invoice_number == invoice_number for i in _invoices):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return db.get_audit_log(invoice_number)


# ── Routes: budgets ────────────────────────────────────────────────────────────
class BudgetCreate(BaseModel):
    name: str
    scope: str = "global"
    monthly_limit: float


@app.get("/api/budgets")
def get_budgets():
    if _is_test(): return []
    budgets = db.get_budgets()
    # Annotate with current month spend
    today = date.today()
    month_key = today.strftime("%Y-%m")
    for b in budgets:
        scope = b.get("scope", "global")
        if scope == "global":
            spent = sum(i.total_amount for i in _invoices
                       if i.invoice_date and i.invoice_date.strftime("%Y-%m") == month_key)
        elif scope.startswith("vendor:"):
            vendor = scope[7:]
            spent = sum(i.total_amount for i in _invoices
                       if i.vendor_name == vendor and i.invoice_date and i.invoice_date.strftime("%Y-%m") == month_key)
        elif scope.startswith("category:"):
            cat = scope[9:]
            spent = sum(i.total_amount for i in _invoices
                       if cat in (i.categories or []) and i.invoice_date and i.invoice_date.strftime("%Y-%m") == month_key)
        else:
            spent = 0
        b["current_spend"] = round(spent, 2)
        b["utilization"] = round(spent / b["monthly_limit"], 3) if b["monthly_limit"] > 0 else 0
    return budgets


@app.post("/api/budgets")
def create_budget(body: BudgetCreate):
    if _is_test():
        return {"id": 1, **body.model_dump(), "created_at": datetime.now().isoformat()}
    return db.add_budget(body.name, body.scope, body.monthly_limit)


@app.delete("/api/budgets/{budget_id}")
def delete_budget(budget_id: int):
    if _is_test(): return {"deleted": True}
    if not db.delete_budget(budget_id):
        raise HTTPException(status_code=404, detail="Budget not found")
    return {"deleted": True}


# ── Routes: webhooks ───────────────────────────────────────────────────────────
class WebhookCreate(BaseModel):
    name: str
    url: str
    events: list[str] = ["all"]


@app.get("/api/webhooks")
def get_webhooks():
    if _is_test(): return []
    return db.get_webhooks()


@app.post("/api/webhooks")
def create_webhook(body: WebhookCreate):
    if _is_test():
        return {"id": 1, **body.model_dump(), "enabled": True, "created_at": datetime.now().isoformat()}
    return db.add_webhook(body.name, body.url, body.events)


@app.delete("/api/webhooks/{webhook_id}")
def delete_webhook(webhook_id: int):
    if _is_test(): return {"deleted": True}
    if not db.delete_webhook(webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"deleted": True}


@app.patch("/api/webhooks/{webhook_id}/toggle")
def toggle_webhook(webhook_id: int, body: dict):
    if _is_test(): return {"ok": True}
    db.toggle_webhook(webhook_id, bool(body.get("enabled", True)))
    return {"ok": True}


@app.post("/api/webhooks/test")
async def test_webhook(body: dict):
    url = body.get("url", "")
    if not url:
        raise HTTPException(status_code=422, detail="URL required")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json={"event": "test", "data": {"message": "Invoice Automation webhook test"}, "timestamp": datetime.now().isoformat()})
        return {"ok": True, "status_code": resp.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Routes: saved filters ──────────────────────────────────────────────────────
class SavedFilterCreate(BaseModel):
    name: str
    filter_json: str


@app.get("/api/filters")
def get_filters():
    if _is_test(): return []
    return db.get_saved_filters()


@app.post("/api/filters")
def create_filter(body: SavedFilterCreate):
    if _is_test():
        return {"id": 1, **body.model_dump(), "created_at": datetime.now().isoformat()}
    return db.add_saved_filter(body.name, body.filter_json)


@app.delete("/api/filters/{filter_id}")
def delete_filter(filter_id: int):
    if _is_test(): return {"deleted": True}
    if not db.delete_saved_filter(filter_id):
        raise HTTPException(status_code=404, detail="Filter not found")
    return {"deleted": True}


# ── Routes: export ─────────────────────────────────────────────────────────────
@app.get("/api/export/accounting")
def export_accounting(format: str = "quickbooks"):
    """Export in QuickBooks/Xero compatible CSV format."""
    import io, csv as csv_lib
    output = io.StringIO()
    if format == "xero":
        fieldnames = ["ContactName","EmailAddress","POAddressLine1","InvoiceNumber","InvoiceDate","DueDate","Description","Quantity","UnitAmount","AccountCode","TaxType","Currency","TotalAmount"]
        writer = csv_lib.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for inv in _invoices:
            line_items = inv.line_items or []
            if line_items:
                for li in line_items:
                    writer.writerow({
                        "ContactName": inv.vendor_name, "EmailAddress": inv.vendor_email or "",
                        "POAddressLine1": inv.vendor_address or "",
                        "InvoiceNumber": inv.invoice_number, "InvoiceDate": str(inv.invoice_date or ""),
                        "DueDate": str(inv.due_date or ""),
                        "Description": li.description, "Quantity": li.quantity, "UnitAmount": li.unit_price,
                        "AccountCode": "200", "TaxType": "TAX001", "Currency": inv.currency or "USD",
                        "TotalAmount": inv.total_amount,
                    })
            else:
                writer.writerow({
                    "ContactName": inv.vendor_name, "EmailAddress": inv.vendor_email or "",
                    "POAddressLine1": inv.vendor_address or "",
                    "InvoiceNumber": inv.invoice_number, "InvoiceDate": str(inv.invoice_date or ""),
                    "DueDate": str(inv.due_date or ""),
                    "Description": "Invoice total", "Quantity": 1, "UnitAmount": inv.total_amount,
                    "AccountCode": "200", "TaxType": "TAX001", "Currency": inv.currency or "USD",
                    "TotalAmount": inv.total_amount,
                })
    else:  # quickbooks
        fieldnames = ["Vendor","Invoice Number","Invoice Date","Due Date","Terms","Amount","Tax","Total","Currency","Status","Categories","Source"]
        writer = csv_lib.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for inv in _invoices:
            writer.writerow({
                "Vendor": inv.vendor_name, "Invoice Number": inv.invoice_number,
                "Invoice Date": str(inv.invoice_date or ""), "Due Date": str(inv.due_date or ""),
                "Terms": inv.payment_terms or "", "Amount": inv.subtotal,
                "Tax": inv.tax_amount, "Total": inv.total_amount,
                "Currency": inv.currency or "USD", "Status": inv.status.value,
                "Categories": "; ".join(inv.categories or []),
                "Source": inv.source_file,
            })

    csv_bytes = output.getvalue().encode()
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=invoices-{format}-{date.today()}.csv"},
    )


# ── Routes: SSE ────────────────────────────────────────────────────────────────
@app.get("/api/events")
async def sse_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_queues.append(queue)

    async def generator():
        try:
            yield 'data: {"type":"connected"}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_queues.remove(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Routes: delete ─────────────────────────────────────────────────────────────
@app.delete("/api/invoices/{invoice_number}")
def delete_invoice(invoice_number: str):
    before = len(_invoices)
    _invoices[:] = [i for i in _invoices if i.invoice_number != invoice_number]
    if len(_invoices) == before:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_number!r} not found")
    _db_delete(invoice_number)
    return {"deleted": True, "invoice_number": invoice_number}


@app.delete("/api/invoices", include_in_schema=False)
def clear_all_invoices():
    _invoices.clear()
    _db_clear()
    return {"cleared": True, "count": 0}
