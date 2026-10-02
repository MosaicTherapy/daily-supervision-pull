"""
One-time: sign in as the Drive user the Azure job publishes as, and write the
authorized-user token that becomes the google-drive-oauth-token Key Vault secret.

Run locally (it opens a browser). Needs google-auth-oauthlib, which is NOT in
requirements.txt because the job itself never runs this:

    pip install google-auth-oauthlib
    python deploy/authorize_drive_user.py <oauth-client.json> <token-out.json>

<oauth-client.json> is the "Desktop app" OAuth client downloaded from the
mosaic-data-pipelines project. The token file holds a refresh token: treat it as
a secret, store it in Key Vault, and delete it.

Afterwards it lists the live tracker folder (read-only) to prove the token works.
"""

import json
import sys

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/drive']
LIVE_FOLDER_ID = '1Mh9gqV27KkEEuyX6M35_SB_vTErRz7Gm'


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client_file, token_out = sys.argv[1], sys.argv[2]

    # access_type=offline + prompt=consent guarantees a refresh token is returned,
    # even if this account has authorized the client before.
    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')
    if not creds.refresh_token:
        sys.exit('No refresh token returned; nothing written.')

    with open(token_out, 'w') as f:
        f.write(creds.to_json())
    print(f'Wrote token to {token_out}')

    # Prove the stored form works the way the job will load it.
    with open(token_out) as f:
        reloaded = Credentials.from_authorized_user_info(json.load(f), scopes=SCOPES)
    service = build('drive', 'v3', credentials=reloaded, cache_discovery=False)
    about = service.about().get(fields='user(emailAddress)').execute()
    files = service.files().list(
        q=f"'{LIVE_FOLDER_ID}' in parents and trashed = false",
        fields='files(name)', supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get('files', [])
    print(f"Signed in as {about['user']['emailAddress']}; DailyRBTTracking contains: "
          f"{sorted(f['name'] for f in files)}")


if __name__ == '__main__':
    main()
