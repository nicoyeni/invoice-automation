#!/bin/bash
set -e

mkdir -p config data/files logs

# ── Write config.yaml from env vars ──────────────────────────────────────────
cat > config/config.yaml << YAML
google:
  credentials_file: "config/credentials.json"
  token_file: "config/token.json"

gemini:
  api_key: "${GEMINI_API_KEY}"
  model: "${GEMINI_MODEL:-gemini-2.5-pro}"

gmail:
  search_query: "${GMAIL_SEARCH_QUERY:-has:attachment filename:pdf OR filename:png OR filename:jpg subject:(invoice OR bill OR statement)}"
  mark_as_processed: true

drive:
  enabled: false

sheets:
  spreadsheet_id: "${SHEETS_SPREADSHEET_ID:-}"
  sheet_name: "${SHEETS_NAME:-Sheet1}"

anomaly:
  high_amount_threshold: ${HIGH_AMOUNT_THRESHOLD:-10000}
  low_confidence_threshold: ${LOW_CONFIDENCE_THRESHOLD:-0.7}

reminders:
  enabled: false
YAML

# ── Decode Google credentials from base64 env vars ────────────────────────────
if [ -n "$GOOGLE_CREDENTIALS_B64" ]; then
  echo "$GOOGLE_CREDENTIALS_B64" | base64 -d > config/credentials.json
  echo "✓ credentials.json written"
fi

if [ -n "$GMAIL_TOKEN_B64" ]; then
  echo "$GMAIL_TOKEN_B64" | base64 -d > config/token_gmail.json
  echo "✓ token_gmail.json written"
fi

if [ -n "$SHEETS_TOKEN_B64" ]; then
  echo "$SHEETS_TOKEN_B64" | base64 -d > config/token_sheets.json
  echo "✓ token_sheets.json written"
fi

# ── Start the API server ───────────────────────────────────────────────────────
echo "Starting InvoiceAI on port ${PORT:-8000}…"
exec uvicorn src.api:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info
