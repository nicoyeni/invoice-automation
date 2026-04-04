from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os.path
import yaml

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def main():
    creds = None
    if os.path.exists("config/token.json"):
        creds = Credentials.from_authorized_user_file("config/token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing token: {e}")
                return
        else:
            print("Token invalid and no refresh token. Please re-authenticate.")
            return

    try:
        service = build("sheets", "v4", credentials=creds)

        # Load spreadsheet ID from config
        with open("config/config.yaml", 'r') as f:
            config = yaml.safe_load(f)
        spreadsheet_id = config['sheets']['spreadsheet_id']

        print(f"Checking Spreadsheet ID: {spreadsheet_id}")

        sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = sheet_metadata.get('sheets', '')
        
        print(f"Found {len(sheets)} sheets:")
        for sheet in sheets:
            title = sheet.get("properties", {}).get("title", "Unknown")
            print(f"- {title}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
