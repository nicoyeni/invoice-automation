"""Google Drive watcher - monitors a folder for new invoice files."""

from pathlib import Path
from typing import Optional
import io

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from rich.console import Console

console = Console()


class DriveWatcher:
    """Watches a Google Drive folder for new invoice files."""

    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

    SUPPORTED_MIMES = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/tiff",
    }

    def __init__(
        self,
        credentials_file: str,
        token_file: str,
        folder_id: str,
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.folder_id = folder_id
        self.service = None
        self._processed_files: set[str] = set()  # Track processed file IDs

    def authenticate(self) -> None:
        """Authenticate with Google Drive API."""
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

        self.service = build("drive", "v3", credentials=creds)
        console.print("[green]✓ Google Drive authenticated[/green]")

    def fetch_new_files(self) -> list[dict]:
        """Fetch new (unprocessed) files from the watched folder."""
        if not self.service:
            self.authenticate()

        # Build mime type filter
        mime_filter = " or ".join(
            f"mimeType='{m}'" for m in self.SUPPORTED_MIMES
        )
        query = f"'{self.folder_id}' in parents and ({mime_filter}) and trashed=false"

        results = (
            self.service.files()
            .list(q=query, fields="files(id, name, mimeType, createdTime)")
            .execute()
        )

        files = results.get("files", [])

        # Filter out already processed
        new_files = [f for f in files if f["id"] not in self._processed_files]

        if new_files:
            console.print(f"[blue]Found {len(new_files)} new file(s) in Drive[/blue]")
        else:
            console.print("[dim]No new files in Drive folder[/dim]")

        return new_files

    def download_file(self, file_id: str) -> tuple[bytes, str]:
        """Download a file's contents. Returns (data, filename)."""
        if not self.service:
            self.authenticate()

        # Get file metadata
        file_meta = (
            self.service.files()
            .get(fileId=file_id, fields="name, mimeType")
            .execute()
        )

        # Download
        request = self.service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        return buffer.getvalue(), file_meta["name"]

    def mark_as_processed(self, file_id: str) -> None:
        """Mark a file as processed (in-memory tracking)."""
        self._processed_files.add(file_id)
