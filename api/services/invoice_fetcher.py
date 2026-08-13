"""Invoice fetcher service - Fetch invoices from email."""

import os
import json
import base64
import re
import csv
from datetime import datetime, timedelta
from typing import Optional
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload


def _calculate_extraction_confidence(data: dict, method: str = "gemini") -> dict:
    """
    抽出結果の信頼度スコアを計算する。
    Returns: {"score": float (0.0-1.0), "level": str, "details": list[str]}
    """
    score = 0.0
    details = []

    amount = data.get("amount")
    vendor = data.get("vendor")
    date = data.get("date")

    # 基本スコア: 抽出方法
    if method == "gemini":
        score += 0.1  # Geminiベースライン
        details.append("Gemini API使用")
    else:
        details.append("正規表現フォールバック使用")

    # 金額 (最大 0.35)
    if amount is not None:
        score += 0.25
        details.append(f"金額: ¥{amount:,}")
        if 100 <= amount <= 10_000_000:
            score += 0.10
        else:
            details.append("金額が想定範囲外(¥100〜¥10,000,000)")
    else:
        details.append("金額: 抽出失敗")

    # 請求元 (最大 0.30)
    if vendor:
        score += 0.20
        details.append(f"請求元: {vendor}")
        # 有意味な名前かどうかチェック
        if len(vendor) >= 2 and vendor not in ("unknown", "不明", "null", "None"):
            score += 0.10
        else:
            details.append("請求元名が不明確")
    else:
        details.append("請求元: 抽出失敗")

    # 日付 (最大 0.25)
    if date:
        score += 0.15
        details.append(f"日付: {date}")
        # YYYY-MM-DD形式の妥当性チェック
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            try:
                parsed = datetime.strptime(date, "%Y-%m-%d")
                # 未来すぎる日付や古すぎる日付をチェック
                if datetime(2020, 1, 1) <= parsed <= datetime.now() + timedelta(days=60):
                    score += 0.10
                else:
                    details.append("日付が想定範囲外")
            except ValueError:
                details.append("日付のパース失敗")
        else:
            details.append("日付形式が不正（YYYY-MM-DD以外）")
    else:
        details.append("日付: 抽出失敗")

    # 信頼度レベル判定
    if score >= 0.80:
        level = "high"
    elif score >= 0.50:
        level = "medium"
    else:
        level = "low"

    return {
        "score": round(score, 2),
        "level": level,
        "details": details,
    }


def extract_invoice_data_with_gemini(pdf_data: bytes) -> dict:
    """
    Gemini APIを使ってPDFから請求書データを構造化抽出する
    Returns: {"amount": int|None, "vendor": str|None, "date": str|None, "summary": str|None,
              "confidence": {"score": float, "level": str, "details": list[str]},
              "extraction_method": str}
    """
    try:
        import google.generativeai as genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("GEMINI_API_KEY not set, falling back to regex extraction")
            return _extract_amount_from_pdf_regex(pdf_data)

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        # PDFをbase64エンコードしてGeminiに送信
        pdf_part = {
            "mime_type": "application/pdf",
            "data": pdf_data,
        }

        prompt = """あなたは経理書類の読み取り専門AIです。
このPDFは請求書・領収書・レシート・明細書のいずれかです。
以下の情報をJSON形式で正確に抽出してください。
必ず以下のJSON形式のみで回答してください。説明文は不要です。

【金額の抽出ルール（最重要）】
- "amount" には最終的な支払い総額（税込）を整数で入れてください。
- 以下の優先順で金額を探してください:
  1. 「ご請求金額」「お支払い金額」「請求金額」「合計金額」「税込合計」「領収金額」
  2. 「Total」「Amount Due」「Grand Total」
  3. 上記が見つからない場合、文書内で最も大きい金額（税込と思われるもの）
- ⚠️「小計」「税抜金額」「消費税額」「値引き前金額」など途中の計算金額は絶対に使わないでください。
- 例: 小計 10,000円 / 消費税 1,000円 / 合計 11,000円 → amount は 11000

【日付の抽出ルール（重要）】
- "date" にはこの書類に記載されている日付を入れてください。
- 以下の優先順で探してください:
  1. 「発行日」「請求日」「領収日」「ご利用日」「取引日」
  2. 「Date」「Invoice Date」「Receipt Date」
  3. 書類上部に記載された日付
- ⚠️ 書類に記載された日付を抽出してください。今日の日付やアップロード日は使わないでください。
- 複数の日付がある場合は、取引日・発行日を優先してください。

【発行元の抽出ルール】
- "vendor" には請求元・発行元・店舗名を入れてください。
- ロゴ、ヘッダー、「発行者」「販売者」欄などから特定してください。
- 会社名・屋号・店舗名のみを抽出し、住所や電話番号は含めないでください。

【画像ベースPDFの場合】
- スキャンされた書類や画像ベースのPDFの場合も、画像内のテキストを読み取って抽出してください。
- 手書きの文字がある場合も可能な限り読み取ってください。

{
  "amount": 支払い総額（税込、整数、円単位、不明ならnull）,
  "vendor": "発行元の会社名・店舗名・サービス名（不明ならnull）",
  "date": "書類に記載された日付（YYYY-MM-DD形式、不明ならnull）",
  "summary": "内容の簡潔な要約（20文字以内）"
}"""

        response = model.generate_content([prompt, pdf_part])
        text = response.text.strip()

        # JSON部分を抽出（```json ... ``` で囲まれている場合に対応）
        if "```" in text:
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
            text = match.group(1) if match else text

        # コードフェンスなしでもJSON部分を抽出
        if not text.startswith("{"):
            json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            text = json_match.group(0) if json_match else "{}"

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"Failed to parse Gemini response as JSON: {text[:200]}")
            data = {}

        # 金額を整数に正規化
        amount = data.get("amount")
        if amount is not None:
            try:
                amount = int(str(amount).replace(",", "").replace("¥", "").replace("￥", ""))
            except (ValueError, TypeError):
                amount = None

        result = {
            "amount": amount,
            "vendor": data.get("vendor"),
            "date": data.get("date"),
            "summary": data.get("summary"),
            "extraction_method": "gemini",
        }

        # Geminiで金額が取れなかった場合、regexフォールバックで補完
        if result["amount"] is None:
            try:
                regex_result = _extract_amount_from_pdf_regex(pdf_data)
                if regex_result.get("amount") is not None:
                    result["amount"] = regex_result["amount"]
                    result["extraction_method"] = "gemini+regex"
                    print(f"Gemini amount was null, regex fallback found: {result['amount']}")
            except Exception as regex_err:
                print(f"Regex fallback also failed: {regex_err}")

        result["confidence"] = _calculate_extraction_confidence(result, method="gemini")
        return result

    except Exception as e:
        print(f"Error extracting invoice data with Gemini: {e}")
        # フォールバック: 正規表現ベースの抽出
        return _extract_amount_from_pdf_regex(pdf_data)


def _extract_amount_from_pdf_regex(pdf_data: bytes) -> dict:
    """
    正規表現ベースのフォールバック抽出（Gemini APIが使えない場合）
    Returns: {"amount": int|None, "vendor": None, "date": None, "summary": None,
              "confidence": {...}, "extraction_method": "regex"}
    """
    try:
        import pdfplumber

        with pdfplumber.open(BytesIO(pdf_data)) as pdf:
            text = ""
            for page in pdf.pages[:3]:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"

        if not text:
            result = {"amount": None, "vendor": None, "date": None, "summary": None,
                      "extraction_method": "regex"}
            result["confidence"] = _calculate_extraction_confidence(result, method="regex")
            return result

        # 小計に紐づく金額を事前に収集して除外リストを作成
        subtotal_amounts = set()
        subtotal_pattern = r'小計[:\s]*(?:JPY|[¥￥])?\s*([\d,]+)\s*(?:円)?'
        for match in re.findall(subtotal_pattern, text):
            try:
                subtotal_amounts.add(int(match.replace(",", "")))
            except ValueError:
                pass

        # 優先度順のパターン（上位パターンでマッチした金額を優先）
        prioritized_patterns = [
            r'(?:ご請求金額|お支払い?金額|請求金額|合計金額|ご利用金額|総額)[:\s]*(?:JPY|[¥￥])?\s*([\d,]+)\s*(?:円)?',
            r'(?:Total|Amount\s*Due|Grand\s*Total)[:\s]*(?:JPY|[¥￥$])?\s*([\d,]+)',
            r'(?<!小)計[:\s]*(?:JPY|[¥￥])?\s*([\d,]+)\s*(?:円)?',
        ]
        generic_patterns = [
            r'(?:JPY|[¥￥])\s*([\d,]{4,})',
            r'([\d,]{5,})\s*円',
        ]

        # まず優先パターンで合計金額を探す
        priority_amounts = []
        for pattern in prioritized_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    amt = int(match.replace(",", ""))
                    if 100 <= amt <= 10000000 and amt not in subtotal_amounts:
                        priority_amounts.append(amt)
                except ValueError:
                    continue

        # 優先パターンで見つかればそちらを使用、なければ汎用パターンにフォールバック
        if priority_amounts:
            amounts = priority_amounts
        else:
            amounts = []
            for pattern in generic_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    try:
                        amt = int(match.replace(",", ""))
                        if 100 <= amt <= 10000000 and amt not in subtotal_amounts:
                            amounts.append(amt)
                    except ValueError:
                        continue

        amount = None
        if amounts:
            from collections import Counter
            count = Counter(amounts)
            most_common = count.most_common(1)
            if most_common:
                amount = most_common[0][0]

        result = {"amount": amount, "vendor": None, "date": None, "summary": None,
                  "extraction_method": "regex"}
        result["confidence"] = _calculate_extraction_confidence(result, method="regex")
        return result

    except Exception as e:
        print(f"Error in regex PDF extraction: {e}")
        result = {"amount": None, "vendor": None, "date": None, "summary": None,
                  "extraction_method": "regex"}
        result["confidence"] = _calculate_extraction_confidence(result, method="regex")
        return result


def extract_amount_from_pdf(pdf_data: bytes) -> Optional[int]:
    """後方互換: 金額のみ返す"""
    result = extract_invoice_data_with_gemini(pdf_data)
    return result.get("amount")


def _sanitize_for_filename(text: str) -> str:
    """ファイル名に使えない文字を除去・置換する"""
    if not text:
        return "unknown"
    # ファイルシステムで問題になる文字を除去
    sanitized = re.sub(r'[\\/:*?"<>|]', '', text)
    # 前後の空白を除去
    sanitized = sanitized.strip()
    return sanitized or "unknown"


def format_invoice_filename(date: str, vendor: str, amount) -> str:
    """
    請求書ファイル名を命名ルールに従って生成する。
    フォーマット: {請求日}_{請求元}_{金額}.pdf
    例: 2026-02-01_AWS_15000.pdf
    """
    safe_date = date or "unknown-date"
    safe_vendor = _sanitize_for_filename(vendor)
    safe_amount = str(amount) if amount else "0"
    return f"{safe_date}_{safe_vendor}_{safe_amount}.pdf"


def analyze_original_filename(filename: str) -> dict:
    """
    元のファイル名が既に構造化された請求書情報を含んでいるか分析する。
    ファイル名をリネームすべきか、そのままにすべきかの判定材料を返す。

    Returns: {
        "has_date": bool,
        "has_amount": bool,
        "has_vendor_like": bool,
        "already_structured": bool,  # リネーム不要と判定
        "reason": str,
    }
    """
    name = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE)

    has_date = False
    has_amount = False
    has_vendor_like = False

    # 日付パターン検出
    date_patterns = [
        r'\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}',  # 2026-02-01, 2026.02.01, 2026年2月1日
        r'\d{8}',                                 # 20260201
        r'\d{4}[-/]\d{1,2}',                     # 2026-02, 2026/02
    ]
    for pat in date_patterns:
        if re.search(pat, name):
            has_date = True
            break

    # 金額パターン検出
    amount_patterns = [
        r'[¥￥]\s*[\d,]+',            # ¥15,000
        r'[\d,]{4,}\s*円',            # 15,000円
        r'_[\d,]{4,}(?:\.|$|_)',      # _15000. or _15000_ (ファイル名内の金額)
    ]
    for pat in amount_patterns:
        if re.search(pat, name):
            has_amount = True
            break

    # ベンダー名らしき要素の検出（日本語文字列やアルファベット企業名）
    # アンダースコアやハイフンで区切られた意味のある文字列
    vendor_patterns = [
        r'[a-zA-Z]{2,}',             # AWS, Google, etc.
        r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]{2,}',  # 日本語2文字以上
    ]
    for pat in vendor_patterns:
        if re.search(pat, name):
            has_vendor_like = True
            break

    # リネーム不要判定: 既に{日付}_{名前}_{金額}に近い形式
    already_structured = has_date and has_vendor_like

    if already_structured:
        reason = "ファイル名に日付と名前が含まれています"
    elif has_date:
        reason = "日付は含まれていますが、請求元が不明確です"
    elif has_vendor_like:
        reason = "名前は含まれていますが、日付が不明確です"
    else:
        reason = "構造化された情報がファイル名にありません"

    return {
        "has_date": has_date,
        "has_amount": has_amount,
        "has_vendor_like": has_vendor_like,
        "already_structured": already_structured,
        "reason": reason,
    }


def _parse_date_part(date_str: str):
    """日付文字列をYYYY-MM-DD形式に変換。無効ならNoneを返す"""
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str
    elif re.match(r'^\d{8}$', date_str):
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    elif re.match(r'^\d{6}$', date_str):
        return f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"
    return None


def parse_invoice_filename(filename: str) -> dict:
    """
    命名ルールに従ったファイル名からdate, vendor, amountを抽出する。
    フォーマット:
      3パート: {請求日}_{請求元}_{金額}.pdf
      2パート: {請求日}_{金額}.pdf（vendorはNone → Geminiで補完）
    パース戦略: 最初の要素=date, 最後の要素=amount, 中間=vendor
    """
    # 拡張子を除去
    name = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE)
    parts = name.split("_")

    if len(parts) < 2:
        return {"date": None, "vendor": None, "amount": None}

    # 2パートの場合: {日付}_{金額} → vendorはNone
    if len(parts) == 2:
        date = _parse_date_part(parts[0])
        amount = None
        try:
            parsed = int(parts[1].replace(",", ""))
            if parsed > 0:
                amount = parsed
        except (ValueError, TypeError):
            pass
        if date and amount:
            return {"date": date, "vendor": None, "amount": amount}
        return {"date": date, "vendor": None, "amount": None}

    # 3パート以上: {日付}_{ベンダー}_{金額}
    date = _parse_date_part(parts[0])
    amount_str = parts[-1]
    vendor = "_".join(parts[1:-1])

    # 金額をパース
    amount = None
    try:
        parsed = int(amount_str.replace(",", ""))
        if parsed > 0:
            amount = parsed
    except (ValueError, TypeError):
        pass

    # 日付も金額もパースできなかった場合は命名ルールに合致していない
    if date is None and amount is None:
        return {"date": None, "vendor": None, "amount": None}

    return {
        "date": date,
        "vendor": vendor if vendor else None,
        "amount": amount,
    }


def parse_period(period_str: str) -> list[dict]:
    """
    期間文字列をパースして月リストを返す
    例: "202602" -> [{"year": 2026, "month": 2, "code": "202602"}]
    例: "202509~202601" -> [{"year": 2025, "month": 9, "code": "202509"}, ...]
    """
    periods = []

    if "~" in period_str:
        # 範囲指定
        start, end = period_str.split("~")
        start_year = int(start[:4])
        start_month = int(start[4:6])
        end_year = int(end[:4])
        end_month = int(end[4:6])

        current_year = start_year
        current_month = start_month

        while (current_year < end_year) or (current_year == end_year and current_month <= end_month):
            periods.append({
                "year": current_year,
                "month": current_month,
                "code": f"{current_year}{current_month:02d}"
            })
            current_month += 1
            if current_month > 12:
                current_month = 1
                current_year += 1
    else:
        # 単月指定
        year = int(period_str[:4])
        month = int(period_str[4:6])
        periods.append({
            "year": year,
            "month": month,
            "code": period_str
        })

    return periods


def get_google_credentials(scopes: list[str]):
    """Google認証情報を取得"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON not set")

    creds_dict = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=scopes
    )

    # ドメイン全体の委任: 指定ユーザーとして操作する
    delegate_email = os.environ.get("GOOGLE_DELEGATE_EMAIL")
    if delegate_email:
        credentials = credentials.with_subject(delegate_email)

    return credentials


class InvoiceFetcher:
    """メールから請求書を取得するサービス"""

    def __init__(self):
        # 複数アカウント対応（カンマ区切り）
        # GMAIL_USER_EMAILS または GMAIL_USER_EMAIL どちらでもOK
        emails_str = os.environ.get("GMAIL_USER_EMAILS", "") or os.environ.get("GMAIL_USER_EMAIL", "")
        if emails_str:
            # カンマ区切りの場合は分割、単一の場合はそのまま
            self.gmail_users = [e.strip() for e in emails_str.split(",") if e.strip()]
        else:
            self.gmail_users = []

        self.gmail_user = self.gmail_users[0] if self.gmail_users else ""
        self.spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
        self.drive_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")

        # Google API clients (per user)
        self._gmail_clients = {}
        self._drive = None
        self._sheets = None

    def get_gmail_client(self, user_email: str):
        """指定ユーザーのGmail APIクライアントを取得"""
        if user_email not in self._gmail_clients:
            creds = get_google_credentials([
                "https://www.googleapis.com/auth/gmail.readonly"
            ])
            delegated = creds.with_subject(user_email)
            self._gmail_clients[user_email] = build("gmail", "v1", credentials=delegated)
        return self._gmail_clients[user_email]

    @property
    def gmail(self):
        """デフォルトアカウントのGmailクライアント（後方互換）"""
        return self.get_gmail_client(self.gmail_user) if self.gmail_user else None

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

    def get_subscriptions(self) -> list[dict]:
        """Spreadsheetからメール取得ルールを読み込む（category=email のみ）

        subscriptions シートのカラム (10列, A〜J):
          A:name, B:category, C:sender_email, D:subject_pattern,
          E:fetch_type, F:url, G:notes, H:link_selector,
          I:file_naming, J:is_active
        """
        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="subscriptions!A2:J100"
            ).execute()

            rows = result.get("values", [])
            rules = []
            for row in rows:
                if len(row) < 2 or not row[0]:
                    continue

                # category チェック (B列, index 1) - emailのみ対象
                category = row[1] if len(row) > 1 else "email"
                if category != "email":
                    continue

                # is_active チェック (J列, index 9)
                is_active = row[9].lower() == "true" if len(row) > 9 and row[9] else True
                if not is_active:
                    continue

                rules.append({
                    "name": row[0],                                        # A: name
                    "sender": row[2] if len(row) > 2 else "",             # C: sender_email
                    "subject_pattern": row[3] if len(row) > 3 else "",    # D: subject_pattern
                    "fetch_type": row[4] if len(row) > 4 else "attachment",  # E: fetch_type
                    "link_pattern": row[7] if len(row) > 7 else "",       # H: link_selector
                    "file_naming": row[8] if len(row) > 8 and row[8] in ("rename", "original") else "rename",  # I: file_naming
                })
            return rules
        except Exception as e:
            print(f"Error getting email rules: {e}")
            return []

    def search_emails(self, rule: dict, days_back: int = 30,
                      start_date: str = None, end_date: str = None,
                      user_email: str = None) -> list[dict]:
        """
        ルールに基づいてメールを検索
        start_date/end_date: "YYYY/MM/DD" 形式
        user_email: 検索対象のGmailアカウント（省略時はデフォルト）
        """
        query_parts = []

        if rule.get("sender"):
            query_parts.append(f"from:{rule['sender']}")

        if rule.get("subject_pattern"):
            subject = rule["subject_pattern"].replace("*", "")
            query_parts.append(f"subject:({subject})")

        # 期間指定
        if start_date and end_date:
            query_parts.append(f"after:{start_date}")
            query_parts.append(f"before:{end_date}")
        else:
            after_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
            query_parts.append(f"after:{after_date}")

        # PDF添付ありの場合
        if rule.get("fetch_type") == "attachment":
            query_parts.append("has:attachment filename:pdf")

        query = " ".join(query_parts)
        target_email = user_email or self.gmail_user
        print(f"Gmail search query ({target_email}): {query}")

        try:
            gmail_client = self.get_gmail_client(target_email)
            results = gmail_client.users().messages().list(
                userId="me",
                q=query,
                maxResults=100
            ).execute()

            messages = results.get("messages", [])
            detailed = []

            for msg in messages:
                full = gmail_client.users().messages().get(
                    userId="me",
                    id=msg["id"]
                ).execute()
                # 検索元アカウント情報を付与
                full["_gmail_user"] = target_email
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

        # メッセージに付与されたユーザー情報を使用
        user_email = message.get("_gmail_user", self.gmail_user)
        gmail_client = self.get_gmail_client(user_email)

        for part in parts:
            filename = part.get("filename", "")
            if filename.lower().endswith(".pdf"):
                attachment_id = part.get("body", {}).get("attachmentId")
                if attachment_id:
                    attachment = gmail_client.users().messages().attachments().get(
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
        return ""

    def get_message_subject(self, message: dict) -> str:
        """メッセージの件名を取得"""
        headers = message.get("payload", {}).get("headers", [])
        for h in headers:
            if h.get("name", "").lower() == "subject":
                return h.get("value", "")
        return ""

    def save_to_drive(self, file_data: bytes, filename: str, period_code: str,
                      invoice_type: str = "credit") -> dict:
        """
        請求書をGoogle Driveに保存
        invoice_type: "credit" (クレジット) or "bank" (銀行振込)
        """
        # サブフォルダを取得/作成
        folder_id = self._get_or_create_invoice_folder(period_code, invoice_type)

        # 重複チェック: 同じファイル名が既に存在するか確認
        existing = self._check_file_exists(filename, folder_id)
        if existing:
            return {
                "file_id": existing["id"],
                "web_view_link": existing.get("webViewLink", ""),
                "skipped": True
            }

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
            fields="id, webViewLink",
            supportsAllDrives=True
        ).execute()

        return {
            "file_id": file.get("id"),
            "web_view_link": file.get("webViewLink")
        }

    def _check_file_exists(self, filename: str, folder_id: str) -> Optional[dict]:
        """フォルダ内に同名ファイルが存在するか確認"""
        query = (
            f"name='{filename}' and "
            f"'{folder_id}' in parents and "
            f"trashed=false"
        )

        results = self.drive.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        files = results.get("files", [])
        if files:
            return files[0]
        return None

    def _get_or_create_folder(self, name: str, parent_id: str) -> str:
        """フォルダを取得または作成"""
        query = (
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )

        results = self.drive.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        files = results.get("files", [])
        if files:
            return files[0]["id"]

        # フォルダを作成
        folder_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id]
        }

        folder = self.drive.files().create(
            body=folder_metadata,
            fields="id",
            supportsAllDrives=True
        ).execute()

        return folder.get("id")

    def _get_or_create_invoice_folder(self, period_code: str, invoice_type: str) -> str:
        """
        請求書用フォルダ構造を作成
        例: 2025年9月/202509_クレジット
        売上の場合は月フォルダ直下に格納
        """
        # period_code: "202509" -> "2025年9月"
        year = int(period_code[:4])
        month = int(period_code[4:6])
        month_folder_name = f"{year}年{month}月"

        # 月フォルダを作成
        month_folder_id = self._get_or_create_folder(month_folder_name, self.drive_folder_id)

        # 売上の場合は月フォルダ直下
        if invoice_type == "sales":
            return month_folder_id

        # サブフォルダ名
        type_name = "クレジット" if invoice_type == "credit" else "銀行振込"
        sub_folder_name = f"{period_code}_{type_name}"

        # サブフォルダを作成
        return self._get_or_create_folder(sub_folder_name, month_folder_id)

    def create_period_folders(self, period_str: str) -> dict:
        """期間のフォルダ構造を一括作成"""
        periods = parse_period(period_str)
        created = []

        for p in periods:
            period_code = p["code"]
            month_name = f"{p['year']}年{p['month']}月"

            # クレジットと銀行振込両方のフォルダを作成
            credit_folder = self._get_or_create_invoice_folder(period_code, "credit")
            bank_folder = self._get_or_create_invoice_folder(period_code, "bank")

            created.append({
                "period": month_name,
                "code": period_code,
                "credit_folder": credit_folder,
                "bank_folder": bank_folder
            })

        return {"periods": created}

    def _is_invoice_registered(self, drive_url: str) -> bool:
        """invoicesシートに同じdrive_urlが既に登録されているか確認"""
        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="invoices!F2:F1000"
            ).execute()
            rows = result.get("values", [])
            return any(row[0] == drive_url for row in rows if row)
        except Exception:
            return False

    def _is_invoice_duplicate(self, vendor: str, amount_str: str, date: str) -> bool:
        """金額一致 かつ (日付±3日一致 or 名前部分一致/頭文字一致) で既存invoiceとの重複を判定"""
        new_amount = self._parse_amount(amount_str)
        if new_amount is None:
            return False

        new_date = self._normalize_date(date) if date else ""

        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="invoices!B2:D1000"
            ).execute()
            rows = result.get("values", [])

            # カラム: vendor(B=0), amount(C=1), date(D=2)
            for row in rows:
                if len(row) < 2:
                    continue
                existing_amount = self._parse_amount(row[1] if len(row) > 1 else "")
                if existing_amount is None or not self._amounts_match(new_amount, existing_amount):
                    continue

                existing_date = self._normalize_date(row[2]) if len(row) > 2 and row[2] else ""
                existing_vendor = row[0] if len(row) > 0 else ""

                # 金額一致 かつ 日付±3日一致 かつ ベンダー名一致 で重複判定
                # （ベンダー名のみ一致で日付が異なる場合は別月の請求書として登録を許可）
                if self._dates_match(new_date, existing_date) and self._vendors_match(vendor, existing_vendor):
                    return True

            return False
        except Exception:
            return False

    def record_invoice(self, invoice_data: dict) -> bool:
        """
        請求書情報をSpreadsheetに記録。
        重複（同じdrive_url、または金額+日付/名前一致）がある場合はスキップしてFalseを返す。
        """
        drive_url = invoice_data.get("drive_url", "")
        if drive_url and self._is_invoice_registered(drive_url):
            print(f"[record_invoice] skipped duplicate (drive_url): {drive_url}")
            return False

        # 金額 + (日付一致 or 名前部分一致) で重複判定
        vendor = invoice_data.get("vendor", "")
        amount = invoice_data.get("amount", "")
        date = invoice_data.get("date", "")
        if self._is_invoice_duplicate(vendor, amount, date):
            print(f"[record_invoice] skipped duplicate (amount+date/vendor): {vendor} {amount} {date}")
            return False

        row = [
            invoice_data.get("id", ""),
            self._normalize_for_storage(invoice_data.get("vendor", "")),
            invoice_data.get("amount", ""),
            invoice_data.get("date", ""),
            invoice_data.get("source", ""),
            drive_url,
            invoice_data.get("status", "pending"),
            datetime.now().isoformat()
        ]

        self.sheets.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range="invoices!A:H",
            valueInputOption="USER_ENTERED",
            body={"values": [row]}
        ).execute()
        return True

    def _register_single_invoice(self, pdf_data: bytes, drive_result: dict,
                                 rule_name: str, email_date: str, period: str) -> dict:
        """
        単一のPDFをGemini解析してinvoicesシートに登録する。
        Drive保存とは独立したtry/exceptで呼ぶこと。
        戻り値: {"registered": bool, "invoice": dict|None, "error": str|None,
                 "gemini_data": dict|None (ファイル名生成用)}
        """
        try:
            pdf_info = extract_invoice_data_with_gemini(pdf_data)
            amount = pdf_info.get("amount")
            vendor = pdf_info.get("vendor") or rule_name

            invoice_data = {
                "id": f"inv_{datetime.now().timestamp()}",
                "vendor": vendor,
                "amount": str(amount) if amount else "",
                "date": pdf_info.get("date") or email_date,
                "period": period,
                "source": "email_attachment",
                "drive_url": drive_result["web_view_link"],
                "summary": pdf_info.get("summary") or "",
                "type": "credit",
                "status": "pending"
            }
            recorded = self.record_invoice(invoice_data)
            if not recorded:
                return {"registered": False, "invoice": None, "error": None,
                        "duplicate": True, "gemini_data": pdf_info}
            return {"registered": True, "invoice": invoice_data, "error": None,
                    "gemini_data": pdf_info}
        except Exception as e:
            return {"registered": False, "invoice": None, "error": str(e),
                    "gemini_data": None}

    def fetch_invoices(self, days_back: int = 30) -> dict:
        """メールから請求書PDFを取得しDriveに保存、保存成功したら自動登録"""
        rules = self.get_subscriptions()
        results = {
            "processed": 0,
            "saved": 0,
            "registered": 0,
            "skipped": 0,
            "errors": [],
            "register_errors": [],
            "invoices": [],
            "extraction_details": [],
        }

        for rule in rules:
            for user_email in self.gmail_users:
                try:
                    emails = self.search_emails(rule, days_back, user_email=user_email)
                    results["processed"] += len(emails)

                    for email in emails:
                        email_date = self.get_message_date(email)

                        try:
                            dt = datetime.strptime(email_date, "%Y-%m-%d")
                            period = f"{dt.year}{dt.month:02d}"
                        except Exception:
                            period = datetime.now().strftime("%Y%m")

                        if rule.get("fetch_type") == "attachment":
                            attachments = self.get_attachments(email)
                            file_naming = rule.get("file_naming", "rename")

                            for att in attachments:
                                original_filename = att["filename"]
                                filename_analysis = analyze_original_filename(original_filename)

                                if file_naming == "original":
                                    # Original: 元の添付ファイル名をそのまま使用、Geminiは呼ばない
                                    filename = original_filename
                                    fn_parsed = parse_invoice_filename(original_filename)
                                    inv_date = fn_parsed.get("date") or email_date
                                    inv_vendor = fn_parsed.get("vendor") or rule["name"]
                                    inv_amount = fn_parsed.get("amount")
                                    pdf_info = {"amount": inv_amount, "vendor": inv_vendor, "date": inv_date, "summary": None,
                                                "extraction_method": "filename", "confidence": {"score": 0, "level": "n/a", "details": ["ファイル名から抽出"]}}
                                else:
                                    # Rename: Gemini解析して命名ルールでファイル名を生成
                                    try:
                                        pdf_info = extract_invoice_data_with_gemini(att["data"])
                                    except Exception as gemini_err:
                                        pdf_info = {"amount": None, "vendor": None, "date": None, "summary": None,
                                                    "extraction_method": "none", "confidence": {"score": 0, "level": "low", "details": ["Gemini抽出失敗"]}}
                                        print(f"Gemini extraction failed: {gemini_err}")

                                    inv_date = pdf_info.get("date") or email_date
                                    inv_vendor = pdf_info.get("vendor") or rule["name"]
                                    inv_amount = pdf_info.get("amount")
                                    filename = format_invoice_filename(inv_date, inv_vendor, inv_amount)

                                # 抽出詳細を記録
                                detail = {
                                    "original_filename": original_filename,
                                    "saved_filename": filename,
                                    "file_naming_rule": file_naming,
                                    "rule_name": rule.get("name", ""),
                                    "extraction_method": pdf_info.get("extraction_method", "unknown"),
                                    "confidence": pdf_info.get("confidence", {}),
                                    "extracted_vendor": pdf_info.get("vendor"),
                                    "extracted_amount": pdf_info.get("amount"),
                                    "extracted_date": pdf_info.get("date"),
                                    "filename_analysis": filename_analysis,
                                }
                                results["extraction_details"].append(detail)

                                drive_result = self.save_to_drive(
                                    att["data"],
                                    filename,
                                    period
                                )

                                if drive_result.get("skipped"):
                                    results["skipped"] += 1
                                    continue

                                results["saved"] += 1

                                # Drive保存成功 → シートに登録
                                try:
                                    invoice_data = {
                                        "id": f"inv_{datetime.now().timestamp()}",
                                        "vendor": inv_vendor,
                                        "amount": str(inv_amount) if inv_amount else "",
                                        "date": inv_date,
                                        "period": period,
                                        "source": "email_attachment",
                                        "drive_url": drive_result["web_view_link"],
                                        "summary": pdf_info.get("summary") or "",
                                        "type": "credit",
                                        "status": "pending"
                                    }
                                    recorded = self.record_invoice(invoice_data)
                                    if recorded:
                                        results["registered"] += 1
                                        results["invoices"].append(invoice_data)
                                    else:
                                        results["skipped"] += 1
                                except Exception as reg_err:
                                    results["register_errors"].append(f"{filename}: {reg_err}")

                        elif rule.get("fetch_type") == "link":
                            links = self.extract_links(email, rule.get("link_pattern"))
                            for link in links:
                                results["invoices"].append({
                                    "vendor": rule["name"],
                                    "link": link,
                                    "status": "link_found"
                                })

                except Exception as e:
                    results["errors"].append(f"{rule.get('name', 'unknown')} ({user_email}): {str(e)}")

        return results


    def fetch_invoices_by_period(self, period_str: str) -> dict:
        """
        期間指定でメールからPDFを取得しDriveに保存、保存成功したら自動登録
        period_str: "202602" or "202509~202601"
        """
        periods = parse_period(period_str)
        rules = self.get_subscriptions()

        results = {
            "processed": 0,
            "saved": 0,
            "registered": 0,
            "skipped": 0,
            "errors": [],
            "register_errors": [],
            "invoices": [],
            "extraction_details": [],
            "periods_created": []
        }

        folder_result = self.create_period_folders(period_str)
        results["periods_created"] = folder_result["periods"]

        for p in periods:
            period_code = p["code"]
            year = p["year"]
            month = p["month"]

            start_date = f"{year}/{month:02d}/01"
            if month == 12:
                end_year = year + 1
                end_month = 1
            else:
                end_year = year
                end_month = month + 1
            end_date = f"{end_year}/{end_month:02d}/01"

            for rule in rules:
                for user_email in self.gmail_users:
                    try:
                        emails = self.search_emails(
                            rule,
                            start_date=start_date,
                            end_date=end_date,
                            user_email=user_email
                        )
                        results["processed"] += len(emails)

                        for email in emails:
                            email_date = self.get_message_date(email)

                            if rule.get("fetch_type") == "attachment":
                                attachments = self.get_attachments(email)
                                file_naming = rule.get("file_naming", "rename")

                                for att in attachments:
                                    original_filename = att["filename"]
                                    filename_analysis = analyze_original_filename(original_filename)

                                    if file_naming == "original":
                                        # Original: 元の添付ファイル名をそのまま使用、Geminiは呼ばない
                                        filename = original_filename
                                        fn_parsed = parse_invoice_filename(original_filename)
                                        inv_date = fn_parsed.get("date") or email_date
                                        inv_vendor = fn_parsed.get("vendor") or rule["name"]
                                        inv_amount = fn_parsed.get("amount")
                                        pdf_info = {"amount": inv_amount, "vendor": inv_vendor, "date": inv_date, "summary": None,
                                                    "extraction_method": "filename", "confidence": {"score": 0, "level": "n/a", "details": ["ファイル名から抽出"]}}
                                    else:
                                        # Rename: Gemini解析して命名ルールでファイル名を生成
                                        try:
                                            pdf_info = extract_invoice_data_with_gemini(att["data"])
                                        except Exception as gemini_err:
                                            pdf_info = {"amount": None, "vendor": None, "date": None, "summary": None,
                                                        "extraction_method": "none", "confidence": {"score": 0, "level": "low", "details": ["Gemini抽出失敗"]}}
                                            print(f"Gemini extraction failed: {gemini_err}")

                                        inv_date = pdf_info.get("date") or email_date
                                        inv_vendor = pdf_info.get("vendor") or rule["name"]
                                        inv_amount = pdf_info.get("amount")
                                        filename = format_invoice_filename(inv_date, inv_vendor, inv_amount)

                                    # 抽出詳細を記録
                                    detail = {
                                        "original_filename": original_filename,
                                        "saved_filename": filename,
                                        "file_naming_rule": file_naming,
                                        "rule_name": rule.get("name", ""),
                                        "extraction_method": pdf_info.get("extraction_method", "unknown"),
                                        "confidence": pdf_info.get("confidence", {}),
                                        "extracted_vendor": pdf_info.get("vendor"),
                                        "extracted_amount": pdf_info.get("amount"),
                                        "extracted_date": pdf_info.get("date"),
                                        "filename_analysis": filename_analysis,
                                    }
                                    results["extraction_details"].append(detail)

                                    drive_result = self.save_to_drive(
                                        att["data"],
                                        filename,
                                        period_code,
                                        invoice_type="credit"
                                    )

                                    if drive_result.get("skipped"):
                                        results["skipped"] += 1
                                        continue

                                    results["saved"] += 1

                                    # Drive保存成功 → シートに登録
                                    try:
                                        invoice_data = {
                                            "id": f"inv_{datetime.now().timestamp()}",
                                            "vendor": inv_vendor,
                                            "amount": str(inv_amount) if inv_amount else "",
                                            "date": inv_date,
                                            "period": period_code,
                                            "source": "email_attachment",
                                            "drive_url": drive_result["web_view_link"],
                                            "summary": pdf_info.get("summary") or "",
                                            "type": "credit",
                                            "status": "pending"
                                        }
                                        recorded = self.record_invoice(invoice_data)
                                        if recorded:
                                            results["registered"] += 1
                                            results["invoices"].append(invoice_data)
                                        else:
                                            results["skipped"] += 1
                                    except Exception as reg_err:
                                        results["register_errors"].append(f"{filename}: {reg_err}")

                    except Exception as e:
                        results["errors"].append(f"{rule.get('name', 'unknown')} ({user_email}, {period_code}): {str(e)}")

        return results

    def register_from_drive(self, period_str: str) -> dict:
        """
        指定期間のDriveフォルダ内の既存PDFを走査し、
        ファイル名（{請求日}_{請求元}_{金額}.pdf）からデータを抽出してinvoicesシートに登録する。
        ファイル名から日付・金額・請求元が取得できない場合はGemini APIでPDFを解析する。
        """
        periods = parse_period(period_str)
        results = {
            "scanned": 0,
            "registered": 0,
            "skipped": 0,
            "errors": [],
            "invoices": []
        }

        for p in periods:
            period_code = p["code"]
            year = p["year"]
            month = p["month"]
            month_folder_name = f"{year}年{month}月"

            for invoice_type in ["credit", "bank"]:
                type_name = "クレジット" if invoice_type == "credit" else "銀行振込"
                sub_folder_name = f"{period_code}_{type_name}"

                try:
                    # 月フォルダを検索
                    month_folder_id = self._find_folder(month_folder_name, self.drive_folder_id)
                    if not month_folder_id:
                        continue

                    # サブフォルダを検索
                    sub_folder_id = self._find_folder(sub_folder_name, month_folder_id)
                    if not sub_folder_id:
                        continue

                    # フォルダ内のPDFを一覧
                    pdf_files = self._list_pdfs_in_folder(sub_folder_id)
                    results["scanned"] += len(pdf_files)

                    for pdf_file in pdf_files:
                        drive_url = pdf_file.get("webViewLink", "")

                        # 既にinvoicesシートに登録済みならスキップ
                        if drive_url and self._is_invoice_registered(drive_url):
                            results["skipped"] += 1
                            continue

                        try:
                            # ファイル名から請求データを抽出
                            parsed = parse_invoice_filename(pdf_file["name"])
                            vendor = parsed.get("vendor") or pdf_file["name"]
                            amount = parsed.get("amount")
                            inv_date = parsed.get("date") or ""
                            summary = ""

                            # ファイル名から日付が取得できない場合、Geminiで解析
                            if not inv_date:
                                try:
                                    pdf_data = self._download_pdf_from_drive(pdf_file["id"])
                                    pdf_info = extract_invoice_data_with_gemini(pdf_data)
                                    inv_date = pdf_info.get("date") or ""
                                    vendor = pdf_info.get("vendor") or vendor
                                    amount = pdf_info.get("amount") or amount
                                    summary = pdf_info.get("summary") or ""
                                except Exception as gemini_err:
                                    print(f"Gemini extraction failed for {pdf_file['name']}: {gemini_err}")

                            invoice_data = {
                                "id": f"inv_{datetime.now().timestamp()}",
                                "vendor": vendor,
                                "amount": str(amount) if amount else "",
                                "date": inv_date,
                                "period": period_code,
                                "source": "drive_scan",
                                "drive_url": drive_url,
                                "summary": summary,
                                "type": invoice_type,
                                "status": "pending"
                            }
                            recorded = self.record_invoice(invoice_data)
                            if recorded:
                                results["registered"] += 1
                                results["invoices"].append(invoice_data)
                            else:
                                results["skipped"] += 1

                        except Exception as e:
                            results["errors"].append(f"{pdf_file['name']}: {str(e)}")

                except Exception as e:
                    results["errors"].append(f"{sub_folder_name}: {str(e)}")

        return results

    def _find_folder(self, name: str, parent_id: str) -> Optional[str]:
        """フォルダを検索（存在しなければNone）"""
        query = (
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )
        results = self.drive.files().list(
            q=query, spaces="drive", fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def _list_pdfs_in_folder(self, folder_id: str) -> list[dict]:
        """フォルダ内のPDFファイル一覧を返す"""
        query = (
            f"'{folder_id}' in parents and "
            f"mimeType='application/pdf' and "
            f"trashed=false"
        )
        results = self.drive.files().list(
            q=query, spaces="drive",
            fields="files(id, name, webViewLink)",
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        return results.get("files", [])

    def _list_files_in_folder(self, folder_id: str) -> list[dict]:
        """フォルダ内の全ファイル一覧を返す（フォルダ自体は除外）"""
        query = (
            f"'{folder_id}' in parents and "
            f"mimeType!='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )
        results = self.drive.files().list(
            q=query, spaces="drive",
            fields="files(id, name, webViewLink)",
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        return results.get("files", [])

    def _download_pdf_from_drive(self, file_id: str) -> bytes:
        """DriveからPDFファイルのバイナリデータをダウンロードする"""
        request = self.drive.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """日付文字列をYYYY-MM-DD形式に正規化"""
        date_str = date_str.strip()
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日", "%m/%d/%Y"):
            try:
                return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return date_str

    def parse_saison_csv(self, csv_content: str) -> list[dict]:
        """SaisonカードCSVをパース"""
        transactions = []
        lines = csv_content.strip().split("\n")

        # ヘッダー行を探す
        header_idx = -1
        for i, line in enumerate(lines):
            if "利用日" in line and "利用金額" in line:
                header_idx = i
                break

        if header_idx == -1:
            return transactions

        # CSVをパース
        reader = csv.reader(lines[header_idx:])
        headers = next(reader)

        # カラムインデックスを特定
        date_idx = next((i for i, h in enumerate(headers) if "利用日" in h), 0)
        name_idx = next((i for i, h in enumerate(headers) if "利用店名" in h or "ご利用店名" in h), 1)
        amount_idx = next((i for i, h in enumerate(headers) if "利用金額" in h), -1)

        for row in reader:
            if len(row) > max(date_idx, name_idx, amount_idx) and row[date_idx]:
                # 金額を数値に変換（マイナス金額は返金のため除外）
                amount_str = row[amount_idx] if amount_idx >= 0 else "0"
                amount_str = amount_str.strip()
                if amount_str.startswith("-") or amount_str.startswith("△") or amount_str.startswith("▲"):
                    continue
                amount = int(re.sub(r"[^\d]", "", amount_str) or "0")

                if amount > 0:
                    transactions.append({
                        "date": self._normalize_date(row[date_idx]),
                        "vendor": row[name_idx].strip(),
                        "amount": amount,
                        "type": "credit"
                    })

        return transactions

    def parse_bank_csv(self, csv_content: str) -> list[dict]:
        """銀行CSVをパース"""
        transactions = []
        lines = csv_content.strip().split("\n")

        # ヘッダー行を探す
        header_idx = -1
        for i, line in enumerate(lines):
            if "日付" in line or "摘要" in line:
                header_idx = i
                break

        if header_idx == -1:
            # ヘッダーなしの場合、最初の行から
            header_idx = 0

        reader = csv.reader(lines[header_idx:])
        headers = next(reader)

        # カラムインデックスを特定
        date_idx = next((i for i, h in enumerate(headers) if "日付" in h), 0)
        desc_idx = next((i for i, h in enumerate(headers) if "摘要" in h), 1)
        deposit_idx = next((i for i, h in enumerate(headers) if "入金" in h), 2)
        withdraw_idx = next((i for i, h in enumerate(headers) if "出金" in h), 3)

        for row in reader:
            if len(row) > max(date_idx, desc_idx):
                date_str = row[date_idx].strip()
                desc = row[desc_idx].strip() if desc_idx < len(row) else ""

                # 出金額を取得（マイナス金額は返金のため除外）
                withdraw_str = row[withdraw_idx].strip() if withdraw_idx < len(row) else "0"
                if withdraw_str.startswith("-") or withdraw_str.startswith("△") or withdraw_str.startswith("▲"):
                    continue
                withdraw = int(re.sub(r"[^\d]", "", withdraw_str) or "0")

                if withdraw > 0 and date_str:
                    transactions.append({
                        "date": self._normalize_date(date_str),
                        "vendor": desc,
                        "amount": withdraw,
                        "type": "bank"
                    })

        return transactions

    def get_drive_files_for_period(self, period_code: str) -> dict:
        """
        指定期間のDriveフォルダから直接ファイルIDを取得する。
        スプレッドシートを経由せず、Driveフォルダ構造から
        クレジット/銀行振込に分類し、フォルダ名とファイルIDリストを返す。
        """
        periods = parse_period(period_code)
        card_folders = []
        bank_folders = []

        for p in periods:
            year = p["year"]
            month = p["month"]
            code = p["code"]
            month_folder_name = f"{year}年{month}月"

            month_folder_id = self._find_folder(month_folder_name, self.drive_folder_id)
            if not month_folder_id:
                continue

            for invoice_type in ["credit", "bank"]:
                type_name = "クレジット" if invoice_type == "credit" else "銀行振込"
                sub_folder_name = f"{code}_{type_name}"

                sub_folder_id = self._find_folder(sub_folder_name, month_folder_id)
                if not sub_folder_id:
                    continue

                files = self._list_files_in_folder(sub_folder_id)
                file_ids = [f["id"] for f in files if f.get("id")]

                if file_ids:
                    entry = {"name": sub_folder_name, "file_ids": file_ids}
                    if invoice_type == "bank":
                        bank_folders.append(entry)
                    else:
                        card_folders.append(entry)

        return {"card": card_folders, "bank": bank_folders}

    def get_invoices_for_period(self, period_code: str) -> list[dict]:
        """指定期間の請求書一覧を取得（日付ベースでフィルタリング）"""
        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="invoices!A2:H1000"
            ).execute()

            rows = result.get("values", [])
            invoices = []

            # 期間コードから対象月のリストを生成
            periods = parse_period(period_code) if period_code else []
            target_months = set()
            for p in periods:
                # YYYY-MM形式で対象月を追加
                target_months.add(f"{p['year']}-{p['month']:02d}")

            # カラム: id(0), vendor(1), amount(2), date(3), source(4), drive_url(5), status(6), created_at(7)
            for row_idx, row in enumerate(rows):
                if len(row) >= 4:
                    inv_date = row[3] if len(row) > 3 else ""

                    # 日付を正規化してYYYY-MMを抽出
                    normalized_date = self._normalize_date(inv_date)
                    inv_month = normalized_date[:7] if len(normalized_date) >= 7 else ""

                    # 期間指定がない場合は全件、ある場合は月が一致するもの
                    if not period_code or inv_month in target_months:
                        invoices.append({
                            "id": row[0] if len(row) > 0 else "",
                            "vendor": row[1] if len(row) > 1 else "",
                            "amount": row[2] if len(row) > 2 else "",
                            "date": inv_date,
                            "source": row[4] if len(row) > 4 else "",
                            "drive_url": row[5] if len(row) > 5 else "",
                            "status": row[6] if len(row) > 6 else "pending",
                            "type": "credit",
                            "_sheet_row": row_idx + 2  # スプレッドシートの実際の行番号（ヘッダー行=1を考慮）
                        })

            return invoices
        except Exception as e:
            print(f"Error getting invoices: {e}")
            return []

    @staticmethod
    def _normalize_date(d: str) -> str:
        """日付文字列をYYYY-MM-DD形式に正規化する"""
        if not d:
            return ""
        d = d.strip().replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
        d = d.strip().rstrip("-")
        if "-" in d:
            parts = d.split("-")
            if len(parts) >= 3 and parts[2].strip():
                return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].strip().zfill(2)}"
            elif len(parts) >= 2:
                return f"{parts[0]}-{parts[1].zfill(2)}-01"
        elif len(d) >= 8 and d[:8].isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        elif "/" in d:
            parts = d.split("/")
            if len(parts) >= 3 and parts[2].strip():
                return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].strip().zfill(2)}"
            elif len(parts) >= 2:
                return f"{parts[0]}-{parts[1].zfill(2)}-01"
        return d

    @staticmethod
    def _dates_match(date1: str, date2: str, tolerance_days: int = 3) -> bool:
        """2つの正規化済み日付がtolerance_days以内なら一致とみなす"""
        if not date1 or not date2:
            return False
        if date1 == date2:
            return True
        try:
            from datetime import datetime, timedelta
            d1 = datetime.strptime(date1[:10], "%Y-%m-%d")
            d2 = datetime.strptime(date2[:10], "%Y-%m-%d")
            return abs((d1 - d2).days) <= tolerance_days
        except (ValueError, TypeError):
            return date1 == date2

    @staticmethod
    def _normalize_text(text: str) -> str:
        """全角/半角を統一し、スペースを除去して正規化"""
        import unicodedata
        text = unicodedata.normalize("NFKC", text)
        text = "".join(text.split())
        return text.lower().strip()

    @staticmethod
    def _normalize_for_storage(text: str) -> str:
        """
        シート保存用の正規化。表示は保ったままUnicodeの表記ゆれだけ潰す。

        PDFやメールから抽出した文字列は濁点が結合文字で分解されていることがある
        （例: 「ダ」= U+30BF + U+3099 の2文字）。見た目は同じでも別の文字列として
        扱われるため、検索・集計・名寄せが全てすり抜ける。NFKCで合成済みの
        1文字（U+30C0）に統一しておく。
        """
        if not text:
            return ""
        import unicodedata
        return unicodedata.normalize("NFKC", str(text)).strip()

    @staticmethod
    def _amounts_match(amount1: int, amount2: int) -> bool:
        """
        金額の一致判定。外貨建て請求は取込のたびに為替換算され数円ズレることが
        あるため（例: $135.01 が 22,112円 / 22,120円）、わずかな差は同額とみなす。
        許容幅は 0.1% かつ最大50円。少額請求（110円 vs 130円など）を
        取り違えないよう絶対値でも上限をかける。
        """
        if amount1 == amount2:
            return True
        tolerance = max(1, min(50, int(max(amount1, amount2) * 0.001)))
        return abs(amount1 - amount2) <= tolerance

    @staticmethod
    def _strip_vendor_noise(name: str) -> str:
        """ベンダー名からCSV取引種別・銀行名・会社種別などのノイズを除去"""
        import re
        # CSV摘要のプレフィックス除去（振込、PE、デビット利用、etc.）
        name = re.sub(r"^(振込|pe|デビット利用|クレジット|口座振替|引落|自振)\s*", "", name, flags=re.IGNORECASE)
        # 銀行名除去（カタカナの銀行名: ミツビシユーエフジエイ、ミツイスミトモ等）
        name = re.sub(
            r"^(ミツビシユーエフジエイ|ミツイスミトモ|ミズホ|スミシンエスビーアイネツト|"
            r"ラクテン|ナント|キヨウトシンキン|ジユウハチ|リソナ|ゆうちょ|ユウチヨ|"
            r"ミツイスミトモギンコウ|ミズホギンコウ|smbc|mufg|sbi)\s*",
            "", name, flags=re.IGNORECASE
        )
        # 会社種別除去
        name = re.sub(r"(株式会社|有限会社|合同会社|\(株\)|（株）|inc\.?|co\.?,?\s*ltd\.?|llc|corp\.?)", "", name, flags=re.IGNORECASE)
        # 承認番号・長い数字除去
        name = re.sub(r"承認番号[：:]?\s*\d+", "", name)
        name = re.sub(r"\d{8,}", "", name)
        return name.strip()

    @classmethod
    def _vendors_match(cls, vendor1: str, vendor2: str) -> bool:
        """ベンダー名の柔軟なマッチング（部分一致 + ノイズ除去 + 頭文字一致 + 全角半角対応）"""
        if not vendor1 or not vendor2:
            return False
        import unicodedata
        # NFKC正規化 + スペース除去
        v1 = unicodedata.normalize("NFKC", vendor1)
        v2 = unicodedata.normalize("NFKC", vendor2)
        v1_clean = "".join(v1.split()).lower()
        v2_clean = "".join(v2.split()).lower()
        if not v1_clean or not v2_clean:
            return False
        # 1. 部分文字列一致（スペース除去済みで比較）
        if v1_clean in v2_clean or v2_clean in v1_clean:
            return True
        # 2. ノイズ除去後の部分文字列一致
        v1_stripped = "".join(cls._strip_vendor_noise(v1).split()).lower()
        v2_stripped = "".join(cls._strip_vendor_noise(v2).split()).lower()
        if v1_stripped and v2_stripped:
            if v1_stripped in v2_stripped or v2_stripped in v1_stripped:
                return True
        # 3. 頭文字一致（例: "AWS" ↔ "Amazon Web Services"）
        w1_orig = v1.lower().split()
        w2_orig = v2.lower().split()
        if len(w1_orig) == 1 and len(v1_clean) <= 5 and len(w2_orig) > 1:
            initials = "".join(w[0] for w in w2_orig if w)
            if v1_clean == initials:
                return True
        if len(w2_orig) == 1 and len(v2_clean) <= 5 and len(w1_orig) > 1:
            initials = "".join(w[0] for w in w1_orig if w)
            if v2_clean == initials:
                return True
        return False

    @staticmethod
    def _parse_amount(amount_str) -> int | None:
        """金額文字列をintに変換。全角数字にも対応。変換できない場合はNone"""
        if not amount_str:
            return None
        try:
            import unicodedata
            s = unicodedata.normalize("NFKC", str(amount_str))
            return int(s.replace(",", "").replace("¥", "").replace("円", "").replace("￥", "").strip())
        except (ValueError, TypeError):
            return None

    def reconcile_csv(self, transactions: list[dict], period_code: str) -> dict:
        """CSV取引と請求書を照合（2段階マッチ）

        ステップ1: 日付(±3日以内) + 金額一致 → matched
        ステップ2: 名前の部分一致/頭文字一致 + 金額一致 → matched（日付不問）
        ※金額一致は常に必須
        ※csv_transactionsのstatus=matchedは呼び出し元でフィルタ済み
        """
        invoices = self.get_invoices_for_period(period_code)
        rules = self.get_subscriptions()
        rule_names = {self._normalize_text(r["name"]) for r in rules}

        matched = []
        used_invoices = set()
        unmatched_tx_indices = set(range(len(transactions)))

        # --- ステップ1: 日付(±3日以内) + 金額 で照合 ---
        for tx_idx, tx in enumerate(transactions):
            tx_amount = tx.get("amount", 0)
            tx_date = self._normalize_date(tx.get("date", ""))

            if not tx_date or not tx_amount:
                continue

            for inv_idx, inv in enumerate(invoices):
                if inv_idx in used_invoices:
                    continue

                inv_amount = self._parse_amount(inv.get("amount", ""))
                if inv_amount is None or inv_amount != tx_amount:
                    continue

                inv_date = self._normalize_date(inv.get("date", ""))
                if self._dates_match(tx_date, inv_date):
                    matched.append({"transaction": tx, "invoice": inv})
                    used_invoices.add(inv_idx)
                    unmatched_tx_indices.discard(tx_idx)
                    break

        # --- ステップ2: 名前の部分一致/頭文字一致 + 金額 で照合（日付不問） ---
        for tx_idx in sorted(unmatched_tx_indices):
            tx = transactions[tx_idx]
            tx_amount = tx.get("amount", 0)

            if not tx_amount:
                continue

            for inv_idx, inv in enumerate(invoices):
                if inv_idx in used_invoices:
                    continue

                inv_amount = self._parse_amount(inv.get("amount", ""))
                if inv_amount is None or inv_amount != tx_amount:
                    continue

                if self._vendors_match(tx["vendor"], inv["vendor"]):
                    matched.append({"transaction": tx, "invoice": inv})
                    used_invoices.add(inv_idx)
                    unmatched_tx_indices.discard(tx_idx)
                    break

        # --- 未マッチ取引の処理 ---
        # ステップ3: 未マッチの取引が、既にマッチ済みのinvoiceと一致する場合は
        # 重複取引（同じCSVの再アップロード等）として扱い、missingではなくmatched扱いにする
        duplicate_matched = []
        still_unmatched = set()
        for tx_idx in sorted(unmatched_tx_indices):
            tx = transactions[tx_idx]
            tx_amount = tx.get("amount", 0)
            tx_date = self._normalize_date(tx.get("date", ""))
            is_duplicate = False

            if tx_amount:
                for inv_idx in used_invoices:
                    inv = invoices[inv_idx]
                    inv_amount = self._parse_amount(inv.get("amount", ""))
                    if inv_amount is None or inv_amount != tx_amount:
                        continue
                    inv_date = self._normalize_date(inv.get("date", ""))
                    if self._dates_match(tx_date, inv_date):
                        is_duplicate = True
                        break
                    if self._vendors_match(tx["vendor"], inv["vendor"]):
                        is_duplicate = True
                        break

            if is_duplicate:
                duplicate_matched.append({"transaction": tx, "invoice": invoices[inv_idx]})
            else:
                still_unmatched.add(tx_idx)

        missing = []
        unregistered_vendors = []
        for tx_idx in sorted(still_unmatched):
            tx = transactions[tx_idx]
            tx_vendor_lower = tx["vendor"].lower()
            missing.append(tx)

            vendor_registered = any(
                self._vendors_match(tx["vendor"], rn)
                for rn in rule_names
            )
            if not vendor_registered:
                if tx["vendor"] not in [v["vendor"] for v in unregistered_vendors]:
                    unregistered_vendors.append(tx)

        return {
            "matched": matched,
            "duplicate_matched": duplicate_matched,
            "missing": missing,
            "unregistered_vendors": unregistered_vendors,
            "total_transactions": len(transactions),
            "matched_count": len(matched),
            "duplicate_count": len(duplicate_matched),
            "missing_count": len(missing),
        }


# シングルトンインスタンス
invoice_fetcher = InvoiceFetcher()
