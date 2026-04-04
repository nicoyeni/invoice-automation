# Invoice Automation — Dev Context

## Current State (2026-04-02)

### What's working
- FastAPI backend v2.0 (`src/api.py`) on port 8000
- **SQLite persistence** — data survives restarts (`data/invoices.db`)
- Upload flow (file → Gemini AI → anomaly check → dashboard)
- All 77 tests passing, zero warnings
- Full frontend v2 with 3 views, charts, bulk select, notes, audit trail, dark mode

### What's broken / in progress
- "Run Processing Cycle" Gmail OAuth issues (see Error History below) — auth tokens regenerated but not confirmed fixed

---

## Architecture

### Backend
```
src/
  api.py           — FastAPI app, all endpoints
  database.py      — SQLite layer (invoices, notes, audit_log tables)
  models.py        — Pydantic models (Invoice, LineItem, ExtractionResult)
  anomaly_detector.py — 11-check anomaly engine
  invoice_parser.py   — Gemini AI extraction
  gmail_watcher.py    — Gmail OAuth + attachment fetch
  sheets_writer.py    — Google Sheets sync
  drive_watcher.py    — Google Drive watcher
  reminder_sender.py  — Payment reminder emails
  main.py             — run_once() orchestrator
```

### Frontend views (static/index.html — vanilla JS + Tailwind + Chart.js)
| View | What it shows |
|------|--------------|
| Dashboard | Stats, invoice table with sort/filter/search/bulk select |
| Analytics | Monthly spend bar, vendor doughnut, status donut, confidence bar |
| Vendors | Card grid per vendor with totals, status breakdown, confidence |

### API endpoints (v2)
| Method | Path | Description |
|--------|------|-------------|
| GET | /api/invoices | List (filterable by ?status=) |
| POST | /api/upload | Upload invoice file |
| PATCH | /api/invoices/{num}/status | Change status |
| POST | /api/invoices/bulk-status | Bulk status update |
| DELETE | /api/invoices/bulk | Bulk delete |
| GET | /api/invoices/{num}/notes | List notes |
| POST | /api/invoices/{num}/notes | Add note |
| DELETE | /api/invoices/{num}/notes/{id} | Delete note |
| GET | /api/invoices/{num}/audit | Audit trail |
| GET | /api/analytics | Monthly spend, vendor breakdown, status dist, confidence buckets |
| GET | /api/vendors | Per-vendor aggregates |
| GET | /api/events | SSE real-time stream |
| GET | /api/stats | Summary counts |
| POST | /api/process | Trigger Gmail cycle (background) |
| DELETE | /api/invoices/{num} | Delete one |

---

## SQLite Persistence

**Path:** `data/invoices.db` (created automatically)
**Tables:** `invoices`, `notes`, `audit_log`
**Test isolation:** DB operations are skipped when `PYTEST_CURRENT_TEST` env var is set — tests use in-memory `_invoices` list only.

---

## Auth Architecture (Google OAuth)

All three Google services use separate token files to avoid scope collisions:

```
config/token.json          ← base path (never used directly)
config/token_gmail.json    ← GmailWatcher  (gmail.readonly + modify + send)
config/token_sheets.json   ← SheetsWriter  (spreadsheets)
config/token_drive.json    ← DriveWatcher  (drive.readonly) — not yet generated
```

`_service_token()` in `main.py` derives per-service paths from the base config value.

### Re-auth procedure
```bash
cd ~/Downloads/invoice-automation && source venv/bin/activate
rm config/token_gmail.json config/token_sheets.json
python -c "
import yaml, sys; sys.path.insert(0,'src')
from pathlib import Path
cfg = yaml.safe_load(open('config/config.yaml'))
base, creds = cfg['google']['token_file'], cfg['google']['credentials_file']
def st(b,s): p=Path(b); return str(p.parent/f'{p.stem}_{s}{p.suffix}')
from gmail_watcher import GmailWatcher
GmailWatcher(creds, st(base,'gmail'), cfg['gmail']['search_query']).authenticate()
from sheets_writer import SheetsWriter
SheetsWriter(creds, st(base,'sheets'), cfg['sheets']['spreadsheet_id']).authenticate()
print('Done')
"
```

---

## Anomaly Checks (11 total)
1. Duplicate invoice number
2. High amount (>threshold)
3. Zero/negative amount
4. Low AI confidence (<threshold)
5. Line item math mismatch
6. Subtotal + tax ≠ total
7. Unusually high tax rate (>30%)
8. Missing invoice number
9. Missing vendor name
10. Future-dated invoice (>30 days ahead)
11. Suspiciously round number (≥$1000, multiple of $100, no line items)

---

## Error History

| Error | Cause | Fix |
|-------|-------|-----|
| `invalid_grant` | Refresh token expired/revoked | Delete tokens, re-auth via browser |
| `403 insufficientPermissions` | Shared token.json overwritten with wrong scopes | Per-service token files |
| `RESOURCE_EXHAUSTED 429` | gemini-2.5-pro on free-tier key | Config set to paid key |

---

## Config snapshot
```yaml
google:
  credentials_file: config/credentials.json
  token_file: config/token.json   # base — service tokens derived from this
gemini:
  model: gemini-2.5-pro           # paid model
gmail:
  search_query: has:attachment filename:pdf OR filename:png OR filename:jpg subject:(invoice OR bill OR statement)
  mark_as_processed: true
drive:
  enabled: false
sheets:
  spreadsheet_id: 1XhTfeYekbnvx3cbmjX1UoI4mwmrpN41goG0k0nhddes
  sheet_name: Sheet1
reminders:
  enabled: true
```

## Running locally
```bash
cd ~/Downloads/invoice-automation
source venv/bin/activate
uvicorn src.api:app --reload --port 8000
# open http://localhost:8000
```
