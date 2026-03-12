"""Google Spreadsheet service for data storage."""

import os
import json
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..models import (
    Subscription,
    SubscriptionRule,
    Invoice,
    Transaction,
    ReconciliationResult,
    SHEET_NAMES,
    SHEET_HEADERS,
)


def get_google_credentials(scopes: list[str]):
    """Google認証情報を取得（複数の方法に対応）"""
    credentials = None

    # 方法1: GOOGLE_CREDENTIALS_JSON 環境変数（Vercel用）
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=scopes
        )

    # 方法2: credentials.json ファイル
    if credentials is None:
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
        if creds_path and os.path.exists(creds_path):
            credentials = service_account.Credentials.from_service_account_file(
                creds_path, scopes=scopes
            )

    # 方法3: 個別の環境変数
    if credentials is None:
        credentials = service_account.Credentials.from_service_account_info(
            {
                "type": "service_account",
                "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
                "private_key": os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n"),
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            scopes=scopes
        )

    # ドメイン全体の委任: 指定ユーザーとして操作する
    delegate_email = os.getenv("GOOGLE_DELEGATE_EMAIL")
    if delegate_email:
        credentials = credentials.with_subject(delegate_email)

    return credentials


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

    # ===== Subscription Master methods =====

    def get_subscriptions(self, active_only: bool = False) -> list[Subscription]:
        """サブスクリプション一覧を取得"""
        rows = self._read_sheet(SHEET_NAMES["SUBSCRIPTION_MASTER"])
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
        self._append_rows(SHEET_NAMES["SUBSCRIPTION_MASTER"], [subscription.to_row()])

    def update_subscription(self, subscription_id: str, **updates) -> None:
        """サブスクリプションを更新"""
        rows = self._read_sheet(SHEET_NAMES["SUBSCRIPTION_MASTER"])
        for i, row in enumerate(rows):
            if row and row[0] == subscription_id:
                subscription = Subscription.from_row(row)
                for key, value in updates.items():
                    if hasattr(subscription, key):
                        setattr(subscription, key, value)
                subscription.updated_at = __import__("datetime").datetime.now().isoformat()
                self._update_row(SHEET_NAMES["SUBSCRIPTION_MASTER"], i, subscription.to_row())
                return
        raise ValueError(f"Subscription not found: {subscription_id}")

    # ===== Subscription (取得ルール) methods =====

    def get_subscriptions(self, active_only: bool = True, category: str = None) -> list[SubscriptionRule]:
        """取得ルール一覧を取得（カテゴリでフィルタ可能）"""
        rows = self._read_sheet(SHEET_NAMES["SUBSCRIPTIONS"])
        rules = [SubscriptionRule.from_row(row) for row in rows if row]

        if active_only:
            rules = [r for r in rules if r.is_active]
        if category:
            rules = [r for r in rules if r.category.value == category]
        return rules

    def add_subscription_rule(self, rule: SubscriptionRule) -> None:
        """取得ルールを追加"""
        self._append_rows(SHEET_NAMES["SUBSCRIPTIONS"], [rule.to_row()])

    def delete_subscription_rule(self, rule_name: str) -> None:
        """取得ルールを削除（is_active=falseに設定）"""
        rows = self._read_sheet(SHEET_NAMES["SUBSCRIPTIONS"])
        for i, row in enumerate(rows):
            if row and row[0] == rule_name:
                rule = SubscriptionRule.from_row(row)
                rule.is_active = False
                self._update_row(SHEET_NAMES["SUBSCRIPTIONS"], i, rule.to_row())
                return
        raise ValueError(f"Subscription rule not found: {rule_name}")

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

    # ===== CSV Transaction methods =====

    def get_existing_csv_transactions(self) -> set[tuple[str, str]]:
        """既存のCSV取引キー (date, description) のセットを返す"""
        rows = self._read_sheet(SHEET_NAMES["CSV_TRANSACTIONS"])
        keys = set()
        for row in rows:
            if row and len(row) >= 2:
                keys.add((row[0], row[1]))
        return keys

    def save_csv_transactions(self, transactions: list[Transaction]) -> None:
        """CSV取引をスプレッドシートに保存"""
        from datetime import datetime
        now = datetime.now().isoformat()
        rows = [
            [tx.date, tx.description, str(tx.amount), tx.transaction_type, now]
            for tx in transactions
        ]
        if rows:
            self._append_rows(SHEET_NAMES["CSV_TRANSACTIONS"], rows)

    def filter_new_transactions(self, transactions: list[Transaction]) -> list[Transaction]:
        """既存の取引と重複しないものだけを返す（日付+説明で判定）"""
        existing_keys = self.get_existing_csv_transactions()
        new_transactions = []
        for tx in transactions:
            key = (tx.date, tx.description)
            if key not in existing_keys:
                new_transactions.append(tx)
        return new_transactions

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
