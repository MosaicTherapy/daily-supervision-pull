"""
Google Drive API publisher for the RBT supervision tracker.

Used where there is no Google Drive desktop client to provide the G:/ mount,
i.e. the Azure Container Apps job. It does through the API what merge_data's
filesystem publish does through the mount:
  - live publish: move every other .xlsx in DailyRBTTracking into archived/
    (timestamp-suffixed on a name clash), then put the new workbook in
    DailyRBTTracking, replacing a same-named file
  - archive publish: put the workbook straight into archived/, replacing a
    same-named file

The tracker folder lives in a user's My Drive, and service accounts have no Drive
storage of their own, so files must be created as a real user. Two ways to do
that, both injected from Key Vault in Azure:
  - GOOGLE_OAUTH_TOKEN_JSON: a user's authorized-user token (refresh token +
    OAuth client), produced once by deploy/authorize_drive_user.py. Used today,
    since domain-wide delegation needs a Workspace admin.
  - GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_IMPERSONATE_USER: a service account
    key with domain-wide delegation, acting as that user.
The OAuth token wins if both are set.
"""

import json
import logging
import os
from datetime import datetime

from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive']
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# RBT Supervision Tracking/DailyRBTTracking and its archived/ subfolder. Both can be
# overridden by env so a test run publishes to a scratch folder instead.
DEFAULT_LIVE_FOLDER_ID = '1Mh9gqV27KkEEuyX6M35_SB_vTErRz7Gm'
DEFAULT_ARCHIVE_FOLDER_ID = '1w66zw3i_-tQ9AOQlzN6pwNU8quXhsRn1'


def _folder_ids() -> tuple:
    return (os.getenv('DRIVE_LIVE_FOLDER_ID', DEFAULT_LIVE_FOLDER_ID),
            os.getenv('DRIVE_ARCHIVE_FOLDER_ID', DEFAULT_ARCHIVE_FOLDER_ID))


def _get_service():
    """Build a Drive v3 client acting as the configured user."""
    if os.getenv('GOOGLE_OAUTH_TOKEN_JSON'):
        info = json.loads(os.environ['GOOGLE_OAUTH_TOKEN_JSON'])
        creds = user_credentials.Credentials.from_authorized_user_info(info, scopes=SCOPES)
    else:
        info = json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'])
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES, subject=os.environ['GOOGLE_IMPERSONATE_USER'])
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def _list_files(service, folder_id: str) -> list:
    """Return [{'id', 'name'}] for every non-trashed item directly in folder_id."""
    files, page_token = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields='nextPageToken, files(id, name)',
            pageSize=1000,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            return files


def _upload(service, source_file: str, folder_id: str, existing: list) -> str:
    """Upload source_file into folder_id, replacing the content of a same-named file."""
    name = os.path.basename(source_file)
    media = MediaFileUpload(source_file, mimetype=XLSX_MIME, resumable=True)
    match = next((f for f in existing if f['name'] == name), None)
    if match:
        service.files().update(fileId=match['id'], media_body=media,
                               supportsAllDrives=True).execute()
        return 'Overwrote'
    service.files().create(body={'name': name, 'parents': [folder_id]}, media_body=media,
                           fields='id', supportsAllDrives=True).execute()
    return 'Saved'


def publish_to_live_folder(source_file: str, logger: logging.Logger) -> None:
    """Archive the other workbooks in the live folder, then upload source_file to it."""
    service = _get_service()
    live_id, archive_id = _folder_ids()
    output_filename = os.path.basename(source_file)

    live_files = _list_files(service, live_id)
    archived_names = {f['name'] for f in _list_files(service, archive_id)}

    for f in live_files:
        if not f['name'].endswith('.xlsx') or f['name'] == output_filename:
            continue
        body = {}
        if f['name'] in archived_names:
            name, ext = os.path.splitext(f['name'])
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            body['name'] = f'{name}_{timestamp}{ext}'
        service.files().update(fileId=f['id'], addParents=archive_id, removeParents=live_id,
                               body=body, supportsAllDrives=True).execute()
        logger.info(f"Archived existing file in Google Drive folder: {f['name']}")

    action = _upload(service, source_file, live_id, live_files)
    logger.info(f"{action} file in Google Drive folder via API: {output_filename}")


def publish_to_archive_folder(source_file: str, logger: logging.Logger) -> None:
    """Upload source_file into the archived/ folder, replacing a same-named file."""
    service = _get_service()
    _, archive_id = _folder_ids()
    action = _upload(service, source_file, archive_id, _list_files(service, archive_id))
    logger.info(f"{action} file in Google Drive archive folder via API: {os.path.basename(source_file)}")
