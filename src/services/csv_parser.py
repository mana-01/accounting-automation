"""CSV parser for bank and card statements."""

import io
import re
from datetime import datetime
from typing import Optional
import pandas as pd

from ..models import Transaction


class CSVParser:
    """銀行・カード明細CSVをパースするサービス"""

    # よくあるカード会社・銀行のCSVフォーマット定義
    KNOWN_FORMATS = {
        "rakuten_card": {
            "encoding": "shift_jis",
            "date_column": "利用日",
            "description_column": "利用店名・商品名",
            "amount_column": "利用金額",
            "date_format": "%Y/%m/%d",
        },
        "smbc_card": {
            "encoding": "shift_jis",
            "date_column": "ご利用日",
            "description_column": "ご利用先など",
            "amount_column": "ご利用金額",
            "date_format": "%Y/%m/%d",
        },
        "mufg_bank": {
            "encoding": "shift_jis",
            "date_column": "日付",
            "description_column": "摘要",
            "amount_column": "お支払金額",
            "date_format": "%Y/%m/%d",
        },
        "generic": {
            "encoding": "utf-8",
            "date_column": "date",
            "description_column": "description",
            "amount_column": "amount",
            "date_format": "%Y-%m-%d",
        },
    }

    def __init__(self):
        pass

    def parse(
        self,
        csv_content: bytes,
        format_type: Optional[str] = None,
        custom_config: Optional[dict] = None
    ) -> list[Transaction]:
        """CSVをパースしてTransaction一覧を返す"""

        # フォーマット設定を取得
        if custom_config:
            config = custom_config
        elif format_type and format_type in self.KNOWN_FORMATS:
            config = self.KNOWN_FORMATS[format_type]
        else:
            # 自動検出を試みる
            config = self._detect_format(csv_content)

        # CSVを読み込み
        try:
            df = pd.read_csv(
                io.BytesIO(csv_content),
                encoding=config.get("encoding", "utf-8"),
                dtype=str
            )
        except UnicodeDecodeError:
            # エンコーディングを変えて再試行
            for enc in ["shift_jis", "cp932", "utf-8-sig", "utf-8"]:
                try:
                    df = pd.read_csv(io.BytesIO(csv_content), encoding=enc, dtype=str)
                    break
                except:
                    continue
            else:
                raise ValueError("CSVのエンコーディングを検出できませんでした")

        # カラム名をマッピング
        date_col = self._find_column(df, config.get("date_column", "date"))
        desc_col = self._find_column(df, config.get("description_column", "description"))
        amount_col = self._find_column(df, config.get("amount_column", "amount"))

        if not all([date_col, desc_col, amount_col]):
            raise ValueError(
                f"必要なカラムが見つかりません。"
                f"日付: {date_col}, 説明: {desc_col}, 金額: {amount_col}"
            )

        # Transactionに変換
        transactions = []
        date_format = config.get("date_format", "%Y-%m-%d")

        for _, row in df.iterrows():
            try:
                # 日付をパース
                date_str = str(row[date_col]).strip()
                try:
                    parsed_date = datetime.strptime(date_str, date_format)
                except ValueError:
                    # 他のフォーマットを試す
                    parsed_date = self._parse_date(date_str)

                # 金額をパース
                amount = self._parse_amount(str(row[amount_col]))

                # 説明
                description = str(row[desc_col]).strip()

                if parsed_date and amount is not None and amount > 0 and description:
                    transactions.append(Transaction(
                        date=parsed_date.strftime("%Y-%m-%d"),
                        description=description,
                        amount=amount,
                        transaction_type="credit",
                    ))
            except Exception as e:
                print(f"Row parse error: {e}")
                continue

        return transactions

    def _detect_format(self, csv_content: bytes) -> dict:
        """CSVフォーマットを自動検出"""
        # まずUTF-8で読んでみる
        try:
            text = csv_content.decode("utf-8")
        except:
            try:
                text = csv_content.decode("shift_jis")
            except:
                text = csv_content.decode("cp932", errors="ignore")

        header_line = text.split("\n")[0].lower()

        # 各フォーマットをチェック
        for format_name, config in self.KNOWN_FORMATS.items():
            if config["date_column"].lower() in header_line:
                return config

        # デフォルトはgeneric
        return self.KNOWN_FORMATS["generic"]

    def _find_column(self, df: pd.DataFrame, column_name: str) -> Optional[str]:
        """カラム名を検索（部分一致・大小文字無視）"""
        column_name_lower = column_name.lower()

        # 完全一致
        for col in df.columns:
            if col.lower() == column_name_lower:
                return col

        # 部分一致
        for col in df.columns:
            if column_name_lower in col.lower():
                return col

        return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """様々なフォーマットの日付をパース"""
        formats = [
            "%Y/%m/%d",
            "%Y-%m-%d",
            "%Y年%m月%d日",
            "%m/%d/%Y",
            "%d/%m/%Y",
        ]

        date_str = date_str.strip()

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        return None

    def _parse_amount(self, amount_str: str) -> Optional[float]:
        """金額文字列をパース"""
        if not amount_str or amount_str.lower() == "nan":
            return None

        # カンマ、円記号、スペースを除去
        cleaned = re.sub(r"[,¥￥\s円]", "", amount_str)

        # マイナス記号の処理
        is_negative = False
        if cleaned.startswith("-") or cleaned.startswith("△") or cleaned.startswith("▲"):
            is_negative = True
            cleaned = cleaned.lstrip("-△▲")

        if cleaned.endswith("-"):
            is_negative = True
            cleaned = cleaned.rstrip("-")

        try:
            amount = float(cleaned)
            return -amount if is_negative else amount
        except ValueError:
            return None

    def get_supported_formats(self) -> list[str]:
        """サポートしているフォーマット一覧を取得"""
        return list(self.KNOWN_FORMATS.keys())


# シングルトンインスタンス
csv_parser = CSVParser()
