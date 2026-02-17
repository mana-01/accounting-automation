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
from googleapiclient.http import MediaIoBaseUpload


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

    def search_emails(self, rule: dict, days_back: int = 30,
                      start_date: str = None, end_date: str = None) -> list[dict]:
        """
        ルールに基づいてメールを検索
        start_date/end_date: "YYYY/MM/DD" 形式
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
        print(f"Gmail search query: {query}")

        try:
            results = self.gmail.users().messages().list(
                userId="me",
                q=query,
                maxResults=100
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
            fields="id, webViewLink"
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
            fields="files(id, name, webViewLink)"
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
            fields="files(id, name)"
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
            fields="id"
        ).execute()

        return folder.get("id")

    def _get_or_create_invoice_folder(self, period_code: str, invoice_type: str) -> str:
        """
        請求書用フォルダ構造を作成
        例: 2025年9月/202509_クレジット
        """
        # period_code: "202509" -> "2025年9月"
        year = int(period_code[:4])
        month = int(period_code[4:6])
        month_folder_name = f"{year}年{month}月"

        # 月フォルダを作成
        month_folder_id = self._get_or_create_folder(month_folder_name, self.drive_folder_id)

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


    def fetch_invoices_by_period(self, period_str: str) -> dict:
        """
        期間指定でメールから請求書を取得
        period_str: "202602" or "202509~202601"
        """
        periods = parse_period(period_str)
        rules = self.get_email_rules()

        results = {
            "processed": 0,
            "saved": 0,
            "skipped": 0,
            "errors": [],
            "invoices": [],
            "periods_created": []
        }

        # まずフォルダ構造を作成
        folder_result = self.create_period_folders(period_str)
        results["periods_created"] = folder_result["periods"]

        for p in periods:
            period_code = p["code"]
            year = p["year"]
            month = p["month"]

            # 該当月の開始日と終了日（月末まで含む）
            start_date = f"{year}/{month:02d}/01"
            # 翌月の1日をbefore条件にすることで月末まで含む
            if month == 12:
                end_year = year + 1
                end_month = 1
            else:
                end_year = year
                end_month = month + 1
            end_date = f"{end_year}/{end_month:02d}/01"

            for rule in rules:
                try:
                    emails = self.search_emails(
                        rule,
                        start_date=start_date,
                        end_date=end_date
                    )
                    results["processed"] += len(emails)

                    for email in emails:
                        email_date = self.get_message_date(email)

                        if rule.get("fetch_type") == "attachment":
                            attachments = self.get_attachments(email)
                            for att in attachments:
                                filename = f"{rule['name']}_{email_date}_{att['filename']}"
                                drive_result = self.save_to_drive(
                                    att["data"],
                                    filename,
                                    period_code,
                                    invoice_type="credit"
                                )

                                # 重複の場合はスキップ
                                if drive_result.get("skipped"):
                                    results["skipped"] += 1
                                    continue

                                invoice_data = {
                                    "id": f"inv_{datetime.now().timestamp()}",
                                    "vendor": rule["name"],
                                    "amount": "",
                                    "date": email_date,
                                    "period": period_code,
                                    "source": "email_attachment",
                                    "drive_url": drive_result["web_view_link"],
                                    "type": "credit",
                                    "status": "pending"
                                }
                                self.record_invoice(invoice_data)
                                results["saved"] += 1
                                results["invoices"].append(invoice_data)

                except Exception as e:
                    results["errors"].append(f"{rule.get('name', 'unknown')} ({period_code}): {str(e)}")

        return results

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
                # 金額を数値に変換
                amount_str = row[amount_idx] if amount_idx >= 0 else "0"
                amount = int(re.sub(r"[^\d]", "", amount_str) or "0")

                if amount > 0:
                    transactions.append({
                        "date": row[date_idx],
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

                # 出金額を取得
                withdraw_str = row[withdraw_idx] if withdraw_idx < len(row) else "0"
                withdraw = int(re.sub(r"[^\d]", "", withdraw_str) or "0")

                if withdraw > 0 and date_str:
                    transactions.append({
                        "date": date_str,
                        "vendor": desc,
                        "amount": withdraw,
                        "type": "bank"
                    })

        return transactions

    def get_invoices_for_period(self, period_code: str) -> list[dict]:
        """指定期間の請求書一覧を取得"""
        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="invoices!A2:I1000"
            ).execute()

            rows = result.get("values", [])
            invoices = []

            for row in rows:
                if len(row) >= 5:
                    inv_period = row[4] if len(row) > 4 else ""
                    if period_code in str(inv_period) or not period_code:
                        invoices.append({
                            "id": row[0] if len(row) > 0 else "",
                            "vendor": row[1] if len(row) > 1 else "",
                            "amount": row[2] if len(row) > 2 else "",
                            "date": row[3] if len(row) > 3 else "",
                            "period": inv_period,
                            "drive_url": row[5] if len(row) > 5 else "",
                            "type": row[6] if len(row) > 6 else "credit",
                            "status": row[7] if len(row) > 7 else "pending"
                        })

            return invoices
        except Exception as e:
            print(f"Error getting invoices: {e}")
            return []

    def reconcile_csv(self, transactions: list[dict], period_code: str) -> dict:
        """CSV取引と請求書を照合（金額と日付ベース）"""
        invoices = self.get_invoices_for_period(period_code)
        rules = self.get_email_rules()
        rule_names = {r["name"].lower() for r in rules}

        matched = []
        missing = []
        unregistered_vendors = []
        used_invoices = set()  # 同じ請求書を複数回マッチさせない

        for tx in transactions:
            tx_vendor_lower = tx["vendor"].lower()
            tx_amount = tx.get("amount", 0)
            tx_date = tx.get("date", "")  # YYYY-MM-DD形式を想定

            # 請求書と照合
            found = False
            for idx, inv in enumerate(invoices):
                if idx in used_invoices:
                    continue

                inv_vendor_lower = inv["vendor"].lower()
                inv_date = inv.get("date", "")  # YYYY-MM-DD形式を想定
                inv_amount_str = inv.get("amount", "")

                # 金額の比較（請求書に金額がある場合のみ）
                amount_match = False
                if inv_amount_str:
                    try:
                        inv_amount = int(str(inv_amount_str).replace(",", "").replace("¥", ""))
                        amount_match = (inv_amount == tx_amount)
                    except (ValueError, TypeError):
                        amount_match = False
                else:
                    # 請求書に金額がない場合はベンダー名で判断
                    amount_match = (inv_vendor_lower in tx_vendor_lower or tx_vendor_lower in inv_vendor_lower)

                # 日付の比較（同じ月かどうか）
                date_match = False
                if tx_date and inv_date:
                    # YYYY-MM部分を比較
                    tx_month = tx_date[:7] if len(tx_date) >= 7 else ""
                    inv_month = inv_date[:7] if len(inv_date) >= 7 else ""
                    date_match = (tx_month == inv_month)
                else:
                    # 日付がない場合は期間コードで判断
                    date_match = True

                # ベンダー名の部分一致もチェック（補助条件）
                vendor_match = (inv_vendor_lower in tx_vendor_lower or tx_vendor_lower in inv_vendor_lower)

                # マッチ条件: (金額一致 AND 日付一致) OR (ベンダー一致 AND 日付一致)
                if (amount_match and date_match) or (vendor_match and date_match and amount_match):
                    matched.append({
                        "transaction": tx,
                        "invoice": inv
                    })
                    used_invoices.add(idx)
                    found = True
                    break

            if not found:
                missing.append(tx)

                # ルール登録済みかチェック
                vendor_registered = any(
                    rn in tx_vendor_lower or tx_vendor_lower in rn
                    for rn in rule_names
                )
                if not vendor_registered:
                    # 同じベンダーを重複登録しない
                    if tx["vendor"] not in [v["vendor"] for v in unregistered_vendors]:
                        unregistered_vendors.append(tx)

        return {
            "matched": matched,
            "missing": missing,
            "unregistered_vendors": unregistered_vendors,
            "total_transactions": len(transactions),
            "matched_count": len(matched),
            "missing_count": len(missing)
        }


# シングルトンインスタンス
invoice_fetcher = InvoiceFetcher()
