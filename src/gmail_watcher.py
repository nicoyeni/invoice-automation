"""Gmail watcher - polls for new invoice emails and extracts attachments."""

import base64
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from rich.console import Console

console = Console()


class GmailWatcher:
    """Watches Gmail for new invoice emails and extracts PDF/image attachments."""

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    def __init__(
        self,
        credentials_file: str,
        token_file: str,
        search_query: str,
        processed_label: str = "AutoProcessed",
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.search_query = search_query
        self.processed_label = processed_label
        self.service = None
        self._processed_label_id = None

    def authenticate(self) -> None:
        """Authenticate with Gmail API using OAuth2."""
        creds = None
        token_path = Path(self.token_file)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    # Refresh token revoked or expired — force re-auth
                    console.print("[yellow]⚠ Token refresh failed, re-authenticating...[/yellow]")
                    creds = None
            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        console.print("[green]✓ Gmail authenticated[/green]")

    def fetch_new_invoices(self) -> list[dict]:
        """
        Fetch unprocessed emails matching the search query.
        Returns list of dicts with: email_id, subject, sender, attachments[]
        """
        if not self.service:
            self.authenticate()

        # Search for matching emails that haven't been processed yet
        query = f"{self.search_query} -label:{self.processed_label}"
        console.print(f"[blue]Searching Gmail:[/blue] {query}")

        results = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=20)
            .execute()
        )

        messages = results.get("messages", [])
        if not messages:
            console.print("[dim]No new invoices found[/dim]")
            return []

        console.print(f"[blue]Found {len(messages)} new email(s)[/blue]")

        invoice_emails = []
        for msg_ref in messages:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="full")
                .execute()
            )

            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }

            attachments = self._extract_attachments(msg)
            if attachments:
                invoice_emails.append(
                    {
                        "email_id": msg_ref["id"],
                        "subject": headers.get("Subject", ""),
                        "sender": headers.get("From", ""),
                        "date": headers.get("Date", ""),
                        "attachments": attachments,
                    }
                )

        console.print(
            f"[green]✓ {len(invoice_emails)} email(s) with attachments[/green]"
        )
        return invoice_emails

    def _extract_attachments(self, message: dict) -> list[dict]:
        """Extract PDF/image attachments from an email message."""
        attachments = []
        supported_mimes = {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/tiff",
        }

        def walk_parts(parts):
            for part in parts:
                mime = part.get("mimeType", "")
                filename = part.get("filename", "")

                if mime in supported_mimes and filename:
                    att_id = part.get("body", {}).get("attachmentId")
                    if att_id:
                        # Fetch the actual attachment data
                        att = (
                            self.service.users()
                            .messages()
                            .attachments()
                            .get(
                                userId="me",
                                messageId=message["id"],
                                id=att_id,
                            )
                            .execute()
                        )
                        data = base64.urlsafe_b64decode(att["data"])
                        attachments.append(
                            {
                                "filename": filename,
                                "mime_type": mime,
                                "data": data,
                            }
                        )

                # Recurse into nested parts
                if "parts" in part:
                    walk_parts(part["parts"])

        payload = message.get("payload", {})
        if "parts" in payload:
            walk_parts(payload["parts"])

        return attachments

    def mark_as_processed(self, email_id: str) -> None:
        """Add the 'AutoProcessed' label to an email so we don't re-process it."""
        if not self._processed_label_id:
            self._processed_label_id = self._get_or_create_label(
                self.processed_label
            )

        self.service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": [self._processed_label_id]},
        ).execute()

    def _get_or_create_label(self, label_name: str) -> str:
        """Get or create a Gmail label, return its ID."""
        labels = (
            self.service.users().labels().list(userId="me").execute()
        )
        for label in labels.get("labels", []):
            if label["name"] == label_name:
                return label["id"]

        # Create it
        new_label = (
            self.service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        return new_label["id"]

    def send_email(self, to: str, subject: str, html_body: str) -> None:
        """Send an email (used for payment reminders)."""
        import email.mime.text as mime_text
        import email.mime.multipart as mime_multi

        message = mime_multi.MIMEMultipart("alternative")
        message["to"] = to
        message["subject"] = subject
        message.attach(mime_text.MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        console.print(f"[green]✓ Email sent to {to}[/green]")
