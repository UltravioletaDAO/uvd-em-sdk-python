"""The FeedbackDelegate contracts an EIP-7702 authorization may point at.

An EIP-7702 authorization sets the code of the signing account to the contract
it names, and that code then acts AS the account. The first rating a wallet
authors on a chain (:meth:`~uvd_em_sdk.resources.reputation.ReputationResource.submit_relayed_rating`)
carries one, and the contract it names comes from Execution Market's
``prepare`` answer: a server's word. :func:`verify_feedback_delegate` checks
that word against the table below, which ships with the SDK, so a compromised
or mistaken server cannot obtain a delegation to any other contract, or one
valid on every chain (``chainId`` 0).

**Pinned snapshot**: ``FEEDBACK_DELEGATES`` is GENERATED from Execution
Market's deployment record (``contracts/deployments/feedback-delegate.json``:
per network, its chain id and the address of the v4 FeedbackDelegate, nothing
else), pinned in ``vendor/em/feedback-delegate.json`` by
``scripts/vendor_em.py``, by ``scripts/sync_feedback_delegates.py``;
``tests/test_feedback_delegates_sync.py`` fails when it drifts. To resync::

    python scripts/vendor_em.py --em-repo <Execution Market clone>
    python scripts/sync_feedback_delegates.py && ruff format .

A delegate deployed after the SDK you run was released is refused until a
release carries it. That is the point: the table travels with the code, not
with the answer it checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .exceptions import EMDelegateNotPinnedError

#: An EVM address as compared here: ``0x`` (lower-case x) and 40 hex digits,
#: nothing around it. Execution Market refuses a ``0X`` prefix the same way.
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


@dataclass(frozen=True)
class FeedbackDelegate:
    """The FeedbackDelegate Execution Market pins on one network."""

    network: str
    chain_id: int
    address: str


# ---------------------------------------------------------------------------
# Pinned table (synced from vendor/em/feedback-delegate.json)
# Regenerate with: python scripts/sync_feedback_delegates.py && ruff format .
# ---------------------------------------------------------------------------

# --- BEGIN GENERATED: FEEDBACK DELEGATES (scripts/sync_feedback_delegates.py) ---
FEEDBACK_DELEGATES: dict[str, FeedbackDelegate] = {
    "base": FeedbackDelegate(
        network="base",
        chain_id=8453,
        address="0x260D3D0258680aA458D0EBB8BcAE8A2f68bf6163",
    ),
    "ethereum": FeedbackDelegate(
        network="ethereum",
        chain_id=1,
        address="0x9577F05D10D2052C75D115e3a0A8f9Dc0cBb7A7b",
    ),
    "polygon": FeedbackDelegate(
        network="polygon",
        chain_id=137,
        address="0x1dfc1A578bf321A42c441B4bd60fD5d7bb4BFaD5",
    ),
    "arbitrum": FeedbackDelegate(
        network="arbitrum",
        chain_id=42161,
        address="0xf670C69BCbb2453FaE5Ec009c2b6dd934BE46A7f",
    ),
    "optimism": FeedbackDelegate(
        network="optimism",
        chain_id=10,
        address="0x794C907FdfC71BFaF0b86D0e463BBD6E949A31bA",
    ),
    "celo": FeedbackDelegate(
        network="celo",
        chain_id=42220,
        address="0x0Dff14dFF648769cB8C3D5F5a150f32Ca2BB9511",
    ),
    "bsc": FeedbackDelegate(
        network="bsc",
        chain_id=56,
        address="0xe25cF9B9F5A3B5faa7628c751466df0166d96B59",
    ),
    "monad": FeedbackDelegate(
        network="monad",
        chain_id=143,
        address="0x794C907FdfC71BFaF0b86D0e463BBD6E949A31bA",
    ),
    "arc": FeedbackDelegate(
        network="arc",
        chain_id=5042,
        address="0x955Cc9fB9aB95FC0821ae74197D273dde5dA84f1",
    ),
    "base-sepolia": FeedbackDelegate(
        network="base-sepolia",
        chain_id=84532,
        address="0x9551263b9B83b1A737D55fd5e67Fb6D60e4eF787",
    ),
}
# --- END GENERATED: FEEDBACK DELEGATES ---

_BY_CHAIN: dict[int, FeedbackDelegate] = {
    d.chain_id: d for d in FEEDBACK_DELEGATES.values()
}


def get_feedback_delegate(chain_id: int) -> FeedbackDelegate | None:
    """The pinned FeedbackDelegate of ``chain_id``, or ``None``.

    ``chain_id`` must be an ``int``: ``True`` is not chain 1 here.
    """
    if type(chain_id) is not int:
        return None
    return _BY_CHAIN.get(chain_id)


def _address(value: Any) -> str | None:
    """``value`` when it is an address written 0x and 40 hex digits, else None."""
    if isinstance(value, str) and _EVM_ADDRESS.fullmatch(value):
        return value
    return None


def verify_feedback_delegate(
    prepared: Mapping[str, Any],
    authorization: Mapping[str, Any] | None = None,
) -> FeedbackDelegate:
    """The pinned FeedbackDelegate ``prepared`` names, or a refusal.

    ``prepared`` is the answer of
    :meth:`~uvd_em_sdk.resources.reputation.ReputationResource.prepare_relayed_rating`.
    Call this on it BEFORE signing an EIP-7702 authorization (when
    ``prepared["delegated"]`` is ``False``), and sign over the ``chain_id``
    and ``address`` it returns, with ``prepared["account_nonce"]``.
    :meth:`~uvd_em_sdk.resources.reputation.ReputationResource.submit_relayed_rating`
    runs it again, with the authorization, before it sends anything.

    Raises :class:`~uvd_em_sdk.exceptions.EMDelegateNotPinnedError`, whose
    ``code`` names the first check that failed, in this order:

    * ``chain_id_invalid``: ``prepared["chain_id"]`` is absent, ``None``, 0,
      negative or not an ``int``. 0 is EIP-7702's wildcard: an authorization
      signed for it delegates the account on every EVM chain at once.
    * ``chain_not_pinned``: the SDK pins no FeedbackDelegate on that chain.
    * ``network_mismatch``: ``prepared["network"]``, when present, is not the
      network of that chain.
    * ``delegate_not_pinned``: ``prepared["delegate"]`` is not the pinned
      address of that chain (compared without case; it must be written 0x
      and 40 hex digits).

    With ``authorization`` (the dict the wallet produced), also:

    * ``authorization_invalid``: it is not a mapping.
    * ``authorization_wrong_chain``: its ``chainId`` is not exactly
      ``prepared["chain_id"]`` as an ``int`` (0 included).
    * ``authorization_wrong_delegate``: its ``address`` is not the pinned
      delegate (same comparison as above).

    No request is made: this is a local check.
    """
    chain_id = prepared.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise EMDelegateNotPinnedError(
            "chain_id_invalid",
            f"prepare answered chain_id {chain_id!r}: an EIP-7702 authorization "
            "needs this chain's id, never 0 (0 would delegate the account on "
            "every chain). Nothing was signed or sent.",
            {"chain_id": chain_id},
        )
    pinned = _BY_CHAIN.get(chain_id)
    if pinned is None:
        raise EMDelegateNotPinnedError(
            "chain_not_pinned",
            f"this SDK pins no FeedbackDelegate on chain {chain_id}, so it lets "
            "no EIP-7702 authorization be sent for it. Nothing was signed or "
            "sent; a release of uvd-em-sdk that pins one is needed.",
            {"chain_id": chain_id},
        )
    network = prepared.get("network")
    if network is not None and network != pinned.network:
        raise EMDelegateNotPinnedError(
            "network_mismatch",
            f"prepare answered network {network!r} with chain_id {chain_id}, "
            f"which is {pinned.network}. Nothing was signed or sent.",
            {
                "chain_id": chain_id,
                "network": network,
                "pinned_network": pinned.network,
            },
        )
    delegate = _address(prepared.get("delegate"))
    if delegate is None or delegate.lower() != pinned.address.lower():
        raise EMDelegateNotPinnedError(
            "delegate_not_pinned",
            f"prepare named {prepared.get('delegate')!r} as the FeedbackDelegate "
            f"on {pinned.network} (chain {chain_id}), and the one this SDK pins "
            f"there is {pinned.address}. Do not sign a delegation to any other "
            "contract. Nothing was signed or sent.",
            {
                "chain_id": chain_id,
                "network": pinned.network,
                "delegate": prepared.get("delegate"),
                "pinned_delegate": pinned.address,
            },
        )
    if authorization is None:
        return pinned
    if not isinstance(authorization, Mapping):
        raise EMDelegateNotPinnedError(
            "authorization_invalid",
            "the EIP-7702 authorization is not a mapping with chainId and "
            "address. Nothing was sent.",
            {"authorization_type": type(authorization).__name__},
        )
    signed_chain = authorization.get("chainId")
    if type(signed_chain) is not int or signed_chain != chain_id:
        raise EMDelegateNotPinnedError(
            "authorization_wrong_chain",
            f"the EIP-7702 authorization is signed for chainId {signed_chain!r}, "
            f"and this rating is on {pinned.network} (chain {chain_id}). Sign it "
            f"again with chainId {chain_id}, never 0. Nothing was sent.",
            {
                "chain_id": chain_id,
                "network": pinned.network,
                "authorization_chain_id": signed_chain,
            },
        )
    signed_address = _address(authorization.get("address"))
    if signed_address is None or signed_address.lower() != pinned.address.lower():
        raise EMDelegateNotPinnedError(
            "authorization_wrong_delegate",
            f"the EIP-7702 authorization delegates the account to "
            f"{authorization.get('address')!r}, and the FeedbackDelegate this SDK "
            f"pins on {pinned.network} is {pinned.address}. Sign it again with "
            f"that address. Nothing was sent.",
            {
                "chain_id": chain_id,
                "network": pinned.network,
                "authorization_address": authorization.get("address"),
                "pinned_delegate": pinned.address,
            },
        )
    return pinned
