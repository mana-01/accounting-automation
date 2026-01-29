"""Google Drive service for invoice storage."""

import os
from io import BytesIO
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


class DriveService:
    """Google Drive を使用した請求書保存サービス"""

    def __init__(self):
        self.root_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        self.service = self._build_service()

    def _build_service(self):
        """Google Drive API サービスを構築"""
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")

        if creds_path and os.path.exists(creds_path):
            credentials = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/drive"]
            )
        else:
            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
                    "private_key": os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n"),
                    "token_uri": "https://oauth2.googleapis.com/token",
                },
                scopes=["https://www.googleapis.com/auth/drive"]
            )

        return build("drive", "v3", credentials=credentials)

    def get_or_create_monthly_folder(self, period: str) -> str:
        """月別フォルダを取得または作成 (例: "2026年1月")"""
        # 既存フォルダを検索
        query = (
            f"name='{period}' and "
            f"'{self.root_folder_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )

        results = self.service.files().list(
            q=query,
            fields="files(id, name)"
        ).execute()

        files = results.get("files", [])
        if files:
            return files[0]["id"]

        # 新規フォルダ作成
        folder_metadata = {
            "name": period,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [self.root_folder_id]
        }

        folder = self.service.files().create(
            body=folder_metadata,
            fields="id"
        ).execute()

        print(f"Created folder: {period} ({folder['id']})")
        return folder["id"]

    def upload_invoice(
        self,
        file_content: bytes,
        file_name: str,
        mime_type: str,
        period: str
    ) -> dict:
        """請求書ファイルを月別フォルダにアップロード"""
        folder_id = self.get_or_create_monthly_folder(period)

        # 既存ファイルをチェック
        query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
        existing = self.service.files().list(q=query, fields="files(id)").execute()

        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype=mime_type,
            resumable=True
        )

        if existing.get("files"):
            # 既存ファイルを更新
            file_id = existing["files"][0]["id"]
            self.service.files().update(
                fileId=file_id,
                media_body=media
            ).execute()
        else:
            # 新規ファイルを作成
            file_metadata = {
                "name": file_name,
                "parents": [folder_id]
            }
            result = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink"
            ).execute()
            file_id = result["id"]

        # ファイル情報を取得
        file_info = self.service.files().get(
            fileId=file_id,
            fields="id, webViewLink"
        ).execute()

        print(f"Uploaded invoice: {file_name} ({file_id})")
        return {
            "file_id": file_id,
            "web_view_link": file_info.get("webViewLink", "")
        }

    def get_file(self, file_id: str) -> bytes:
        """ファイルをダウンロード"""
        request = self.service.files().get_media(fileId=file_id)
        content = request.execute()
        return content

    def list_invoices_in_folder(self, period: str) -> list[dict]:
        """月別フォルダ内の請求書一覧を取得"""
        folder_id = self.get_or_create_monthly_folder(period)

        results = self.service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, webViewLink)",
            orderBy="name"
        ).execute()

        return [
            {
                "id": f["id"],
                "name": f["name"],
                "web_view_link": f.get("webViewLink", "")
            }
            for f in results.get("files", [])
        ]

    def delete_file(self, file_id: str) -> None:
        """ファイルを削除"""
        self.service.files().delete(fileId=file_id).execute()

    def get_folder_url(self, folder_id: str) -> str:
        """フォルダのURLを取得"""
        return f"https://drive.google.com/drive/folders/{folder_id}"


# シングルトンインスタンス
drive_service = DriveService()
