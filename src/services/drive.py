"""Google Drive service for invoice storage."""

import os
from io import BytesIO
from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .spreadsheet import get_google_credentials


class DriveService:
    """Google Drive を使用した請求書保存サービス"""

    def __init__(self):
        self.root_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        self.accountant_card_folder_id = os.getenv("ACCOUNTANT_CARD_FOLDER_ID", "")
        self.accountant_bank_folder_id = os.getenv("ACCOUNTANT_BANK_FOLDER_ID", "")
        self.service = self._build_service()

    def _build_service(self):
        """Google Drive API サービスを構築"""
        credentials = get_google_credentials(
            ["https://www.googleapis.com/auth/drive"]
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
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
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
            fields="id",
            supportsAllDrives=True
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
        existing = self.service.files().list(
            q=query, fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

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
                media_body=media,
                supportsAllDrives=True
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
                fields="id, webViewLink",
                supportsAllDrives=True
            ).execute()
            file_id = result["id"]

        # ファイル情報を取得
        file_info = self.service.files().get(
            fileId=file_id,
            fields="id, webViewLink",
            supportsAllDrives=True
        ).execute()

        print(f"Uploaded invoice: {file_name} ({file_id})")
        return {
            "file_id": file_id,
            "web_view_link": file_info.get("webViewLink", "")
        }

    def get_file(self, file_id: str) -> bytes:
        """ファイルをダウンロード"""
        request = self.service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True
        )
        content = request.execute()
        return content

    def list_invoices_in_folder(self, period: str) -> list[dict]:
        """月別フォルダ内の請求書一覧を取得"""
        folder_id = self.get_or_create_monthly_folder(period)

        results = self.service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, webViewLink)",
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
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
        self.service.files().delete(
            fileId=file_id,
            supportsAllDrives=True
        ).execute()

    def get_folder_url(self, folder_id: str) -> str:
        """フォルダのURLを取得"""
        return f"https://drive.google.com/drive/folders/{folder_id}"

    def _get_or_create_subfolder(self, parent_folder_id: str, folder_name: str) -> str:
        """指定された親フォルダ内にサブフォルダを取得または作成"""
        query = (
            f"name='{folder_name}' and "
            f"'{parent_folder_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )

        results = self.service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        files = results.get("files", [])
        if files:
            return files[0]["id"]

        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_folder_id]
        }

        folder = self.service.files().create(
            body=folder_metadata,
            fields="id",
            supportsAllDrives=True
        ).execute()

        print(f"Created subfolder: {folder_name} ({folder['id']})")
        return folder["id"]

    def copy_file_to_folder(self, file_id: str, target_folder_id: str, new_name: Optional[str] = None) -> dict:
        """ファイルを指定フォルダにコピー"""
        body = {"parents": [target_folder_id]}
        if new_name:
            body["name"] = new_name

        copied_file = self.service.files().copy(
            fileId=file_id,
            body=body,
            fields="id, name, webViewLink",
            supportsAllDrives=True
        ).execute()

        return {
            "file_id": copied_file["id"],
            "name": copied_file.get("name", ""),
            "web_view_link": copied_file.get("webViewLink", "")
        }

    def share_with_accountant(
        self,
        period: str,
        card_file_ids: list[str],
        bank_file_ids: list[str]
    ) -> dict:
        """税理士共有フォルダに月別ファイルをコピー

        Args:
            period: 期間 (例: "2026年2月")
            card_file_ids: クレジットカード関連の請求書ファイルID一覧
            bank_file_ids: 銀行関連の請求書ファイルID一覧

        Returns:
            コピー結果の辞書 (card_folder_url, bank_folder_url, card_count, bank_count)
        """
        result = {
            "card_folder_url": "",
            "bank_folder_url": "",
            "card_count": 0,
            "bank_count": 0,
        }

        # クレジットカードファイルをコピー
        if card_file_ids and self.accountant_card_folder_id:
            card_subfolder_id = self._get_or_create_subfolder(
                self.accountant_card_folder_id, period
            )
            for file_id in card_file_ids:
                try:
                    self.copy_file_to_folder(file_id, card_subfolder_id)
                    result["card_count"] += 1
                except Exception as e:
                    print(f"Failed to copy card file {file_id}: {e}")
            result["card_folder_url"] = self.get_folder_url(card_subfolder_id)

        # 銀行ファイルをコピー
        if bank_file_ids and self.accountant_bank_folder_id:
            bank_subfolder_id = self._get_or_create_subfolder(
                self.accountant_bank_folder_id, period
            )
            for file_id in bank_file_ids:
                try:
                    self.copy_file_to_folder(file_id, bank_subfolder_id)
                    result["bank_count"] += 1
                except Exception as e:
                    print(f"Failed to copy bank file {file_id}: {e}")
            result["bank_folder_url"] = self.get_folder_url(bank_subfolder_id)

        return result


# シングルトンインスタンス
drive_service = DriveService()
