"""Tests for the fee calculator — flat 13% (1300 bps), StaticFeeCalculator parity."""

import pytest

from em_plugin_sdk.fees import (
    calculate_fee,
    calculate_reverse_fee,
    get_fee_rate,
    FEE_BPS,
    MAX_FEE_BPS,
)
from em_plugin_sdk.models import TaskCategory


class TestFeeConstants:
    def test_fee_bps_is_1300(self):
        """SDK-30: production fee is flat 1300 bps (StaticFeeCalculator)."""
        assert FEE_BPS == 1300

    def test_max_fee_bps_is_1800(self):
        """Headroom for the signed maxFeeBps (x402r protocol fee up to 5%)."""
        assert MAX_FEE_BPS == 1800
        assert MAX_FEE_BPS >= FEE_BPS


class TestFeeCalculation:
    def test_flat_13_percent(self):
        fee = calculate_fee(10.00, "simple_action")
        assert fee.fee_rate == 0.13
        assert fee.fee_amount == 1.30
        assert fee.worker_amount == 8.70
        assert fee.gross_amount == 10.00

    def test_canonical_dime_split(self):
        """Canonical fee model: $0.10 → worker $0.087 / treasury $0.013 exact."""
        fee = calculate_fee(0.10)
        assert fee.fee_amount == 0.013
        assert fee.worker_amount == 0.087

    def test_rate_is_flat_for_every_category(self):
        """No more per-category 11-13% table — every category pays 1300 bps."""
        for cat in TaskCategory:
            fee = calculate_fee(10.00, cat)
            assert fee.fee_rate == 0.13
            assert fee.fee_amount == 1.30

    def test_small_bounty_mirrors_onchain_floor(self):
        """$0.05 → 50000 units * 1300 // 10000 = 6500 units = $0.0065.

        No $0.01 minimum: the on-chain StaticFeeCalculator split has none.
        """
        fee = calculate_fee(0.05)
        assert fee.fee_amount == 0.0065
        assert fee.worker_amount == 0.0435

    def test_accepts_enum(self):
        fee = calculate_fee(10.00, TaskCategory.PHYSICAL_PRESENCE)
        assert fee.category == "physical_presence"
        assert fee.fee_rate == 0.13

    def test_unknown_category_same_flat_rate(self):
        fee = calculate_fee(10.00, "unknown_category")
        assert fee.fee_rate == 0.13

    def test_zero_bounty_raises(self):
        with pytest.raises(ValueError):
            calculate_fee(0, "simple_action")

    def test_negative_bounty_raises(self):
        with pytest.raises(ValueError):
            calculate_fee(-5, "simple_action")


class TestReverseFee:
    def test_reverse_fee_basic(self):
        """Worker wants $10 → bounty = ceil(10 * 10000 / 8700) at 6 decimals."""
        fee = calculate_reverse_fee(10.00, "simple_action")
        assert fee.gross_amount == 11.494253
        assert fee.worker_amount >= 10.00

    def test_reverse_fee_zero_raises(self):
        with pytest.raises(ValueError):
            calculate_reverse_fee(0, "simple_action")


class TestGetFeeRate:
    def test_string_category(self):
        assert get_fee_rate("physical_presence") == 0.13

    def test_enum_category(self):
        assert get_fee_rate(TaskCategory.HUMAN_AUTHORITY) == 0.13

    def test_default(self):
        assert get_fee_rate() == 0.13
