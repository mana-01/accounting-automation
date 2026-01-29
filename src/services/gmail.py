"""Gmail service for fetching invoices from emails."""

import os
import re
import base64
from datetime import datetime
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..models import EmailRule, Invoice, InvoiceStatus
import uuid


class GmailService:
    """Gmail から請求書を取得するサービス"""

    def __init__(self):
        self.user_email = os.getenv("GMAIL_USER_EMAIL", "")
        self.service = self._build_service()

    def _build_service(self):
        """Gmail API サービスを構築"""
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")

        if creds_path and os.path.exists(creds_path):
            credentials = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                subject=self.user_email  # ドメイン全体の委任
            )
        else:
            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
                    "private_key": os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n"),
                    "token_uri": "https://oauth2.googleapis.com/token",
                },
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                subject=self.user_email
            )

        return build("gmail", "v1", credentials=credentials)

    def search_emails(
        self,
        rule: EmailRule,
        after_date: Optional[datetime] = None
    ) -> list[dict]:
        """ルールに基づいてメールを検索"""
        query = self._build_search_query(rule, after_date)

        try:
            results = self.service.users().messages().list(
                userId="me",
                q=query,
                maxResults=50
            ).execute()

            messages = results.get("messages", [])
            if not messages:
                return []

            # 各メッセージの詳細を取得
            detailed_messages = []
            for msg in messages:
                full_msg = self.service.users().messages().get(
                    userId="me",
                    id=msg["id"]
                ).execute()
                detailed_messages.append(full_msg)

            return detailed_messages
        except Exception as e:
            print(f"Error searching emails: {e}")
            return []

    def _build_search_query(
        self,
        rule: EmailRule,
        after_date: Optional[datetime] = None
    ) -> str:
        """Gmail検索クエリを構築"""
        parts = []

        if rule.sender_email:
            parts.append(f"from:{rule.sender_email}")

        if rule.subject_pattern:
            # 正規表現パターンを簡易的なGmail検索に変換
            simplified = rule.subject_pattern.replace(".*", "").replace("\\", "")
            parts.append(f"subject:({simplified})")

        if after_date:
            date_str = after_date.strftime("%Y/%m/%d")
            parts.append(f"after:{date_str}")

        # PDF添付ファイルがある場合
        if rule.fetch_type == "attachment":
            parts.append("has:attachment filename:pdf")

        return " ".join(parts)

    def get_attachments(self, message_id: str) -> list[dict]:
        """メッセージからPDF添付ファイルを取得"""
        message = self.service.users().messages().get(
            userId="me",
            id=message_id
        ).execute()

        attachments = []
        parts = message.get("payload", {}).get("parts", [])

        for part in parts:
            filename = part.get("filename", "")
            if filename and part.get("body", {}).get("attachmentId"):
                attachment = self.service.users().messages().attachments().get(
                    userId="me",
                    messageId=message_id,
                    id=part["body"]["attachmentId"]
                ).execute()

                if attachment.get("data"):
                    attachments.append({
                        "filename": filename,
                        "data": base64.urlsafe_b64decode(attachment["data"]),
                        "mime_type": part.get("mimeType", "application/pdf")
                    })

        return attachments

    def extract_links(self, message: dict) -> list[str]:
        """メール本文からリンクを抽出"""
        body = self._get_message_body(message)
        if not body:
            return []

        # URLパターンで抽出
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        return re.findall(url_pattern, body)

    def _get_message_body(self, message: dict) -> Optional[str]:
        """メッセージ本文を取得"""
        payload = message.get("payload", {})

        # 直接のbody
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")

        # parts内を検索
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") in ["text/plain", "text/html"]:
                if part.get("body", {}).get("data"):
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")

        return None

    def get_message_date(self, message: dict) -> datetime:
        """メッセージの日付を取得"""
        headers = message.get("payload", {}).get("headers", [])
        for header in headers:
            if header.get("name", "").lower() == "date":
                from email.utils import parsedate_to_datetime
                try:
                    return parsedate_to_datetime(header["value"])
                except:
                    pass
        return datetime.now()

    def get_message_subject(self, message: dict) -> str:
        """メッセージの件名を取得"""
        headers = message.get("payload", {}).get("headers", [])
        for header in headers:
            if header.get("name", "").lower() == "subject":
                return header.get("value", "")
        return ""

    def create_invoice_from_email(
        self,
        subscription_id: str,
        subscription_name: str,
        amount: float,
        currency: str,
        email_date: datetime
    ) -> Invoice:
        """メールから請求書オブジェクトを作成"""
        return Invoice(
            id=str(uuid.uuid4()),
            subscription_id=subscription_id,
            subscription_name=subscription_name,
            amount=amount,
            currency=currency,
            invoice_date=email_date.strftime("%Y-%m-%d"),
            source_type="email",
            status=InvoiceStatus.PENDING,
            created_at=datetime.now().isoformat()
        )


# シングルトンインスタンス
gmail_service = GmailService()
