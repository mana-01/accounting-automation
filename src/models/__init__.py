"""Data models for the accounting automation system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class BillingCycle(Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"
    QUARTERLY = "quarterly"


class PaymentMethod(Enum):
    CARD = "card"
    BANK = "bank"


class FetchMethod(Enum):
    EMAIL_PDF = "email_pdf"
    EMAIL_LINK = "email_link"
    LOGIN = "login"
    MANUAL = "manual"


class FileNamingRule(Enum):
    RENAME = "rename"      # {date}_{vendor}_{amount}.pdf に命名
    ORIGINAL = "original"  # 添付ファイルの元ファイル名をそのまま使用


class InvoiceStatus(Enum):
    PENDING = "pending"
    MATCHED = "matched"
    MISSING = "missing"


class ReconciliationStatus(Enum):
    COMPLETED = "completed"
    PENDING_INVOICES = "pending_invoices"


class SubscriptionCategory(Enum):
    EMAIL = "email"            # メールで自動取得
    MANUAL = "manual"          # ②手動確認
    SCAN = "scan"              # ③固定スキャン


@dataclass
class Subscription:
    """サブスクリプション（定期支払い）の定義"""
    name: str
    vendor: str
    amount: float
    currency: str = "JPY"
    billing_day: int = 1  # 請求日 (1-31)
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    payment_method: PaymentMethod = PaymentMethod.CARD
    fetch_method: FetchMethod = FetchMethod.MANUAL
    file_naming: FileNamingRule = FileNamingRule.RENAME  # ファイル命名ルール
    email_subject: Optional[str] = None  # メール件名パターン
    sender_email: Optional[str] = None  # 送信者メールアドレス
    login_url: Optional[str] = None
    is_active: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> list:
        """Spreadsheet用の行データに変換"""
        return [
            self.id,
            self.name,
            self.vendor,
            str(self.amount),
            self.currency,
            str(self.billing_day),
            self.billing_cycle.value,
            self.payment_method.value,
            self.fetch_method.value,
            self.file_naming.value,
            self.email_subject or "",
            self.sender_email or "",
            self.login_url or "",
            str(self.is_active),
            self.created_at,
            self.updated_at,
        ]

    @classmethod
    def from_row(cls, row: list) -> "Subscription":
        """Spreadsheetの行データから作成"""
        return cls(
            id=row[0],
            name=row[1],
            vendor=row[2],
            amount=float(row[3]) if row[3] else 0,
            currency=row[4] or "JPY",
            billing_day=int(row[5]) if row[5] else 1,
            billing_cycle=BillingCycle(row[6]) if row[6] else BillingCycle.MONTHLY,
            payment_method=PaymentMethod(row[7]) if row[7] else PaymentMethod.CARD,
            fetch_method=FetchMethod(row[8]) if row[8] else FetchMethod.MANUAL,
            file_naming=FileNamingRule(row[9]) if len(row) > 9 and row[9] and row[9] in ("rename", "original") else FileNamingRule.RENAME,
            email_subject=row[10] if len(row) > 10 and row[10] else None,
            sender_email=row[11] if len(row) > 11 and row[11] else None,
            login_url=row[12] if len(row) > 12 and row[12] else None,
            is_active=row[13].lower() == "true" if len(row) > 13 and row[13] else True,
            created_at=row[14] if len(row) > 14 else datetime.now().isoformat(),
            updated_at=row[15] if len(row) > 15 else datetime.now().isoformat(),
        )


@dataclass
class SubscriptionRule:
    """請求書取得ルール（メール自動取得 / 手動確認 / スキャン）

    subscriptions シートのカラム対応:
      A: name, B: category, C: sender_email, D: subject_pattern,
      E: fetch_type, F: url, G: notes, H: link_selector, I: is_active
    """
    name: str
    category: SubscriptionCategory = SubscriptionCategory.EMAIL
    sender_email: str = ""          # メール自動取得: 送信元メールアドレス
    subject_pattern: str = ""       # メール自動取得: 件名パターン
    fetch_type: str = "attachment"  # メール自動取得: "attachment" or "link"
    url: str = ""                   # 手動確認: 確認先URL
    notes: str = ""                 # 手動確認/スキャン: 備考
    link_selector: Optional[str] = None  # メール自動取得: CSSセレクタ
    is_active: bool = True

    def to_row(self) -> list:
        """subscriptions シートの1行に変換 (A-I)"""
        return [
            self.name,                    # A: name
            self.category.value,          # B: category
            self.sender_email,            # C: sender_email
            self.subject_pattern,         # D: subject_pattern
            self.fetch_type,              # E: fetch_type
            self.url,                     # F: url
            self.notes,                   # G: notes
            self.link_selector or "",     # H: link_selector
            str(self.is_active).lower(),  # I: is_active
        ]

    @classmethod
    def from_row(cls, row: list) -> "SubscriptionRule":
        """subscriptions シートの行から生成 (A-I)"""
        return cls(
            name=row[0] if len(row) > 0 else "",              # A
            category=SubscriptionCategory(row[1]) if len(row) > 1 and row[1] else SubscriptionCategory.EMAIL,  # B
            sender_email=row[2] if len(row) > 2 else "",      # C
            subject_pattern=row[3] if len(row) > 3 else "",   # D
            fetch_type=row[4] if len(row) > 4 else "attachment",  # E
            url=row[5] if len(row) > 5 else "",               # F
            notes=row[6] if len(row) > 6 else "",             # G
            link_selector=row[7] if len(row) > 7 and row[7] else None,  # H
            is_active=row[8].lower() == "true" if len(row) > 8 and row[8] else True,  # I
        )


@dataclass
class Invoice:
    """請求書"""
    subscription_id: str
    subscription_name: str
    amount: float
    invoice_date: str
    currency: str = "JPY"
    due_date: Optional[str] = None
    drive_file_id: Optional[str] = None
    drive_url: Optional[str] = None
    source_type: str = "manual"  # "email", "login", "manual"
    status: InvoiceStatus = InvoiceStatus.PENDING
    invoice_type: str = "credit"  # "credit", "bank", "sales"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> list:
        return [
            self.id,
            self.subscription_id,
            self.subscription_name,
            str(self.amount),
            self.currency,
            self.invoice_date,
            self.due_date or "",
            self.drive_file_id or "",
            self.drive_url or "",
            self.source_type,
            self.status.value,
            self.invoice_type,
            self.created_at,
        ]

    @classmethod
    def from_row(cls, row: list) -> "Invoice":
        return cls(
            id=row[0],
            subscription_id=row[1],
            subscription_name=row[2],
            amount=float(row[3]) if row[3] else 0,
            currency=row[4] if len(row) > 4 else "JPY",
            invoice_date=row[5] if len(row) > 5 else "",
            due_date=row[6] if len(row) > 6 and row[6] else None,
            drive_file_id=row[7] if len(row) > 7 and row[7] else None,
            drive_url=row[8] if len(row) > 8 and row[8] else None,
            source_type=row[9] if len(row) > 9 else "manual",
            status=InvoiceStatus(row[10]) if len(row) > 10 and row[10] else InvoiceStatus.PENDING,
            invoice_type=row[11] if len(row) > 11 and row[11] else "credit",
            created_at=row[12] if len(row) > 12 else datetime.now().isoformat(),
        )


@dataclass
class Transaction:
    """銀行/カード明細の取引"""
    date: str
    description: str
    amount: float
    transaction_type: str = "debit"  # "debit" or "credit"
    category: Optional[str] = None
    memo: Optional[str] = None
    matched_invoice_id: Optional[str] = None
    matched_subscription_id: Optional[str] = None


@dataclass
class MissingInvoice:
    """不足している請求書"""
    transaction_date: str
    description: str
    amount: float
    suggested_vendor: Optional[str] = None
    status: str = "pending"  # "pending" or "resolved"


@dataclass
class ReconciliationResult:
    """照会結果"""
    reconciliation_date: str
    period: str  # "2026年1月"
    total_transactions: int
    matched_count: int
    unmatched_count: int
    missing_invoices: list[MissingInvoice] = field(default_factory=list)
    status: ReconciliationStatus = ReconciliationStatus.PENDING_INVOICES
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> list:
        import json
        missing_json = json.dumps(
            [{"date": m.transaction_date, "desc": m.description, "amount": m.amount,
              "vendor": m.suggested_vendor, "status": m.status}
             for m in self.missing_invoices],
            ensure_ascii=False
        )
        return [
            self.id,
            self.reconciliation_date,
            self.period,
            str(self.total_transactions),
            str(self.matched_count),
            str(self.unmatched_count),
            missing_json,
            self.status.value,
            self.created_at,
        ]


# Spreadsheet シート名
SHEET_NAMES = {
    "SUBSCRIPTIONS": "subscriptions",           # 取得ルール（email/manual/scan）
    "SUBSCRIPTION_MASTER": "subscription_master",  # サブスク定義（金額・支払方法等）
    "INVOICES": "invoices",
    "RECONCILIATION_HISTORY": "reconciliation_history",
    "SETTINGS": "settings",
}

# 各シートのヘッダー
# subscriptions シート（旧 email_rules）
#   A: name            サービス名（共通）
#   B: category        種別 email / manual / scan
#   C: sender_email    メール自動取得: 送信元メールアドレス
#   D: subject_pattern メール自動取得: 件名パターン
#   E: fetch_type      メール自動取得: attachment / link
#   F: url             手動確認: 確認先URL
#   G: notes           手動確認/スキャン: 備考
#   H: link_selector   メール自動取得: リンク取得時CSSセレクタ
#   I: is_active       有効フラグ true / false
SHEET_HEADERS = {
    "subscriptions": [
        "name", "category", "sender_email", "subject_pattern",
        "fetch_type", "url", "notes", "link_selector", "is_active"
    ],
    "subscription_master": [
        "id", "name", "vendor", "amount", "currency", "billing_day",
        "billing_cycle", "payment_method", "fetch_method", "file_naming",
        "email_subject", "sender_email", "login_url", "is_active", "created_at", "updated_at"
    ],
    "invoices": [
        "id", "subscription_id", "subscription_name", "amount", "currency",
        "invoice_date", "due_date", "drive_file_id", "drive_url", "source_type",
        "status", "invoice_type", "created_at"
    ],
    "reconciliation_history": [
        "id", "reconciliation_date", "period", "total_transactions",
        "matched_count", "unmatched_count", "missing_invoices_json", "status", "created_at"
    ],
}
