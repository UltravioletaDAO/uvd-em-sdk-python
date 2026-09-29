"""EIP-3009 escrow pre-auth builder — sign-on-assignment (ADR-002).

The implementation is :mod:`uvd_x402_sdk.escrow_signing` (uvd-x402-sdk
>= 0.93.0), the one implementation of the escrow signature, whose ``__all__``
is a stable public API. This module keeps the import path
``uvd_em_sdk.escrow_signing`` and re-exports it: every name below IS the
SDK's object, not a copy (``tests/test_escrow_signing_reexport.py`` pins the
identity). Until 0.9.0 this package carried its own copy of the builder.

>>> PROTOCOL CONSTRAINT (ADR-002, verbatim) <<<
The EIP-3009 nonce is ``AuthCaptureEscrow.getHash(paymentInfo)`` which
**includes the receiver** — the escrow signature can only be created AT
ASSIGNMENT, when the worker is known. Never design flows that sign an escrow
auth before the worker is chosen ("stored pre-auth with late receiver fill"
is on-chain unsound).

What changed for callers when the copy gave way to the SDK (each one pinned
in ``tests/test_escrow_signing_reexport.py``):

1. **Domain refused.** For a chain in ``VERIFIED_USDC_DOMAINS`` (the USDC
   EIP-712 name/version read on-chain), a payment config that asserts another
   name or version is refused (``ValueError``) instead of signed.
2. **Amount conversion.** The bounty goes through the SDK's
   ``to_base_units``: float noise rounds (``0.3 - 0.1`` -> ``200000``; the copy
   truncated it to ``199999``) and a real digit below one base unit raises.
3. **Typehash checked.** ``payment_info_typehash`` must be
   ``AuthCaptureEscrow``'s ``PaymentInfo`` typehash
   (``ESCROW_PAYMENT_INFO_TYPEHASH``); any other value is refused before
   signing, since its nonce could never be the escrow's ``getHash``.
4. **primaryType.** The typed data handed to the wallet carries
   ``"primaryType": "ReceiveWithAuthorization"`` (the digest is unchanged).
5. **Unverified chain warning.** A chain not in ``VERIFIED_USDC_DOMAINS`` is
   still signed with the server's domain, and a warning is logged on
   ``uvd_x402_sdk.escrow_signing``.

``build_escrow_pre_auth`` also gained an optional ninth parameter,
``delegation_resolver`` (EIP-7702-delegated payers); the eight positional
parameters are unchanged. The clock and the salt are read by the SDK module:
code that freezes them for tests patches ``uvd_x402_sdk.escrow_signing.time``
and ``.secrets``.

Usage::

    from uvd_x402_sdk.wallet import EnvKeyAdapter
    from uvd_em_sdk.escrow_signing import build_escrow_pre_auth

    config = await client.h2a.payment_config()   # GET /h2a/payment-config
    payment_auth = build_escrow_pre_auth(
        payment_config=config,
        network="base",
        payer="0xPublisher...",
        receiver="0xWorker...",          # committed by the nonce
        amount_usd=0.10,
        deadline=task_deadline_epoch,    # release window outlasts it
        wallet=EnvKeyAdapter(),
    )
    await client.tasks.assign(task_id, executor_id, payment_auth=payment_auth)
"""

from __future__ import annotations

from uvd_x402_sdk.escrow_signing import (
    DEFAULT_MAX_FEE_BPS,
    DEFAULT_MIN_FEE_BPS,
    ESCROW_DEPOSIT_LIMIT_USD,
    ESCROW_PAYMENT_INFO_TYPEHASH,
    ESCROW_TIER_WINDOWS,
    LIFECYCLE_ACTIONS,
    LIFECYCLE_DEFAULT_DEADLINE_SECS,
    LIFECYCLE_DOMAIN_NAME,
    LIFECYCLE_DOMAIN_VERSION,
    LIFECYCLE_MAX_DEADLINE_SECS,
    LIFECYCLE_ORDER_TYPES,
    LIFECYCLE_PRIMARY_TYPE,
    OPERATOR_FEE_BPS,
    PAYMENT_INFO_ABI,
    PAYMENT_INFO_TYPE,
    RECEIVE_WITH_AUTHORIZATION_TYPES,
    REFUND_WINDOW_SEC,
    REQUIRED_NETWORK_KEYS,
    REVIEW_WINDOW_SEC,
    USDC_DECIMALS,
    VERIFIED_USDC_DOMAINS,
    build_escrow_pre_auth,
    build_lifecycle_auth,
    build_lifecycle_typed_data,
    compute_escrow_nonce,
    lifecycle_auth_from_signature,
)

# Names the copy defined that the SDK's stable API does not list: kept so no
# import of 0.8.0 breaks. The private two are the same objects as the public
# names the SDK gives them.
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_PAYMENT_INFO_ABI = PAYMENT_INFO_ABI
_REQUIRED_NETWORK_KEYS = REQUIRED_NETWORK_KEYS

__all__ = [
    # signing
    "build_escrow_pre_auth",
    "compute_escrow_nonce",
    "build_lifecycle_typed_data",
    "build_lifecycle_auth",
    "lifecycle_auth_from_signature",
    # the signed types
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
    # the guards' tables
    "VERIFIED_USDC_DOMAINS",
    "REQUIRED_NETWORK_KEYS",
    "ESCROW_DEPOSIT_LIMIT_USD",
    "OPERATOR_FEE_BPS",
    "DEFAULT_MIN_FEE_BPS",
    "DEFAULT_MAX_FEE_BPS",
    "USDC_DECIMALS",
    # the windows
    "ESCROW_TIER_WINDOWS",
    "REVIEW_WINDOW_SEC",
    "REFUND_WINDOW_SEC",
    # kept from the 0.8.0 copy
    "ZERO_ADDRESS",
]
