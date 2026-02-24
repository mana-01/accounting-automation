"""Chatwork notification service for tax accountant communication."""

import os
from typing import Optional

import requests


class ChatworkService:
    """Chatwork APIを使用した通知サービス"""

    BASE_URL = "https://api.chatwork.com/v2"

    def __init__(self):
        self.api_token = os.getenv("CHATWORK_API_TOKEN", "")
        self.room_id = os.getenv("CHATWORK_ROOM_ID", "")
        self.enabled = bool(self.api_token and self.room_id)

    def _headers(self) -> dict:
        return {
            "X-ChatWorkToken": self.api_token,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def send_message(self, message: str) -> Optional[dict]:
        """Chatworkルームにメッセージを送信"""
        if not self.enabled:
            print("Chatwork is not configured, skipping notification")
            return None

        url = f"{self.BASE_URL}/rooms/{self.room_id}/messages"
        data = {"body": message, "self_unread": 1}

        try:
            response = requests.post(url, headers=self._headers(), data=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Chatwork notification failed: {e}")
            return None

    def notify_invoice_saved(
        self,
        subscription_name: str,
        amount: float,
        invoice_date: str,
        period: str,
        drive_url: str,
    ) -> Optional[dict]:
        """請求書の格納完了を通知"""
        message = (
            f"[info][title]請求書を格納しました[/title]"
            f"サービス: {subscription_name}\n"
            f"金額: ¥{amount:,.0f}\n"
            f"請求日: {invoice_date}\n"
            f"期間: {period}\n"
            f"Google Drive: {drive_url}[/info]"
        )
        return self.send_message(message)

    def notify_reconciliation_complete(
        self,
        period: str,
        total: int,
        matched: int,
        unmatched: int,
        missing_invoices: list[str],
    ) -> Optional[dict]:
        """CSV照合の完了を通知"""
        missing_text = ""
        if missing_invoices:
            items = "\n".join(f"・{name}" for name in missing_invoices[:10])
            missing_text = f"\n\n不足請求書:\n{items}"
            if len(missing_invoices) > 10:
                missing_text += f"\n...他 {len(missing_invoices) - 10}件"

        message = (
            f"[info][title]月次照合が完了しました[/title]"
            f"期間: {period}\n"
            f"取引数: {total}件\n"
            f"マッチ: {matched}件\n"
            f"不足: {unmatched}件{missing_text}[/info]"
        )
        return self.send_message(message)

    def notify_monthly_reminder(
        self,
        period: str,
        subscription_count: int,
        monthly_total: float,
    ) -> Optional[dict]:
        """月次リマインダーを通知"""
        message = (
            f"[info][title]{period} 経理作業リマインド[/title]"
            f"登録サブスク: {subscription_count}件\n"
            f"予定支出: ¥{monthly_total:,.0f}\n\n"
            f"やることリスト:\n"
            f"1. カード明細CSVをダウンロードしてアップロード\n"
            f"2. 銀行明細CSVをダウンロードしてアップロード\n"
            f"3. 不足請求書を準備\n"
            f"4. 照会結果を確認[/info]"
        )
        return self.send_message(message)

    def notify_custom(self, title: str, body: str) -> Optional[dict]:
        """カスタムメッセージを通知"""
        message = f"[info][title]{title}[/title]{body}[/info]"
        return self.send_message(message)

    def notify_status_report(
        self,
        period: str,
        subscription_count: int,
        invoice_count: int,
        matched_count: int,
        pending_count: int,
    ) -> Optional[dict]:
        """現在の状況レポートを送信"""
        message = (
            f"[info][title]{period} 経理状況レポート[/title]"
            f"登録サブスク: {subscription_count}件\n"
            f"取得済み請求書: {invoice_count}件\n"
            f"  - 照会済み: {matched_count}件\n"
            f"  - 未照会: {pending_count}件[/info]"
        )
        return self.send_message(message)


# シングルトンインスタンス
chatwork_service = ChatworkService()
