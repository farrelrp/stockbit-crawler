"""
Google Drive uploader using a Service Account.
Uploads orderbook CSVs (and optionally historical trade CSVs) to a shared
Drive folder, organising them into date-based sub-folders.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GDRIVE_AVAILABLE = True
except ImportError:
    GDRIVE_AVAILABLE = False
    logger.warning(
        "google-api-python-client / google-auth not installed. "
        "Install with: pip install google-api-python-client google-auth"
    )

SCOPES = ['https://www.googleapis.com/auth/drive.file']
UPLOAD_MANIFEST_FILE = Path('config_data/gdrive_uploads.json')


class GDriveUploader:
    """Handles uploading files to Google Drive via a service account."""

    def __init__(self, service_account_file: str, folder_id: str,
                 delete_after_upload: bool = False):
        if not GDRIVE_AVAILABLE:
            raise ImportError(
                "google-api-python-client / google-auth not installed"
            )

        self.folder_id = folder_id
        self.delete_after_upload = delete_after_upload
        self._service = None
        self._manifest = self._load_manifest()

        try:
            creds = service_account.Credentials.from_service_account_file(
                service_account_file, scopes=SCOPES
            )
            self._service = build('drive', 'v3', credentials=creds,
                                  cache_discovery=False)
            logger.info("Google Drive service initialised (service account)")
        except Exception as e:
            logger.error(f"Failed to initialise Google Drive: {e}")
            raise

    # ---- manifest (duplicate tracking) ----

    def _load_manifest(self) -> Dict[str, str]:
        """Load the upload manifest — maps local path -> Drive file ID."""
        try:
            if UPLOAD_MANIFEST_FILE.exists():
                with open(UPLOAD_MANIFEST_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load upload manifest: {e}")
        return {}

    def _save_manifest(self):
        try:
            UPLOAD_MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(UPLOAD_MANIFEST_FILE, 'w') as f:
                json.dump(self._manifest, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save upload manifest: {e}")

    def _already_uploaded(self, filepath: Path) -> bool:
        return str(filepath) in self._manifest

    def _record_upload(self, filepath: Path, drive_file_id: str):
        self._manifest[str(filepath)] = drive_file_id
        self._save_manifest()

    # ---- Drive helpers ----

    def _get_or_create_subfolder(self, name: str,
                                 parent_id: str) -> Optional[str]:
        """Return the ID of *name* inside *parent_id*, creating it if needed."""
        try:
            q = (
                f"name='{name}' and '{parent_id}' in parents "
                f"and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false"
            )
            resp = self._service.files().list(
                q=q, spaces='drive', fields='files(id, name)'
            ).execute()
            files = resp.get('files', [])
            if files:
                return files[0]['id']

            # create it
            meta = {
                'name': name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id],
            }
            folder = self._service.files().create(
                body=meta, fields='id'
            ).execute()
            logger.info(f"Created Drive subfolder '{name}'")
            return folder['id']
        except Exception as e:
            logger.error(f"Subfolder get/create failed for '{name}': {e}")
            return None

    def _upload_single_file(self, filepath: Path,
                            parent_folder_id: str) -> Optional[Dict[str, str]]:
        """Upload one file and return {id, name, webViewLink} or None."""
        try:
            media = MediaFileUpload(str(filepath), resumable=True)
            meta = {
                'name': filepath.name,
                'parents': [parent_folder_id],
            }
            result = self._service.files().create(
                body=meta, media_body=media,
                fields='id, name, webViewLink'
            ).execute()
            logger.info(f"Uploaded {filepath.name} -> {result.get('id')}")
            return result
        except Exception as e:
            logger.error(f"Upload failed for {filepath.name}: {e}")
            return None

    # ---- public API ----

    def upload_file(self, filepath: Path,
                    date_subfolder: str = None) -> Dict[str, Any]:
        """Upload a single file, optionally into a date-based subfolder.

        Returns dict with 'success', 'file_id', 'link', etc.
        """
        if self._already_uploaded(filepath):
            return {
                'success': True,
                'skipped': True,
                'reason': 'already uploaded',
                'file': filepath.name,
            }

        target_folder = self.folder_id
        if date_subfolder:
            sub_id = self._get_or_create_subfolder(date_subfolder,
                                                   self.folder_id)
            if sub_id:
                target_folder = sub_id

        result = self._upload_single_file(filepath, target_folder)
        if result:
            self._record_upload(filepath, result['id'])
            if self.delete_after_upload:
                try:
                    os.remove(filepath)
                    logger.info(f"Deleted local file after upload: {filepath}")
                except Exception as e:
                    logger.warning(f"Could not delete {filepath}: {e}")

            return {
                'success': True,
                'skipped': False,
                'file': filepath.name,
                'file_id': result['id'],
                'link': result.get('webViewLink', ''),
            }

        return {
            'success': False,
            'file': filepath.name,
            'error': 'Upload failed (see logs)',
        }

    def upload_orderbook_day(self, date_str: str,
                             orderbook_dir: Path) -> Dict[str, Any]:
        """Upload all orderbook CSVs for a given date.

        Looks for files matching ``{date_str}_*.csv`` in *orderbook_dir*.
        Returns a summary dict suitable for Telegram notifications.
        """
        pattern = f"{date_str}_*.csv"
        files = sorted(orderbook_dir.glob(pattern))

        if not files:
            return {
                'success': True,
                'date': date_str,
                'uploaded': 0,
                'failed': 0,
                'skipped': 0,
                'total_bytes': 0,
                'results': [],
                'message': f'No orderbook files found for {date_str}',
            }

        uploaded, failed, skipped = 0, 0, 0
        total_bytes = 0
        results: List[Dict] = []

        for fp in files:
            res = self.upload_file(fp, date_subfolder=date_str)
            results.append(res)
            if res.get('skipped'):
                skipped += 1
            elif res['success']:
                uploaded += 1
                total_bytes += fp.stat().st_size if fp.exists() else 0
            else:
                failed += 1

        return {
            'success': failed == 0,
            'date': date_str,
            'uploaded': uploaded,
            'failed': failed,
            'skipped': skipped,
            'total_bytes': total_bytes,
            'results': results,
            'folder_id': self.folder_id,
        }

    def upload_job_output(self, filepath: Path) -> Dict[str, Any]:
        """Upload a historical-trade job CSV into a 'historical' subfolder."""
        return self.upload_file(filepath, date_subfolder='historical')

    def get_status(self) -> Dict[str, Any]:
        """Quick health-check info."""
        return {
            'available': GDRIVE_AVAILABLE,
            'configured': self._service is not None,
            'folder_id': self.folder_id,
            'delete_after_upload': self.delete_after_upload,
            'files_uploaded': len(self._manifest),
        }
