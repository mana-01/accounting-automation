"""Google Spreadsheet service for data storage."""

import os
import json
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..models import (
    Subscription,
    EmailRule,
    Invoice,
    ReconciliationResult,
    ReminderItem,
    ReminderCategory,
    SHEET_NAMES,
    SHEET_HEADERS,
)


def get_google_credentials(scopes: list[str]):
    """Google認証情報を取得（複数の方法に対応）"""
    # 方法1: GOOGLE_CREDENTIALS_JSON 環境変数（Vercel用）
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(
            creds_dict, scopes=scopes
        )

    # 方法2: credentials.json ファイル
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
    if creds_path and os.path.exists(creds_path):
        return service_account.Credentials.from_service_account_file(
            creds_path, scopes=scopes
        )

    # 方法3: 個別の環境変数
    return service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
            "private_key": os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=scopes
    )


class SpreadsheetService:
    """Google Spreadsheet をデータベースとして使用するサービス"""

    def __init__(self):
        self.spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "")
        self.service = self._build_service()

    def _build_service(self):
        """Google Sheets API サービスを構築"""
        credentials = get_google_credentials(
            ["https://www.googleapis.com/auth/spreadsheets"]
        )
        return build("sheets", "v4", credentials=credentials)

    async def initialize(self) -> None:
        """スプレッドシートを初期化（必要なシートとヘッダーを作成）"""
        try:
            spreadsheet = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()

            existing_sheets = [s["properties"]["title"] for s in spreadsheet.get("sheets", [])]

            for sheet_name in SHEET_NAMES.values():
                if sheet_name not in existing_sheets:
                    # シートを追加
                    self.service.spreadsheets().batchUpdate(
                        spreadsheetId=self.spreadsheet_id,
                        body={
                            "requests": [{
                                "addSheet": {
                                    "properties": {"title": sheet_name}
                                }
                            }]
                        }
                    ).execute()

                    # ヘッダーを追加
                    headers = SHEET_HEADERS.get(sheet_name, [])
                    if headers:
                        self.service.spreadsheets().values().update(
                            spreadsheetId=self.spreadsheet_id,
                            range=f"{sheet_name}!A1",
                            valueInputOption="RAW",
                            body={"values": [headers]}
                        ).execute()

            print("Spreadsheet initialized successfully")
        except Exception as e:
            print(f"Failed to initialize spreadsheet: {e}")
            raise

    def _read_sheet(self, sheet_name: str) -> list[list[str]]:
        """シートの全データを読み込む"""
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A:Z"
        ).execute()

        rows = result.get("values", [])
        if len(rows) < 2:
            return []

        return rows[1:]  # ヘッダー行を除く

    def _append_rows(self, sheet_name: str, rows: list[list]) -> None:
        """シートに行を追加"""
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A:Z",
            valueInputOption="RAW",
            body={"values": rows}
        ).execute()

    def _update_row(self, sheet_name: str, row_index: int, row_data: list) -> None:
        """特定の行を更新"""
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A{row_index + 2}",  # +2: ヘッダー行 + 0-index
            valueInputOption="RAW",
            body={"values": [row_data]}
        ).execute()

    # ===== Subscription methods =====

    def get_subscriptions(self, active_only: bool = False) -> list[Subscription]:
        """サブスクリプション一覧を取得"""
        rows = self._read_sheet(SHEET_NAMES["SUBSCRIPTIONS"])
        subscriptions = [Subscription.from_row(row) for row in rows if row]

        if active_only:
            return [s for s in subscriptions if s.is_active]
        return subscriptions

    def get_subscription_by_id(self, subscription_id: str) -> Optional[Subscription]:
        """IDでサブスクリプションを取得"""
        subscriptions = self.get_subscriptions()
        for s in subscriptions:
            if s.id == subscription_id:
                return s
        return None

    def add_subscription(self, subscription: Subscription) -> None:
        """サブスクリプションを追加"""
        self._append_rows(SHEET_NAMES["SUBSCRIPTIONS"], [subscription.to_row()])

    def update_subscription(self, subscription_id: str, **updates) -> None:
        """サブスクリプションを更新"""
        rows = self._read_sheet(SHEET_NAMES["SUBSCRIPTIONS"])
        for i, row in enumerate(rows):
            if row and row[0] == subscription_id:
                subscription = Subscription.from_row(row)
                for key, value in updates.items():
                    if hasattr(subscription, key):
                        setattr(subscription, key, value)
                subscription.updated_at = __import__("datetime").datetime.now().isoformat()
                self._update_row(SHEET_NAMES["SUBSCRIPTIONS"], i, subscription.to_row())
                return
        raise ValueError(f"Subscription not found: {subscription_id}")

    # ===== Email Rule methods =====

    def get_email_rules(self, active_only: bool = True) -> list[EmailRule]:
        """メール取得ルール一覧を取得"""
        rows = self._read_sheet(SHEET_NAMES["EMAIL_RULES"])
        rules = [EmailRule.from_row(row) for row in rows if row]

        if active_only:
            return [r for r in rules if r.is_active]
        return rules

    def add_email_rule(self, rule: EmailRule) -> None:
        """メール取得ルールを追加"""
        self._append_rows(SHEET_NAMES["EMAIL_RULES"], [rule.to_row()])

    # ===== Invoice methods =====

    def get_invoices(self, period: Optional[str] = None) -> list[Invoice]:
        """請求書一覧を取得"""
        rows = self._read_sheet(SHEET_NAMES["INVOICES"])
        invoices = [Invoice.from_row(row) for row in rows if row]

        if period:
            return [i for i in invoices if self._format_period(i.invoice_date) == period]
        return invoices

    def add_invoice(self, invoice: Invoice) -> None:
        """請求書を追加"""
        self._append_rows(SHEET_NAMES["INVOICES"], [invoice.to_row()])

    def update_invoice_status(self, invoice_id: str, status: str) -> None:
        """請求書のステータスを更新"""
        rows = self._read_sheet(SHEET_NAMES["INVOICES"])
        for i, row in enumerate(rows):
            if row and row[0] == invoice_id:
                invoice = Invoice.from_row(row)
                invoice.status = __import__("..models", fromlist=["InvoiceStatus"]).InvoiceStatus(status)
                self._update_row(SHEET_NAMES["INVOICES"], i, invoice.to_row())
                return
        raise ValueError(f"Invoice not found: {invoice_id}")

    # ===== Reconciliation methods =====

    def get_reconciliation_history(self) -> list[ReconciliationResult]:
        """照会履歴を取得"""
        rows = self._read_sheet(SHEET_NAMES["RECONCILIATION_HISTORY"])
        results = []
        for row in rows:
            if row:
                # 簡易的なパース（完全な変換は省略）
                result = ReconciliationResult(
                    id=row[0],
                    reconciliation_date=row[1],
                    period=row[2],
                    total_transactions=int(row[3]) if row[3] else 0,
                    matched_count=int(row[4]) if row[4] else 0,
                    unmatched_count=int(row[5]) if row[5] else 0,
                    missing_invoices=[],  # JSONパースは省略
                    created_at=row[8] if len(row) > 8 else "",
                )
                results.append(result)
        return results

    def save_reconciliation_result(self, result: ReconciliationResult) -> None:
        """照会結果を保存"""
        self._append_rows(SHEET_NAMES["RECONCILIATION_HISTORY"], [result.to_row()])

    # ===== Reminder Item methods =====

    def get_reminder_items(self, active_only: bool = True, category: str = None) -> list[ReminderItem]:
        """リマインド項目一覧を取得"""
        rows = self._read_sheet(SHEET_NAMES["REMINDER_ITEMS"])
        items = [ReminderItem.from_row(row) for row in rows if row]

        if active_only:
            items = [i for i in items if i.is_active]
        if category:
            items = [i for i in items if i.category.value == category]
        return items

    def add_reminder_item(self, item: ReminderItem) -> None:
        """リマインド項目を追加"""
        self._append_rows(SHEET_NAMES["REMINDER_ITEMS"], [item.to_row()])

    def delete_reminder_item(self, item_id: str) -> None:
        """リマインド項目を削除（is_active=Falseに設定）"""
        rows = self._read_sheet(SHEET_NAMES["REMINDER_ITEMS"])
        for i, row in enumerate(rows):
            if row and row[0] == item_id:
                item = ReminderItem.from_row(row)
                item.is_active = False
                self._update_row(SHEET_NAMES["REMINDER_ITEMS"], i, item.to_row())
                return
        raise ValueError(f"Reminder item not found: {item_id}")

    # ===== Utility methods =====

    def _format_period(self, date_string: str) -> str:
        """日付文字列を期間形式に変換 (例: "2026年1月")"""
        from datetime import datetime
        try:
            date = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
            return f"{date.year}年{date.month}月"
        except:
            return ""

    @staticmethod
    def get_current_period() -> str:
        """現在の期間を取得"""
        from datetime import datetime
        now = datetime.now()
        return f"{now.year}年{now.month}月"


# シングルトンインスタンス
spreadsheet_service = SpreadsheetService()
