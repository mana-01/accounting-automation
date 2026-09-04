"""Reconciliation service for matching transactions with invoices."""

from datetime import datetime
from typing import Optional
import uuid

from ..models import (
    Transaction,
    Invoice,
    Subscription,
    MissingInvoice,
    ReconciliationResult,
    ReconciliationStatus,
    InvoiceStatus,
)
from .spreadsheet import spreadsheet_service


class ReconciliationService:
    """明細と請求書を照会するサービス"""

    def __init__(self):
        self.amount_tolerance = 1.0  # 金額の許容誤差（円）
        self.date_tolerance_days = 5  # 日付の許容誤差（日）

    def reconcile(
        self,
        transactions: list[Transaction],
        period: Optional[str] = None
    ) -> ReconciliationResult:
        """取引明細と請求書・サブスクを照会（重複取引は自動スキップ）"""

        if not period:
            period = spreadsheet_service.get_current_period()

        # 重複チェック: 既存取引を除外して差分のみ処理
        new_transactions = spreadsheet_service.filter_new_transactions(transactions)
        skipped_count = len(transactions) - len(new_transactions)

        # データを取得
        subscriptions = spreadsheet_service.get_subscriptions(active_only=True)
        invoices = spreadsheet_service.get_invoices(period=period)

        # 照会結果
        matched_count = 0
        unmatched_transactions: list[Transaction] = []
        missing_invoices: list[MissingInvoice] = []

        for tx in new_transactions:
            match_result = self._find_match(tx, subscriptions, invoices)

            if match_result:
                tx.matched_subscription_id = match_result.get("subscription_id")
                tx.matched_invoice_id = match_result.get("invoice_id")
                matched_count += 1

                # 請求書のステータスを更新
                if match_result.get("invoice_id"):
                    try:
                        spreadsheet_service.update_invoice_status(
                            match_result["invoice_id"],
                            InvoiceStatus.MATCHED.value
                        )
                    except:
                        pass
            else:
                unmatched_transactions.append(tx)

                # 不足請求書として記録
                missing_invoices.append(MissingInvoice(
                    transaction_date=tx.date,
                    description=tx.description,
                    amount=tx.amount,
                    suggested_vendor=self._suggest_vendor(tx.description, subscriptions),
                    status="pending"
                ))

        # 新規取引をスプレッドシートに記録（次回以降の重複防止）
        if new_transactions:
            spreadsheet_service.save_csv_transactions(new_transactions)

        # 結果を作成
        result = ReconciliationResult(
            id=str(uuid.uuid4()),
            reconciliation_date=datetime.now().strftime("%Y-%m-%d"),
            period=period,
            total_transactions=len(new_transactions),
            matched_count=matched_count,
            unmatched_count=len(unmatched_transactions),
            missing_invoices=missing_invoices,
            skipped_count=skipped_count,
            status=(
                ReconciliationStatus.COMPLETED
                if not missing_invoices
                else ReconciliationStatus.PENDING_INVOICES
            ),
            created_at=datetime.now().isoformat()
        )

        # 結果を保存（新規取引がある場合のみ）
        if new_transactions:
            spreadsheet_service.save_reconciliation_result(result)

        return result

    def _find_match(
        self,
        transaction: Transaction,
        subscriptions: list[Subscription],
        invoices: list[Invoice]
    ) -> Optional[dict]:
        """取引に対応するサブスク・請求書を検索"""

        # subscription_id -> Subscription マップ（請求書のvendor解決用）
        sub_by_id = {s.id: s for s in subscriptions}

        # 1. まず請求書との完全マッチを試みる
        for invoice in invoices:
            if self._match_invoice(transaction, invoice, sub_by_id):
                return {
                    "subscription_id": invoice.subscription_id,
                    "invoice_id": invoice.id,
                    "match_type": "invoice"
                }

        # 2. サブスクとのマッチを試みる
        for subscription in subscriptions:
            if self._match_subscription(transaction, subscription):
                return {
                    "subscription_id": subscription.id,
                    "invoice_id": None,
                    "match_type": "subscription"
                }

        return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """日付文字列を %Y/%m/%d または %Y-%m-%d でパース"""
        if not date_str:
            return None
        for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    def _match_invoice(
        self,
        transaction: Transaction,
        invoice: Invoice,
        sub_by_id: dict
    ) -> bool:
        """取引と請求書がマッチするかチェック

        - 金額が一致 + 日付が許容範囲内 → マッチ
        - 日付が許容範囲内 + 取引説明にvendor名が含まれる → マッチ（金額不一致でも重複扱い）
        """
        tx_date = self._parse_date(transaction.date)
        inv_date = self._parse_date(invoice.invoice_date)
        date_close = (
            tx_date is not None
            and inv_date is not None
            and abs((tx_date - inv_date).days) <= self.date_tolerance_days
        )

        amount_matches = abs(transaction.amount - invoice.amount) <= self.amount_tolerance

        if amount_matches and date_close:
            return True

        if date_close:
            sub = sub_by_id.get(invoice.subscription_id)
            desc_lower = transaction.description.lower()
            candidates = [invoice.subscription_name]
            if sub:
                candidates.extend([sub.vendor, sub.name])
            for cand in candidates:
                if not cand:
                    continue
                cand_lower = cand.lower()
                if cand_lower and cand_lower in desc_lower:
                    return True

        return False

    def _match_subscription(self, transaction: Transaction, subscription: Subscription) -> bool:
        """取引とサブスクがマッチするかチェック"""
        # 金額チェック
        if abs(transaction.amount - subscription.amount) > self.amount_tolerance:
            return False

        # 名前チェック（部分一致）
        desc_lower = transaction.description.lower()
        name_lower = subscription.name.lower()
        vendor_lower = subscription.vendor.lower()

        if name_lower in desc_lower or vendor_lower in desc_lower:
            return True

        # キーワードマッチ
        keywords = self._extract_keywords(subscription.name) + self._extract_keywords(subscription.vendor)
        for keyword in keywords:
            if keyword.lower() in desc_lower:
                return True

        return False

    def _suggest_vendor(
        self,
        description: str,
        subscriptions: list[Subscription]
    ) -> Optional[str]:
        """取引説明から推測されるベンダーを提案"""
        desc_lower = description.lower()

        # 既知のサブスクとの部分一致をチェック
        for sub in subscriptions:
            keywords = self._extract_keywords(sub.name) + self._extract_keywords(sub.vendor)
            for keyword in keywords:
                if keyword.lower() in desc_lower:
                    return sub.vendor

        # よくあるサービス名のパターン
        common_patterns = {
            "aws": "Amazon Web Services",
            "amazon": "Amazon",
            "google": "Google",
            "microsoft": "Microsoft",
            "adobe": "Adobe",
            "notion": "Notion",
            "slack": "Slack",
            "figma": "Figma",
            "github": "GitHub",
            "netlify": "Netlify",
            "vercel": "Vercel",
            "heroku": "Heroku",
            "dropbox": "Dropbox",
            "zoom": "Zoom",
        }

        for pattern, vendor in common_patterns.items():
            if pattern in desc_lower:
                return vendor

        return None

    def _extract_keywords(self, text: str) -> list[str]:
        """テキストからキーワードを抽出"""
        # 記号で分割
        import re
        words = re.split(r"[\s\-_/\\()（）【】\[\]]", text)
        # 2文字以上の単語のみ
        return [w for w in words if len(w) >= 2]

    def generate_missing_invoices_report(
        self,
        missing_invoices: list[MissingInvoice]
    ) -> str:
        """不足請求書のレポートを生成"""
        if not missing_invoices:
            return "✅ すべての取引に対応する請求書が揃っています！"

        report_lines = [
            "📋 *以下の請求書が不足しています:*",
            ""
        ]

        for i, mi in enumerate(missing_invoices, 1):
            vendor_info = f" ({mi.suggested_vendor})" if mi.suggested_vendor else ""
            report_lines.append(
                f"{i}. `{mi.transaction_date}` - {mi.description}{vendor_info}"
            )
            report_lines.append(f"   💰 ¥{mi.amount:,.0f}")
            report_lines.append("")

        report_lines.append("---")
        report_lines.append("これらの請求書を準備してアップロードしてください。")

        return "\n".join(report_lines)


# シングルトンインスタンス
reconciliation_service = ReconciliationService()
