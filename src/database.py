"""SQLite persistence layer — invoices, notes, audit log."""

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

# Avoid importing models at module level to stay lightweight
_DB_PATH: Optional[Path] = None


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            _DB_PATH = Path("/tmp/invoice_automation_test.db")
        else:
            root = Path(__file__).parent.parent
            (root / "data").mkdir(exist_ok=True)
            _DB_PATH = root / "data" / "invoices.db"
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS invoices (
                invoice_number TEXT PRIMARY KEY,
                data           TEXT    NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'pending',
                created_at     TEXT    NOT NULL,
                updated_at     TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT    NOT NULL,
                content        TEXT    NOT NULL,
                created_at     TEXT    NOT NULL,
                FOREIGN KEY (invoice_number) REFERENCES invoices(invoice_number)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT    NOT NULL,
                old_status     TEXT,
                new_status     TEXT    NOT NULL,
                trigger        TEXT    NOT NULL DEFAULT 'manual',
                created_at     TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS budgets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                scope        TEXT    NOT NULL DEFAULT 'global',
                monthly_limit REAL   NOT NULL,
                created_at   TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhooks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                url        TEXT    NOT NULL,
                events     TEXT    NOT NULL DEFAULT '["all"]',
                enabled    INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS saved_filters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                filter_json TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );
        """)


def _now() -> str:
    return datetime.now().isoformat()


# ── Invoices ───────────────────────────────────────────────────────────────────

def save_invoice(invoice) -> None:
    """Upsert an invoice (accepts any object with .model_dump())."""
    data = json.dumps(invoice.model_dump(mode="json"))
    now = _now()
    with _conn() as db:
        db.execute("""
            INSERT INTO invoices (invoice_number, data, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(invoice_number) DO UPDATE SET
                data       = excluded.data,
                status     = excluded.status,
                updated_at = excluded.updated_at
        """, (invoice.invoice_number, data, invoice.status.value, now, now))


def load_invoices() -> list:
    """Load all invoices from DB as Invoice objects."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from models import Invoice

    with _conn() as db:
        rows = db.execute(
            "SELECT data FROM invoices ORDER BY created_at ASC"
        ).fetchall()

    invoices = []
    for row in rows:
        try:
            invoices.append(Invoice(**json.loads(row["data"])))
        except Exception:
            pass
    return invoices


def delete_invoice(invoice_number: str) -> None:
    with _conn() as db:
        db.execute("DELETE FROM invoices WHERE invoice_number = ?", (invoice_number,))


def clear_all_invoices() -> None:
    with _conn() as db:
        db.execute("DELETE FROM invoices")
        db.execute("DELETE FROM notes")
        db.execute("DELETE FROM audit_log")


# ── Notes ──────────────────────────────────────────────────────────────────────

def get_notes(invoice_number: str) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT id, content, created_at FROM notes "
            "WHERE invoice_number = ? ORDER BY created_at ASC",
            (invoice_number,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_note(invoice_number: str, content: str) -> dict:
    now = _now()
    with _conn() as db:
        cursor = db.execute(
            "INSERT INTO notes (invoice_number, content, created_at) VALUES (?, ?, ?)",
            (invoice_number, content.strip(), now),
        )
        return {
            "id": cursor.lastrowid,
            "invoice_number": invoice_number,
            "content": content.strip(),
            "created_at": now,
        }


def delete_note(note_id: int) -> bool:
    with _conn() as db:
        cursor = db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return cursor.rowcount > 0


# ── Audit log ──────────────────────────────────────────────────────────────────

def add_audit_entry(
    invoice_number: str,
    new_status: str,
    old_status: Optional[str] = None,
    trigger: str = "manual",
) -> None:
    with _conn() as db:
        db.execute(
            "INSERT INTO audit_log "
            "(invoice_number, old_status, new_status, trigger, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (invoice_number, old_status, new_status, trigger, _now()),
        )


def get_audit_log(invoice_number: str) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT id, old_status, new_status, trigger, created_at FROM audit_log "
            "WHERE invoice_number = ? ORDER BY created_at ASC",
            (invoice_number,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Budgets ────────────────────────────────────────────────────────────────────

def get_budgets() -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT id, name, scope, monthly_limit, created_at FROM budgets ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def add_budget(name: str, scope: str, monthly_limit: float) -> dict:
    now = _now()
    with _conn() as db:
        cursor = db.execute(
            "INSERT INTO budgets (name, scope, monthly_limit, created_at) VALUES (?, ?, ?, ?)",
            (name.strip(), scope.strip(), monthly_limit, now),
        )
        return {"id": cursor.lastrowid, "name": name, "scope": scope, "monthly_limit": monthly_limit, "created_at": now}


def delete_budget(budget_id: int) -> bool:
    with _conn() as db:
        cursor = db.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
    return cursor.rowcount > 0


# ── Webhooks ───────────────────────────────────────────────────────────────────

def get_webhooks(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT id, name, url, events, enabled, created_at FROM webhooks"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY created_at ASC"
    with _conn() as db:
        rows = db.execute(sql).fetchall()
    return [dict(r) for r in rows]


def add_webhook(name: str, url: str, events: list[str]) -> dict:
    import json as _json
    now = _now()
    events_str = _json.dumps(events)
    with _conn() as db:
        cursor = db.execute(
            "INSERT INTO webhooks (name, url, events, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
            (name.strip(), url.strip(), events_str, now),
        )
        return {"id": cursor.lastrowid, "name": name, "url": url, "events": events, "enabled": True, "created_at": now}


def delete_webhook(webhook_id: int) -> bool:
    with _conn() as db:
        cursor = db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
    return cursor.rowcount > 0


def toggle_webhook(webhook_id: int, enabled: bool) -> bool:
    with _conn() as db:
        cursor = db.execute("UPDATE webhooks SET enabled = ? WHERE id = ?", (1 if enabled else 0, webhook_id))
    return cursor.rowcount > 0


# ── Saved filters ──────────────────────────────────────────────────────────────

def get_saved_filters() -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT id, name, filter_json, created_at FROM saved_filters ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def add_saved_filter(name: str, filter_json: str) -> dict:
    now = _now()
    with _conn() as db:
        cursor = db.execute(
            "INSERT INTO saved_filters (name, filter_json, created_at) VALUES (?, ?, ?)",
            (name.strip(), filter_json, now),
        )
        return {"id": cursor.lastrowid, "name": name, "filter_json": filter_json, "created_at": now}


def delete_saved_filter(filter_id: int) -> bool:
    with _conn() as db:
        cursor = db.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))
    return cursor.rowcount > 0
