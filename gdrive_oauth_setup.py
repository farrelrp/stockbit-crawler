"""
One-off helper script to generate an OAuth token for Google Drive uploads.

Run this manually (in a terminal with a TTY), NOT from systemd:

    source venv/bin/activate
    python gdrive_oauth_setup.py

It will open a browser or print a URL for you to authorise the app and
then save the resulting token into config_data/gdrive-oauth-token.json.
The daemon and Flask app will reuse that token non-interactively.
"""
import logging
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from config import CONFIG_DIR
from gdrive_uploader import SCOPES, OAUTH_TOKEN_FILE

logger = logging.getLogger(__name__)


def main():
    client_secrets = CONFIG_DIR / "gdrive-oauth-client.json"
    if not client_secrets.exists():
        print(
            f"Client secrets file not found: {client_secrets}\n"
            f"- Go to https://console.cloud.google.com/apis/credentials\n"
            f"- Create an OAuth client ID (Desktop app)\n"
            f"- Download the JSON and save it as {client_secrets}"
        )
        return

    print("Starting OAuth flow for Google Drive...")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets), scopes=SCOPES
    )

    # This opens a browser if available or prints a URL for you
    creds = flow.run_console()

    OAUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OAUTH_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\nSaved OAuth token to {OAUTH_TOKEN_FILE}")
    print("You can now set GDRIVE_USE_OAUTH=true in your .env and restart the daemon.")


if __name__ == "__main__":
    main()

