# Invoice Processing Automation

AI-powered invoice processing pipeline for small businesses. Watches Gmail/Google Drive for incoming invoices, extracts structured data using Gemini/Claude, and pushes results to Google Sheets — replacing manual data entry.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Gmail Inbox /  │────▶│  Invoice Parser  │────▶│  Google Sheets  │
│  Google Drive   │     │  (Gemini 3 Pro)  │     │  (Structured)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │                         │
                               ▼                         ▼
                        ┌──────────────┐         ┌──────────────────┐
                        │  Anomaly     │         │  Payment Reminder│
                        │  Flagging    │         │  Scheduler       │
                        └──────────────┘         └──────────────────┘
```

## How It Works

1. **Watch** — Polls Gmail for new emails with PDF/image attachments, or watches a Google Drive folder
2. **Extract** — Sends invoice images/PDFs to Gemini 3 Pro for structured data extraction
3. **Validate** — Checks extracted data for anomalies (duplicate invoices, unusual amounts, missing fields)
4. **Store** — Writes structured invoice data to a Google Sheet (or QuickBooks via API)
5. **Act** — Sends payment reminders, flags overdue invoices, generates weekly summaries

## Quick Start

### Prerequisites
- Python 3.11+
- Google Cloud project with Gmail API + Sheets API + Drive API enabled
- Google service account credentials (or OAuth2 for personal Gmail)
- Gemini API key (free tier works for testing)

### Installation

```bash
# Clone and setup
cd invoice-automation
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Configure
cp config/config.example.yaml config/config.yaml
# Edit config.yaml with your API keys and settings

# Run
python src/main.py
```

### Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (e.g., "invoice-automation")
3. Enable APIs: Gmail API, Google Sheets API, Google Drive API
4. Create OAuth2 credentials (Desktop app) → download as `credentials.json`
5. Place `credentials.json` in `config/`

### Gemini API Setup

1. Go to [Google AI Studio](https://aistudio.google.com)
2. Get an API key
3. Add to `config/config.yaml`

## Project Structure

```
invoice-automation/
├── src/
│   ├── main.py              # Entry point & orchestrator
│   ├── gmail_watcher.py     # Gmail polling & attachment extraction
│   ├── drive_watcher.py     # Google Drive folder monitoring
│   ├── invoice_parser.py    # Gemini-powered invoice data extraction
│   ├── sheets_writer.py     # Google Sheets output
│   ├── anomaly_detector.py  # Duplicate/anomaly checking
│   ├── reminder_sender.py   # Payment reminder emails
│   └── models.py            # Data models (Invoice, LineItem, etc.)
├── config/
│   ├── config.example.yaml  # Template config
│   └── config.yaml          # Your local config (gitignored)
├── templates/
│   └── reminder_email.html  # Payment reminder template
├── tests/
│   └── test_parser.py       # Test with sample invoices
├── docs/
│   └── SELLING_GUIDE.md     # How to sell this to clients
├── requirements.txt
├── Dockerfile               # For 24/7 deployment
├── docker-compose.yml
└── README.md
```

## Deployment

For production (running 24/7 for a client):

```bash
docker-compose up -d
```

Or deploy to Google Cloud Run for serverless execution with a cron trigger.

## Cost Breakdown

| Component | Monthly Cost |
|-----------|-------------|
| Gemini API (free tier) | $0 |
| Gemini API (paid, ~500 invoices/mo) | ~$5-15 |
| Google Cloud Run | ~$5-10 |
| Google Workspace (client already has) | $0 |
| **Total per client** | **~$10-25** |

Charge clients $200-500/month → 90%+ margins.

## License

MIT
