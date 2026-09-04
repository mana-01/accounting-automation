"""Tests for CSV parser."""

import pytest
from src.services.csv_parser import CSVParser


class TestCSVParser:
    """CSVパーサーのテスト"""

    def setup_method(self):
        self.parser = CSVParser()

    def test_parse_generic_csv(self):
        """汎用CSVのパーステスト"""
        csv_content = b"""date,description,amount
2026-01-15,AWS Monthly,10000
2026-01-20,Google Cloud,-5000
"""
        transactions = self.parser.parse(csv_content, format_type="generic")

        assert len(transactions) == 2
        assert transactions[0].date == "2026/01/15"
        assert transactions[0].description == "AWS Monthly"
        assert transactions[0].amount == 10000

    def test_parse_amount_with_comma(self):
        """カンマ付き金額のパーステスト"""
        csv_content = b"""date,description,amount
2026-01-15,Test,10,000
"""
        transactions = self.parser.parse(csv_content, format_type="generic")

        assert len(transactions) == 1
        assert transactions[0].amount == 10000

    def test_parse_negative_amount(self):
        """マイナス金額のパーステスト"""
        csv_content = b"""date,description,amount
2026-01-15,Refund,-5000
"""
        transactions = self.parser.parse(csv_content, format_type="generic")

        assert len(transactions) == 1
        assert transactions[0].amount == 5000
        assert transactions[0].transaction_type == "debit"

    def test_supported_formats(self):
        """サポートフォーマット一覧のテスト"""
        formats = self.parser.get_supported_formats()

        assert "rakuten_card" in formats
        assert "smbc_card" in formats
        assert "mufg_bank" in formats
        assert "generic" in formats
