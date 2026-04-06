"""
Google Drive Uploader Utility
==============================
Uploads files to a specific Google Drive folder using a service account.
This is the right approach for internal tools — no browser OAuth flow required.

HOW TO SET IT UP (one-time, five minutes):
  1. Go to console.cloud.google.com → create a project (or use an existing one)
  2. APIs & Services → Enable → "Google Drive API"
  3. Credentials → Create Credentials → Service Account → download the JSON key
  4. Open Google Drive → right-click the target folder → Share
     → paste the service account email (looks like xxx@yyy.iam.gserviceaccount.com)
     → give it "Editor" access
  5. Add to your backend/.env:
       GOOGLE_CREDENTIALS_PATH=/path/to/service-account-key.json
       GOOGLE_DRIVE_FOLDER_ID=<the folder ID from the Drive URL>

If those two env vars are not set the upload is silently skipped.
The rest of the app works fine either way.
"""

import os
from pathlib import Path


def upload_to_drive(
    content:   bytes,
    filename:  str,
    mime_type: str = "application/octet-stream",
) -> dict:
    """
    Upload bytes to the configured Google Drive folder.

    Returns:
      { "file_id": str, "url": str, "filename": str }   on success
      { "skipped": True, "reason": str }                 if Drive not configured
      { "error": str }                                    on failure
    """
    credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "").strip()
    folder_id        = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()

    if not credentials_path or not folder_id:
        return {
            "skipped": True,
            "reason":  (
                "Google Drive not configured. "
                "Set GOOGLE_CREDENTIALS_PATH and GOOGLE_DRIVE_FOLDER_ID in .env to enable."
            ),
        }

    creds_path = Path(credentials_path)
    if not creds_path.exists():
        return {"error": f"Service account credentials file not found: {credentials_path}"}

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload

        creds = service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        file_metadata = {
            "name":    filename,
            "parents": [folder_id],
        }
        media  = MediaInMemoryUpload(content, mimetype=mime_type)
        result = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id,webViewLink")
            .execute()
        )

        print(f"[DriveUploader] Uploaded '{filename}' → {result.get('webViewLink')}")
        return {
            "file_id":  result.get("id"),
            "url":      result.get("webViewLink"),
            "filename": filename,
        }

    except ImportError:
        return {
            "error": (
                "Google API libraries not installed. "
                "Run: pip install google-api-python-client google-auth"
            )
        }
    except Exception as e:
        return {"error": f"Google Drive upload failed: {str(e)}"}
