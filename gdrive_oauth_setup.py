"""
One-off helper script to generate an OAuth token for Google Drive uploads.

Run this manually (in a terminal), NOT from systemd:

    source venv/bin/activate
    python gdrive_oauth_setup.py

On a machine with a browser: it will open a browser for you to authorise.
On a headless VPS: use SSH port forwarding first:
    ssh -L 8080:localhost:8080 user@your-vps
Then run this script and open the printed URL in your local browser.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from config import CONFIG_DIR, GDRIVE_OAUTH_TOKEN_FILE
from gdrive_uploader import SCOPES


def main():
    client_secrets = CONFIG_DIR / "gdrive-oauth-client.json"
    token_file = Path(GDRIVE_OAUTH_TOKEN_FILE)

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

    auth_url, _ = flow.authorization_url(prompt="consent")
    print("\nOpen this URL in your browser to authorise:")
    print(auth_url)
    print("\nAfter authorising, you will be redirected to localhost:8080.")
    print("If running on a VPS, use SSH port forwarding first:")
    print("  ssh -L 8080:localhost:8080 root@farrelrp\n")

    creds = flow.run_local_server(port=8080, open_browser=False)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    with open(token_file, "w") as f:
        f.write(creds.to_json())

    print(f"\nToken saved to {token_file}")
    print("Restart the daemon to     pick up the new token.")


if __name__ == "__main__":
    main()

