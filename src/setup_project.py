import yaml
import os
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from rich.console import Console

console = Console()

# Combined scopes for all components
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

CONFIG_PATH = "config/config.yaml"

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, sort_keys=False)

def authenticate():
    config = load_config()
    creds_file = config["google"]["credentials_file"]
    token_file = config["google"]["token_file"]

    if not os.path.exists(creds_file):
        console.print(f"[red]Error: {creds_file} not found![/red]")
        sys.exit(1)

    console.print("[yellow]Initiating Authentication...[/yellow]")
    console.print("A browser window should open for you to log in.")

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    creds = flow.run_local_server(port=0)

    # Save credentials
    Path(token_file).parent.mkdir(parents=True, exist_ok=True)
    with open(token_file, "w") as token:
        token.write(creds.to_json())
    
    console.print(f"[green]✓ Authentication successful! Token saved to {token_file}[/green]")
    return creds

def create_spreadsheet(creds):
    console.print("[yellow]Creating Google Sheet...[/yellow]")
    service = build("sheets", "v4", credentials=creds)
    
    spreadsheet = {
        "properties": {
            "title": "Invoice Automation Data"
        }
    }
    
    spreadsheet = service.spreadsheets().create(body=spreadsheet, fields="spreadsheetId").execute()
    spreadsheet_id = spreadsheet.get("spreadsheetId")
    console.print(f"[green]✓ Created Spreadsheet with ID: {spreadsheet_id}[/green]")
    return spreadsheet_id

def main():
    if not os.path.exists(CONFIG_PATH):
        console.print(f"[red]Config file not found at {CONFIG_PATH}[/red]")
        return

    # 1. Authenticate
    creds = authenticate()

    # 2. Update Config with Spreadsheet ID
    config = load_config()
    
    # Check if we already have a spreadsheet ID
    current_id = config["sheets"].get("spreadsheet_id", "")
    if "YOUR_SPREADSHEET_ID" in current_id or not current_id:
        spreadsheet_id = create_spreadsheet(creds)
        config["sheets"]["spreadsheet_id"] = spreadsheet_id
        save_config(config)
        console.print("[green]✓ Config updated with new Spreadsheet ID[/green]")
    else:
        console.print(f"[blue]Spreadsheet ID already exists: {current_id}[/blue]")

    console.print("[bold green]Setup Complete![/bold green]")

if __name__ == "__main__":
    main()
