"""
Tests for the shared cell primitives in core.py.

These are the conversions every other component relies on, so they get the
most direct coverage: a wrong answer here is a wrong control total.
"""

from __future__ import annotations

import pytest

from core import (
    MoneyParseError,
    cents_to_money,
    format_money,
    is_blank,
    money_to_cents,
    normalize_invoice,
    normalize_key,
    normalize_text,
)


class TestMoneyToCents:
    """Money is parsed into integer cents, half-up."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, 0),
            (100, 10_000),
            (1234.56, 123_456),
            (1234.565, 123_457),      # half-up, not banker's rounding
            (1234.564, 123_456),
            (-500.25, -50_025),
        ],
    )
    def test_numbers(self, value, expected):
        assert money_to_cents(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("$1,234.56", 123_456),
            ("1,234.56", 123_456),
            ("1234.56", 123_456),
            ("  1234.56  ", 123_456),
            ("USD 500", 50_000),
            ("500 USD", 50_000),
            ("CAD 99.99", 9_999),
            ("USD1,234.56", 123_456),
        ],
    )
    def test_formatted_strings(self, value, expected):
        assert money_to_cents(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("(1,234.56)", -123_456),
            ("(500)", -50_000),
        ],
    )
    def test_accounting_negatives(self, value, expected):
        """Parentheses mean negative, the way an ERP export writes them."""
        assert money_to_cents(value) == expected

    @pytest.mark.parametrize("value", ["", None, "   ", float("nan")])
    def test_blank_is_zero(self, value):
        assert money_to_cents(value) == 0

    @pytest.mark.parametrize("value", ["abc", "1,2,3.4.5", "$", "12abc", True])
    def test_unparseable_raises(self, value):
        """
        A malformed amount must never collapse to zero.

        A silent zero looks like a real "this invoice is worth nothing" to
        every downstream control total, so the parse has to fail loudly.
        """
        with pytest.raises(MoneyParseError):
            money_to_cents(value)

    def test_error_message_names_the_value(self):
        with pytest.raises(MoneyParseError, match="not-a-number"):
            money_to_cents("not-a-number")


class TestCentsToMoney:
    def test_round_trip(self):
        assert cents_to_money(money_to_cents("1,234.56")) == 1234.56

    @pytest.mark.parametrize(
        ("cents", "expected"),
        [(0, 0.0), (1, 0.01), (99, 0.99), (100, 1.0), (-50_025, -500.25)],
    )
    def test_values(self, cents, expected):
        assert cents_to_money(cents) == expected

    def test_format_money(self):
        assert format_money(123_456) == "1,234.56"
        assert format_money(0) == "0.00"


class TestNormalizeKey:
    """
    Keys are canonical, so the customer-name fallbacks behave identically no
    matter which module performs the comparison.
    """

    @pytest.mark.parametrize(
        "value",
        ["ACME-CORP", "ACME Corp.", "Acme  Corp", "acme corp", "ACME_CORP"],
    )
    def test_equivalent_spellings_match(self, value):
        assert normalize_key(value) == normalize_key("ACME Corp.")

    def test_case_insensitive(self):
        assert normalize_key("ACME") == normalize_key("acme")

    def test_punctuation_removed(self):
        assert normalize_key("A-B.C&D") == "abcd"

    def test_spaces_removed(self):
        assert normalize_key("A B") == "ab"

    def test_empty_stays_empty(self):
        assert normalize_key("") == ""
        assert normalize_key(None) == ""


class TestNormalizeText:
    def test_collapses_whitespace(self):
        assert normalize_text("a   b \t c") == "a b c"

    def test_strips(self):
        assert normalize_text("  hello  ") == "hello"

    def test_none_is_empty(self):
        assert normalize_text(None) == ""

    def test_nan_is_empty(self):
        assert normalize_text(float("nan")) == ""

    def test_keeps_meaningful_punctuation(self):
        """normalize_text keeps punctuation; only normalize_key strips it."""
        assert normalize_text("INV-900175") == "INV-900175"


class TestNormalizeInvoice:
    def test_uppercases(self):
        assert normalize_invoice("inv-900175") == "INV-900175"

    def test_blank(self):
        assert normalize_invoice("") == ""


class TestIsBlank:
    @pytest.mark.parametrize("value", [None, "", "   ", float("nan")])
    def test_blank_values(self, value):
        assert is_blank(value)

    @pytest.mark.parametrize("value", [0, 0.0, "x", False])
    def test_non_blank_values(self, value):
        assert not is_blank(value)
