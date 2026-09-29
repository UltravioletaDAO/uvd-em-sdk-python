"""uvd_em_sdk.escrow_signing re-exports uvd_x402_sdk.escrow_signing (0.9.0).

Until 0.8.0 this package carried its own copy of the escrow EIP-3009 builder.
Since 0.9.0 the one implementation is the SDK's, and this module keeps only
the import path. Pinned here:

- identity: every re-exported name IS the SDK's object, and the module
  defines no function of its own (a copy pasted back turns this red);
- the names the 0.8.0 copy exposed still import;
- the five behaviour changes that came with the SDK's implementation, as
  numbered in the module docstring of ``uvd_em_sdk/escrow_signing.py``.

The golden vectors (``tests/test_escrow_signing.py``) are unchanged and green:
for the inputs the copy covered, the SDK signs the same bytes.
"""

import inspect
import json
import logging
from decimal import Decimal

import pytest
import uvd_x402_sdk.escrow_signing as sdk_escrow

import uvd_em_sdk
import uvd_em_sdk.escrow_signing as em_escrow
from tests.test_escrow_signing import (
    BASE_NETWORK,
    PAYER,
    PAYMENT_CONFIG,
    TYPEHASH,
    WORKER,
    FakeWallet,
)

# uvd_x402_sdk.escrow_signing.__all__ at 0.93.0: the stable public API.
SDK_0_93_ALL = (
    "build_escrow_pre_auth",
    "compute_escrow_nonce",
    "build_lifecycle_typed_data",
    "build_lifecycle_auth",
    "lifecycle_auth_from_signature",
    "RECEIVE_WITH_AUTHORIZATION_TYPES",
    "PAYMENT_INFO_TYPE",
    "PAYMENT_INFO_ABI",
    "ESCROW_PAYMENT_INFO_TYPEHASH",
    "LIFECYCLE_ACTIONS",
    "LIFECYCLE_DEFAULT_DEADLINE_SECS",
    "LIFECYCLE_DOMAIN_NAME",
    "LIFECYCLE_DOMAIN_VERSION",
    "LIFECYCLE_MAX_DEADLINE_SECS",
    "LIFECYCLE_ORDER_TYPES",
    "LIFECYCLE_PRIMARY_TYPE",
    "VERIFIED_USDC_DOMAINS",
    "REQUIRED_NETWORK_KEYS",
    "ESCROW_DEPOSIT_LIMIT_USD",
    "OPERATOR_FEE_BPS",
    "DEFAULT_MIN_FEE_BPS",
    "DEFAULT_MAX_FEE_BPS",
    "USDC_DECIMALS",
    "ESCROW_TIER_WINDOWS",
    "REVIEW_WINDOW_SEC",
    "REFUND_WINDOW_SEC",
)

# Module-level names of em-plugin-sdk 0.8.0's escrow_signing (stdlib imports
# and the private _require_eth_libs helper aside).
COPY_0_8_0_NAMES = (
    "ZERO_ADDRESS",
    "USDC_DECIMALS",
    "ESCROW_DEPOSIT_LIMIT_USD",
    "OPERATOR_FEE_BPS",
    "DEFAULT_MIN_FEE_BPS",
    "DEFAULT_MAX_FEE_BPS",
    "RECEIVE_WITH_AUTHORIZATION_TYPES",
    "_PAYMENT_INFO_ABI",
    "ESCROW_TIER_WINDOWS",
    "REVIEW_WINDOW_SEC",
    "REFUND_WINDOW_SEC",
    "_REQUIRED_NETWORK_KEYS",
    "compute_escrow_nonce",
    "build_escrow_pre_auth",
)


def _cfg(**network_overrides):
    cfg = json.loads(json.dumps(PAYMENT_CONFIG))
    cfg["escrow"]["networks"]["base"].update(network_overrides)
    return cfg


class TestIdentity:
    def test_build_escrow_pre_auth_is_the_sdk_function(self):
        assert em_escrow.build_escrow_pre_auth is sdk_escrow.build_escrow_pre_auth
        assert uvd_em_sdk.build_escrow_pre_auth is sdk_escrow.build_escrow_pre_auth

    def test_compute_escrow_nonce_is_the_sdk_function(self):
        assert em_escrow.compute_escrow_nonce is sdk_escrow.compute_escrow_nonce
        assert uvd_em_sdk.compute_escrow_nonce is sdk_escrow.compute_escrow_nonce

    @pytest.mark.parametrize("name", SDK_0_93_ALL)
    def test_the_sdk_stable_api_is_reexported_as_the_same_object(self, name):
        assert name in em_escrow.__all__
        assert getattr(em_escrow, name) is getattr(sdk_escrow, name)

    def test_every_exported_name_is_the_sdk_object(self):
        for name in em_escrow.__all__:
            if name == "ZERO_ADDRESS":  # a constant, not part of the SDK's API
                continue
            assert getattr(em_escrow, name) is getattr(sdk_escrow, name), name

    def test_the_module_defines_no_function_of_its_own(self):
        """A copy of the builder pasted back into this module turns this red."""
        own = [
            name
            for name, value in vars(em_escrow).items()
            if inspect.isfunction(value) and value.__module__ == em_escrow.__name__
        ]
        assert own == []


class TestTheNamesOf080StillImport:
    @pytest.mark.parametrize("name", COPY_0_8_0_NAMES)
    def test_name_is_there(self, name):
        assert hasattr(em_escrow, name)

    def test_the_kept_constants(self):
        assert em_escrow.ZERO_ADDRESS == "0x" + "0" * 40
        assert em_escrow._PAYMENT_INFO_ABI is sdk_escrow.PAYMENT_INFO_ABI
        assert em_escrow._REQUIRED_NETWORK_KEYS is sdk_escrow.REQUIRED_NETWORK_KEYS

    def test_the_eight_positional_parameters_are_unchanged(self):
        """Callers of the copy passed eight arguments, positionally; the SDK
        added a ninth, optional (``delegation_resolver``), at the end."""
        params = list(inspect.signature(em_escrow.build_escrow_pre_auth).parameters)
        assert params[:8] == [
            "payment_config",
            "network",
            "payer",
            "receiver",
            "amount_usd",
            "deadline",
            "wallet",
            "tier",
        ]
        assert params[8:] == ["delegation_resolver"]


class TestBehaviourThatCameWithTheSDK:
    """The five changes, numbered as in uvd_em_sdk/escrow_signing.py."""

    def test_1_a_domain_other_than_the_verified_one_is_refused(self):
        wallet = FakeWallet()
        # Base's USDC is "USD Coin" on-chain; "USDC" is Arc's and Celo's name.
        with pytest.raises(ValueError, match="EIP-712 domain mismatch"):
            em_escrow.build_escrow_pre_auth(
                _cfg(usdc_domain_name="USDC"),
                "base",
                PAYER,
                WORKER,
                "0.10",
                None,
                wallet,
            )
        assert wallet.typed_data_calls == []

    def test_2_float_noise_rounds_to_the_intended_amount(self):
        amount = 0.3 - 0.1  # 0.19999999999999998
        # What the 0.8.0 copy computed: int() truncation, one base unit short.
        assert int(Decimal(str(amount)) * 10**6) == 199999
        header = em_escrow.build_escrow_pre_auth(
            PAYMENT_CONFIG, "base", PAYER, WORKER, amount, None, FakeWallet()
        )
        payload = json.loads(header)["payload"]
        assert payload["paymentInfo"]["maxAmount"] == "200000"
        assert payload["authorization"]["value"] == "200000"

    def test_2_a_digit_below_one_base_unit_is_refused(self):
        with pytest.raises(ValueError):
            em_escrow.build_escrow_pre_auth(
                PAYMENT_CONFIG, "base", PAYER, WORKER, "0.1000001", None, FakeWallet()
            )

    def test_3_a_typehash_other_than_the_escrow_s_is_refused(self):
        wallet = FakeWallet()
        cfg = json.loads(json.dumps(PAYMENT_CONFIG))
        cfg["escrow"]["payment_info_typehash"] = "0x" + "ab" * 32
        with pytest.raises(ValueError, match="not AuthCaptureEscrow's PaymentInfo"):
            em_escrow.build_escrow_pre_auth(
                cfg, "base", PAYER, WORKER, "0.10", None, wallet
            )
        assert wallet.typed_data_calls == []

    def test_3_the_golden_vector_s_typehash_is_the_escrow_s(self):
        assert TYPEHASH.lower() == em_escrow.ESCROW_PAYMENT_INFO_TYPEHASH

    def test_4_the_typed_data_names_its_primary_type(self):
        wallet = FakeWallet()
        em_escrow.build_escrow_pre_auth(
            PAYMENT_CONFIG, "base", PAYER, WORKER, "0.10", None, wallet
        )
        typed = wallet.typed_data_calls[0]
        assert typed["primaryType"] == "ReceiveWithAuthorization"
        assert list(typed["types"]) == ["ReceiveWithAuthorization"]

    def test_5_an_unverified_chain_is_signed_with_a_warning(self, caplog):
        cfg = json.loads(json.dumps(PAYMENT_CONFIG))
        cfg["escrow"]["networks"] = {
            "base-sepolia": {**BASE_NETWORK, "chain_id": 84532}
        }
        assert 84532 not in em_escrow.VERIFIED_USDC_DOMAINS
        with caplog.at_level(logging.WARNING, logger="uvd_x402_sdk.escrow_signing"):
            header = em_escrow.build_escrow_pre_auth(
                cfg, "base-sepolia", PAYER, WORKER, "0.10", None, FakeWallet()
            )
        assert json.loads(header)["paymentRequirements"]["network"] == "eip155:84532"
        assert any(
            "UNVERIFIED chain 84532" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
