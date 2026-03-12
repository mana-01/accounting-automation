"""Chatwork notification service for accounting automation."""

import os
import requests


class ChatworkService:
    """Chatwork APIを使用した通知サービス"""

    BASE_URL = "https://api.chatwork.com/v2"

    def __init__(self):
        # 環境変数はプロパティで都度読み込む（インポート時の未ロード問題を回避）
        pass

    @property
    def api_token(self) -> str:
        return os.getenv("CHATWORK_API_TOKEN", "")

    @property
    def room_id(self) -> str:
        return os.getenv("CHATWORK_ROOM_ID", "")

    @property
    def accountant_account_id(self) -> str:
        return os.getenv("CHATWORK_ACCOUNTANT_ACCOUNT_ID", "")

    def _is_configured(self) -> bool:
        """Chatwork通知が設定されているか確認"""
        return bool(self.api_token and self.room_id)

    def send_message(self, message: str) -> dict:
        """Chatworkのルームにメッセージを送信

        Args:
            message: 送信するメッセージ本文

        Returns:
            APIレスポンスの辞書 (message_id を含む)

        Raises:
            ValueError: API設定が不足している場合
            requests.HTTPError: API呼び出しが失敗した場合
        """
        if not self._is_configured():
            raise ValueError(
                "Chatwork APIの設定が不足しています。"
                "CHATWORK_API_TOKEN と CHATWORK_ROOM_ID を設定してください。"
            )

        url = f"{self.BASE_URL}/rooms/{self.room_id}/messages"
        headers = {"X-ChatWorkToken": self.api_token}
        data = {"body": message, "self_unread": 0}

        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()

    def notify_accounting_share_complete(
        self,
        period: str,
        card_count: int,
        bank_count: int,
        card_folder_url: str = "",
        bank_folder_url: str = "",
    ) -> dict | None:
        """税理士共有完了をChatworkで通知

        Args:
            period: 対象期間 (例: "2026年2月")
            card_count: クレジットカード請求書の件数
            bank_count: 銀行振込請求書の件数
            card_folder_url: カード請求書フォルダのURL
            bank_folder_url: 銀行請求書フォルダのURL

        Returns:
            APIレスポンスの辞書、または未設定の場合はNone
        """
        if not self._is_configured():
            return None

        # To指定で税理士さんにメンション
        to_line = ""
        if self.accountant_account_id:
            to_line = f"[To:{self.accountant_account_id}]\n"

        lines = [
            to_line + f"[info][title]{period} 請求書共有のお知らせ[/title]",
            f"{period}分の請求書をGoogle Driveの共有フォルダにアップロードいたしました。",
            f"ご確認をお願いいたします。",
            "",
        ]

        if card_count > 0:
            lines.append(f"■ クレジットカード: {card_count}件")
            if card_folder_url:
                lines.append(f"  {card_folder_url}")

        if bank_count > 0:
            lines.append(f"■ 銀行振込: {bank_count}件")
            if bank_folder_url:
                lines.append(f"  {bank_folder_url}")

        total = card_count + bank_count
        lines.append(f"\n合計: {total}件")
        lines.append("[/info]")

        message = "\n".join(lines)
        return self.send_message(message)


# シングルトンインスタンス
chatwork_service = ChatworkService()
