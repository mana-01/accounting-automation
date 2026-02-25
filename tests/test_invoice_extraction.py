"""Tests for invoice extraction confidence scoring and filename analysis."""

import pytest
import sys
import os
import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Google APIモジュールをモックして、依存関係の問題を回避
sys.modules["google.oauth2"] = MagicMock()
sys.modules["google.oauth2.service_account"] = MagicMock()
sys.modules["googleapiclient"] = MagicMock()
sys.modules["googleapiclient.discovery"] = MagicMock()
sys.modules["googleapiclient.http"] = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services.invoice_fetcher import (
    _calculate_extraction_confidence,
    analyze_original_filename,
    format_invoice_filename,
    parse_invoice_filename,
)


class TestExtractionConfidence:
    """抽出信頼度スコアリングのテスト"""

    def test_high_confidence_all_fields(self):
        """全フィールドが抽出できた場合: high"""
        data = {
            "amount": 15000,
            "vendor": "Amazon Web Services",
            "date": "2026-02-01",
        }
        result = _calculate_extraction_confidence(data, method="gemini")

        assert result["level"] == "high"
        assert result["score"] >= 0.80

    def test_medium_confidence_missing_date(self):
        """日付が欠けた場合: medium"""
        data = {
            "amount": 15000,
            "vendor": "AWS",
            "date": None,
        }
        result = _calculate_extraction_confidence(data, method="gemini")

        assert result["level"] == "medium"
        assert 0.50 <= result["score"] < 0.80

    def test_low_confidence_only_amount(self):
        """金額のみの場合: low"""
        data = {
            "amount": 5000,
            "vendor": None,
            "date": None,
        }
        result = _calculate_extraction_confidence(data, method="gemini")

        assert result["level"] == "low"
        assert result["score"] < 0.50

    def test_low_confidence_nothing_extracted(self):
        """何も抽出できなかった場合: low"""
        data = {
            "amount": None,
            "vendor": None,
            "date": None,
        }
        result = _calculate_extraction_confidence(data, method="gemini")

        assert result["level"] == "low"
        assert result["score"] < 0.50

    def test_regex_method_lower_baseline(self):
        """正規表現フォールバックの場合、ベースラインスコアが低い"""
        data = {"amount": 15000, "vendor": None, "date": None}

        gemini = _calculate_extraction_confidence(data, method="gemini")
        regex = _calculate_extraction_confidence(data, method="regex")

        assert gemini["score"] > regex["score"]

    def test_invalid_date_format_lower_score(self):
        """不正な日付形式の場合スコアが下がる"""
        valid = {"amount": 15000, "vendor": "AWS", "date": "2026-02-01"}
        invalid = {"amount": 15000, "vendor": "AWS", "date": "02/01/2026"}

        valid_result = _calculate_extraction_confidence(valid, method="gemini")
        invalid_result = _calculate_extraction_confidence(invalid, method="gemini")

        assert valid_result["score"] > invalid_result["score"]

    def test_amount_out_of_range(self):
        """金額が想定範囲外の場合"""
        data = {"amount": 50, "vendor": "Test", "date": "2026-02-01"}
        result = _calculate_extraction_confidence(data, method="gemini")

        assert "金額が想定範囲外" in " ".join(result["details"])

    def test_unknown_vendor_lower_score(self):
        """不明なベンダー名の場合スコアが下がる"""
        good = {"amount": 15000, "vendor": "Google Cloud", "date": "2026-02-01"}
        unknown = {"amount": 15000, "vendor": "不明", "date": "2026-02-01"}

        good_result = _calculate_extraction_confidence(good, method="gemini")
        unknown_result = _calculate_extraction_confidence(unknown, method="gemini")

        assert good_result["score"] > unknown_result["score"]

    def test_details_contain_extracted_values(self):
        """detailsに抽出された値が含まれる"""
        data = {"amount": 12345, "vendor": "テスト社", "date": "2026-01-15"}
        result = _calculate_extraction_confidence(data, method="gemini")

        details_text = " ".join(result["details"])
        assert "12,345" in details_text
        assert "テスト社" in details_text
        assert "2026-01-15" in details_text


class TestAnalyzeOriginalFilename:
    """元ファイル名分析のテスト"""

    def test_structured_filename(self):
        """構造化されたファイル名: リネーム不要"""
        result = analyze_original_filename("2026-02-01_AWS_15000.pdf")

        assert result["has_date"] is True
        assert result["has_vendor_like"] is True
        assert result["already_structured"] is True

    def test_date_only_filename(self):
        """日付のみのファイル名"""
        result = analyze_original_filename("20260201_invoice.pdf")

        assert result["has_date"] is True
        assert result["has_vendor_like"] is True  # "invoice" matches alpha pattern

    def test_vendor_only_filename(self):
        """ベンダー名のみのファイル名"""
        result = analyze_original_filename("aws_invoice.pdf")

        assert result["has_date"] is False
        assert result["has_vendor_like"] is True
        assert result["already_structured"] is False

    def test_generic_filename(self):
        """汎用的なファイル名（情報なし）"""
        result = analyze_original_filename("document.pdf")

        assert result["has_vendor_like"] is True  # "document" matches
        assert result["has_date"] is False

    def test_numeric_only_filename(self):
        """数値のみのファイル名（請求書番号など）"""
        result = analyze_original_filename("12345.pdf")

        assert result["has_date"] is False
        assert result["has_vendor_like"] is False

    def test_japanese_vendor_name(self):
        """日本語のベンダー名を含むファイル名"""
        result = analyze_original_filename("請求書_株式会社テスト.pdf")

        assert result["has_vendor_like"] is True

    def test_amount_in_filename(self):
        """金額を含むファイル名"""
        result = analyze_original_filename("invoice_¥15,000.pdf")

        assert result["has_amount"] is True

    def test_yen_amount_in_filename(self):
        """「円」表記の金額"""
        result = analyze_original_filename("請求書_15000円.pdf")

        assert result["has_amount"] is True

    def test_date_with_japanese_format(self):
        """日本語形式の日付"""
        result = analyze_original_filename("2026年2月1日_請求書.pdf")

        assert result["has_date"] is True

    def test_already_structured_with_dot_date(self):
        """ドット区切り日付を含む構造化されたファイル名"""
        result = analyze_original_filename("2026.02.01_Google_Cloud_50000.pdf")

        assert result["has_date"] is True
        assert result["has_vendor_like"] is True
        assert result["already_structured"] is True


class TestFormatInvoiceFilename:
    """ファイル名フォーマットのテスト"""

    def test_normal_format(self):
        result = format_invoice_filename("2026-02-01", "AWS", 15000)
        assert result == "2026-02-01_AWS_15000.pdf"

    def test_missing_date(self):
        result = format_invoice_filename(None, "AWS", 15000)
        assert result == "unknown-date_AWS_15000.pdf"

    def test_missing_vendor(self):
        result = format_invoice_filename("2026-02-01", None, 15000)
        assert result == "2026-02-01_unknown_15000.pdf"

    def test_missing_amount(self):
        result = format_invoice_filename("2026-02-01", "AWS", None)
        assert result == "2026-02-01_AWS_0.pdf"

    def test_special_chars_in_vendor(self):
        result = format_invoice_filename("2026-02-01", 'Test/Co "Ltd"', 15000)
        assert "/" not in result
        assert '"' not in result


class TestParseInvoiceFilename:
    """ファイル名パースのテスト"""

    def test_standard_format(self):
        result = parse_invoice_filename("2026-02-01_AWS_15000.pdf")
        assert result["date"] == "2026-02-01"
        assert result["vendor"] == "AWS"
        assert result["amount"] == 15000

    def test_yyyymmdd_format(self):
        result = parse_invoice_filename("20260201_AWS_15000.pdf")
        assert result["date"] == "2026-02-01"

    def test_vendor_with_underscore(self):
        result = parse_invoice_filename("2026-02-01_Google_Cloud_50000.pdf")
        assert result["vendor"] == "Google_Cloud"
        assert result["amount"] == 50000

    def test_japanese_vendor_filename(self):
        """日本語ベンダー名と全角スペースを含むファイル名"""
        result = parse_invoice_filename("20260225_テスト　テスト_8250.pdf")
        assert result["date"] == "2026-02-25"
        assert result["vendor"] == "テスト　テスト"
        assert result["amount"] == 8250

    def test_non_standard_filename(self):
        result = parse_invoice_filename("invoice.pdf")
        assert result["date"] is None
        assert result["amount"] is None
