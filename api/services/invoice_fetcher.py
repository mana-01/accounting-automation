"""Invoice fetcher service - Fetch invoices from email."""

import os
import json
import base64
import re
from datetime import datetime, timedelta
from typing import Optional
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


def get_google_credentials(scopes: list[str]):
    """Google認証情報を取得"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(
            creds_dict, scopes=scopes
        )
    raise ValueError("GOOGLE_CREDENTIALS_JSON not set")


class InvoiceFetcher:
    """メールから請求書を取得するサービス"""

    def __init__(self):
        self.gmail_user = os.environ.get("GMAIL_USER_EMAIL", "")
        self.spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
        self.drive_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")

        # Google API clients
        self._gmail = None
        self._drive = None
        self._sheets = None

    @property
    def gmail(self):
        if not self._gmail:
            creds = get_google_credentials([
                "https://www.googleapis.com/auth/gmail.readonly"
            ])
            # Gmail APIはドメイン全体の委任が必要
            delegated = creds.with_subject(self.gmail_user)
            self._gmail = build("gmail", "v1", credentials=delegated)
        return self._gmail

    @property
    def drive(self):
        if not self._drive:
            creds = get_google_credentials([
                "https://www.googleapis.com/auth/drive"
            ])
            # ユーザーとして操作（ドメイン全体の委任）
            delegated = creds.with_subject(self.gmail_user)
            self._drive = build("drive", "v3", credentials=delegated)
        return self._drive

    @property
    def sheets(self):
        if not self._sheets:
            creds = get_google_credentials([
                "https://www.googleapis.com/auth/spreadsheets"
            ])
            self._sheets = build("sheets", "v4", credentials=creds)
        return self._sheets

    def get_email_rules(self) -> list[dict]:
        """Spreadsheetからメール取得ルールを読み込む"""
        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="email_rules!A2:E100"
            ).execute()

            rows = result.get("values", [])
            rules = []
            for row in rows:
                if len(row) >= 3:
                    rules.append({
                        "name": row[0],
                        "sender": row[1] if len(row) > 1 else "",
                        "subject_pattern": row[2] if len(row) > 2 else "",
                        "fetch_type": row[3] if len(row) > 3 else "attachment",
                        "link_pattern": row[4] if len(row) > 4 else "",
                    })
            return rules
        except Exception as e:
            print(f"Error getting email rules: {e}")
            return []

    def search_emails(self, rule: dict, days_back: int = 30) -> list[dict]:
        """ルールに基づいてメールを検索"""
        # 検索クエリを構築
        query_parts = []

        if rule.get("sender"):
            query_parts.append(f"from:{rule['sender']}")

        if rule.get("subject_pattern"):
            # シンプルな件名検索
            subject = rule["subject_pattern"].replace("*", "")
            query_parts.append(f"subject:({subject})")

        # 期間指定
        after_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
        query_parts.append(f"after:{after_date}")

        # PDF添付ありの場合
        if rule.get("fetch_type") == "attachment":
            query_parts.append("has:attachment filename:pdf")

        query = " ".join(query_parts)
        print(f"Gmail search query: {query}")

        try:
            results = self.gmail.users().messages().list(
                userId="me",
                q=query,
                maxResults=50
            ).execute()

            messages = results.get("messages", [])
            detailed = []

            for msg in messages:
                full = self.gmail.users().messages().get(
                    userId="me",
                    id=msg["id"]
                ).execute()
                detailed.append(full)

            return detailed
        except Exception as e:
            print(f"Error searching emails: {e}")
            return []

    def get_attachments(self, message: dict) -> list[dict]:
        """メッセージからPDF添付ファイルを取得"""
        attachments = []
        payload = message.get("payload", {})
        parts = payload.get("parts", [])

        for part in parts:
            filename = part.get("filename", "")
            if filename.lower().endswith(".pdf"):
                attachment_id = part.get("body", {}).get("attachmentId")
                if attachment_id:
                    attachment = self.gmail.users().messages().attachments().get(
                        userId="me",
                        messageId=message["id"],
                        id=attachment_id
                    ).execute()

                    data = attachment.get("data", "")
                    if data:
                        attachments.append({
                            "filename": filename,
                            "data": base64.urlsafe_b64decode(data),
                            "mime_type": "application/pdf"
                        })

        return attachments

    def extract_links(self, message: dict, pattern: str = None) -> list[str]:
        """メール本文からリンクを抽出"""
        body = self._get_message_body(message)
        if not body:
            return []

        # URLを抽出
        url_pattern = r'https?://[^\s<>"\'{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, body)

        # パターンが指定されていれば絞り込み
        if pattern:
            urls = [u for u in urls if re.search(pattern, u)]

        return urls

    def _get_message_body(self, message: dict) -> Optional[str]:
        """メッセージ本文を取得"""
        payload = message.get("payload", {})

        # 直接のbody
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")

        # partsを検索
        for part in payload.get("parts", []):
            if part.get("mimeType") in ["text/plain", "text/html"]:
                data = part.get("body", {}).get("data")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8")

        return None

    def get_message_date(self, message: dict) -> str:
        """メッセージの日付を取得"""
        headers = message.get("payload", {}).get("headers", [])
        for h in headers:
            if h.get("name", "").lower() == "date":
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(h["value"])
                    return dt.strftime("%Y-%m-%d")
                except:
                    pass
        return datetime.now().strftime("%Y-%m-%d")

    def get_message_subject(self, message: dict) -> str:
        """メッセージの件名を取得"""
        headers = message.get("payload", {}).get("headers", [])
        for h in headers:
            if h.get("name", "").lower() == "subject":
                return h.get("value", "")
        return ""

    def save_to_drive(self, file_data: bytes, filename: str, period: str) -> dict:
        """請求書をGoogle Driveに保存"""
        # 月別フォルダを取得/作成
        folder_id = self._get_or_create_folder(period)

        # ファイルをアップロード
        file_metadata = {
            "name": filename,
            "parents": [folder_id]
        }

        media = MediaIoBaseUpload(
            BytesIO(file_data),
            mimetype="application/pdf",
            resumable=True
        )

        file = self.drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink"
        ).execute()

        return {
            "file_id": file.get("id"),
            "web_view_link": file.get("webViewLink")
        }

    def _get_or_create_folder(self, period: str) -> str:
        """月別フォルダを取得または作成"""
        # 既存フォルダを検索
        query = (
            f"name='{period}' and "
            f"'{self.drive_folder_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )

        results = self.drive.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)"
        ).execute()

        files = results.get("files", [])
        if files:
            return files[0]["id"]

        # フォルダを作成
        folder_metadata = {
            "name": period,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [self.drive_folder_id]
        }

        folder = self.drive.files().create(
            body=folder_metadata,
            fields="id"
        ).execute()

        return folder.get("id")

    def record_invoice(self, invoice_data: dict):
        """請求書情報をSpreadsheetに記録"""
        row = [
            invoice_data.get("id", ""),
            invoice_data.get("vendor", ""),
            invoice_data.get("amount", ""),
            invoice_data.get("date", ""),
            invoice_data.get("source", ""),
            invoice_data.get("drive_url", ""),
            invoice_data.get("status", "pending"),
            datetime.now().isoformat()
        ]

        self.sheets.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range="invoices!A:H",
            valueInputOption="USER_ENTERED",
            body={"values": [row]}
        ).execute()

    def fetch_invoices(self, days_back: int = 30) -> dict:
        """メールから請求書を取得するメイン処理"""
        rules = self.get_email_rules()
        results = {
            "processed": 0,
            "saved": 0,
            "errors": [],
            "invoices": []
        }

        for rule in rules:
            try:
                emails = self.search_emails(rule, days_back)
                results["processed"] += len(emails)

                for email in emails:
                    email_date = self.get_message_date(email)
                    subject = self.get_message_subject(email)

                    # 期間を計算（YYYY年M月形式）
                    try:
                        dt = datetime.strptime(email_date, "%Y-%m-%d")
                        period = f"{dt.year}年{dt.month}月"
                    except:
                        period = datetime.now().strftime("%Y年%m月")

                    if rule.get("fetch_type") == "attachment":
                        # PDF添付ファイルを取得
                        attachments = self.get_attachments(email)
                        for att in attachments:
                            filename = f"{rule['name']}_{email_date}_{att['filename']}"
                            drive_result = self.save_to_drive(
                                att["data"],
                                filename,
                                period
                            )

                            invoice_data = {
                                "id": f"inv_{datetime.now().timestamp()}",
                                "vendor": rule["name"],
                                "amount": "",  # PDF解析で後で抽出
                                "date": email_date,
                                "source": "email_attachment",
                                "drive_url": drive_result["web_view_link"],
                                "status": "pending"
                            }
                            self.record_invoice(invoice_data)
                            results["saved"] += 1
                            results["invoices"].append(invoice_data)

                    elif rule.get("fetch_type") == "link":
                        # リンクから取得（TODO: 実装）
                        links = self.extract_links(email, rule.get("link_pattern"))
                        for link in links:
                            results["invoices"].append({
                                "vendor": rule["name"],
                                "link": link,
                                "status": "link_found"
                            })

            except Exception as e:
                results["errors"].append(f"{rule.get('name', 'unknown')}: {str(e)}")

        return results


# シングルトンインスタンス
invoice_fetcher = InvoiceFetcher()
