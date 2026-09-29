"""Reputation resource — client.reputation.get_agent(), .rate_worker(), etc.

Wraps the ERC-8004 on-chain reputation and identity endpoints.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..models import (
    AgentIdentity,
    AgentReputation,
    CrossChainReputation,
    ReputationNetworkPreference,
)

if TYPE_CHECKING:
    from ..client import EMClient


class ReputationResource:
    """ERC-8004 reputation and identity operations.

    Usage::

        rep = await client.reputation.get_agent(2106)
        identity = await client.reputation.get_agent_identity(2106)
        board = await client.reputation.leaderboard()
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    # -- read ---------------------------------------------------------------

    async def get_agent(
        self, agent_id: int, *, network: str = "base"
    ) -> AgentReputation:
        """Get reputation summary for an agent by ERC-8004 token ID."""
        data = await self._client._request(
            "GET",
            f"/reputation/agents/{agent_id}",
            params={"network": network},
        )
        return AgentReputation.model_validate(data)

    async def get_agent_identity(
        self, agent_id: int, *, network: str = "base"
    ) -> AgentIdentity:
        """Get on-chain identity for an agent."""
        data = await self._client._request(
            "GET",
            f"/reputation/agents/{agent_id}/identity",
            params={"network": network},
        )
        return AgentIdentity.model_validate(data)

    async def leaderboard(self, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """Get the worker reputation leaderboard."""
        return await self._client._request(
            "GET",
            "/reputation/leaderboard",
            params={"limit": limit, "offset": offset},
        )

    async def info(self) -> dict[str, Any]:
        """Get ERC-8004 integration status and configuration."""
        return await self._client._request("GET", "/reputation/info")

    async def networks(self) -> list[str]:
        """List supported ERC-8004 networks."""
        data = await self._client._request("GET", "/reputation/networks")
        return data.get("networks", []) if isinstance(data, dict) else data

    async def em_reputation(self) -> AgentReputation:
        """Get Execution Market's own on-chain reputation."""
        data = await self._client._request("GET", "/reputation/em")
        return AgentReputation.model_validate(data)

    async def em_identity(self) -> AgentIdentity:
        """Get Execution Market's on-chain identity."""
        data = await self._client._request("GET", "/reputation/em/identity")
        return AgentIdentity.model_validate(data)

    async def get_feedback(self, task_id: str) -> dict[str, Any]:
        """Get the off-chain feedback document for a task."""
        return await self._client._request("GET", f"/reputation/feedback/{task_id}")

    async def get_reputation_network(
        self, wallet_address: str
    ) -> ReputationNetworkPreference:
        """Get the reputation-network preference for a wallet (public read).

        The chain where this user's ERC-8004 reputation is written (ratee-
        driven, decoupled from the payment network). Default ``"base"``.
        Inspect :pyattr:`~em_plugin_sdk.models.ReputationNetworkPreference.feature_enabled`
        before offering a selector: while ``False`` the preference is
        read-only ("coming soon") and :meth:`set_reputation_network` yields
        a 409.
        """
        data = await self._client._request(
            "GET", f"/workers/{wallet_address}/reputation-network"
        )
        return ReputationNetworkPreference.model_validate(data)

    async def cross_chain(self, wallet_address: str) -> CrossChainReputation:
        """Get the multi-chain reputation describe.net computed for a wallet.

        EM serves its snapshot of describe.net's aggregate (equal chain
        weight; chains with no eligible rating are counted in
        ``chains_skipped``, not scored as 0). Use this aggregate + its
        per-chain breakdown for display — never a single-chain score
        (anti-laundering) — and show ``retrieved_at`` beside it so the reader
        can see how old the number is.

        :pyattr:`~em_plugin_sdk.models.CrossChainReputation.final_score` is
        ``None`` when there is no evidence. Branch on it; never read it as 0.
        """
        data = await self._client._request(
            "GET", f"/reputation/wallet/{wallet_address}/cross-chain"
        )
        return CrossChainReputation.model_validate(data)

    # -- write (require auth) -----------------------------------------------

    async def set_reputation_network(
        self, wallet_address: str, network: str
    ) -> dict[str, Any]:
        """Set the chain where this wallet's ERC-8004 reputation is written.

        ``PATCH /workers/{wallet}/reputation-network`` — requires an ERC-8128
        wallet signature (attach a ``WalletAdapter`` to :class:`EMClient`;
        non-GET requests are auto-signed). The signing wallet must match
        ``wallet_address`` (403 otherwise). Fails with 409 while the feature
        is disabled (see :meth:`get_reputation_network`) and 429 during the
        anti-laundering cooldown between changes.
        """
        return await self._client._request(
            "PATCH",
            f"/workers/{wallet_address}/reputation-network",
            json={"reputation_network": network},
        )

    async def rate_worker(
        self,
        task_id: str,
        score: int,
        *,
        comment: str | None = None,
        proof_tx: str | None = None,
        worker_address: str | None = None,
    ) -> dict[str, Any]:
        """Agent rates a worker after task completion (on-chain via Facilitator).

        ``POST /reputation/workers/rate`` takes the TASK id (36-char UUID), not
        the submission id — a ``submission_id`` body used to 422 here, which is
        how this signature got fixed (2026-08-27, first real external rating).

        ``proof_tx`` is the payout transaction hash the approve returned
        (``payment_tx``). Passing it makes the rating VERIFIED feedback tied to
        a real trade — the server builds the ERC-8004 ProofOfPayment from it;
        do not build one by hand. Without it the rating is still recorded,
        marked unverified. Optional because rejection paths and cancelled
        tasks legitimately have no payout.
        """
        body: dict[str, Any] = {"task_id": task_id, "score": score}
        if comment:
            body["comment"] = comment
        if proof_tx is not None:
            body["proof_tx"] = proof_tx
        if worker_address is not None:
            body["worker_address"] = worker_address
        return await self._client._request(
            "POST", "/reputation/workers/rate", json=body
        )

    async def rate_agent(
        self,
        task_id: str,
        score: int,
        *,
        comment: str | None = None,
        agent_id: int | None = None,
        proof_tx: str | None = None,
    ) -> dict[str, Any]:
        """Worker rates an agent after task completion (on-chain via Facilitator).

        ``agent_id`` (the requester's numeric ERC-8004 token id) is optional:
        when omitted, the server resolves it from the task — its
        ``erc8004_agent_id`` when set, else the publisher's on-chain identity
        looked up from the publisher wallet. Pass it explicitly only to pin a
        specific identity (it is still validated against the task).

        ``proof_tx``: hash of the payout you received, for verified feedback.
        See :meth:`rate_worker` — the server builds the ProofOfPayment from it.
        """
        body: dict[str, Any] = {"task_id": task_id, "score": score}
        if comment:
            body["comment"] = comment
        if agent_id is not None:
            body["agent_id"] = agent_id
        if proof_tx is not None:
            body["proof_tx"] = proof_tx
        return await self._client._request("POST", "/reputation/agents/rate", json=body)

    async def register(
        self, wallet_address: str, *, network: str = "base"
    ) -> dict[str, Any]:
        """Register an agent on the ERC-8004 identity registry (gasless)."""
        return await self._client._request(
            "POST",
            "/reputation/register",
            json={"wallet_address": wallet_address, "network": network},
        )

    async def prepare_feedback(
        self,
        task_id: str,
        score: int,
        *,
        comment: str | None = None,
        worker_address: str | None = None,
    ) -> dict[str, Any]:
        """Prepare on-chain feedback params for worker signing.

        ``worker_address`` is optional: when omitted, the server resolves it
        from the task's assigned executor (else the authenticated caller's
        wallet). Pass it explicitly only to pin a specific wallet (it is
        still validated against the assignment).

        The response includes ``prepare_id`` — keep it for
        :meth:`confirm_feedback`.
        """
        body: dict[str, Any] = {"task_id": task_id, "score": score}
        if comment:
            body["comment"] = comment
        if worker_address is not None:
            body["worker_address"] = worker_address
        return await self._client._request(
            "POST", "/reputation/prepare-feedback", json=body
        )

    async def confirm_feedback(
        self,
        task_id: str,
        tx_hash: str,
        *,
        prepare_id: str,
    ) -> dict[str, Any]:
        """Confirm a worker-signed feedback transaction.

        ``prepare_id`` comes from the :meth:`prepare_feedback` response;
        ``tx_hash`` is the hash of the worker-signed ``giveFeedback()``
        transaction. All three fields are required by the server.
        """
        return await self._client._request(
            "POST",
            "/reputation/confirm-feedback",
            json={
                "prepare_id": prepare_id,
                "tx_hash": tx_hash,
                "task_id": task_id,
            },
        )

    # ------------------------------------------------------------------
    # Rater-authored ratings (EIP-7702 relay): prepare -> sign -> submit
    # ------------------------------------------------------------------
    #
    # The ERC-8004 Reputation Registry records msg.sender as the author of a
    # rating. When the Facilitator relays your feedback to sponsor the gas, ITS
    # wallet is the one on record — measured on Base, 91,3% of the network's
    # feedback sat under a single address that, since revoking is authorised the
    # same way, could also have erased all of it.
    #
    # This rail fixes it without changing the registry: you delegate your own
    # EOA to the FeedbackDelegate contract, the transaction is sent TO YOUR
    # ADDRESS, and the registry sees you. Gas stays sponsored.
    #
    # The SDK deliberately does NOT accept a private key for any of this. Your
    # signature is yours to produce; if a `private_key` parameter ever appears
    # in this section, something was misunderstood at a fundamental level.

    async def prepare_relayed_rating(
        self,
        task_id: str,
        direction: str,
        score: int,
        *,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Ask what you must sign to author a rating. Writes nothing, costs nothing.

        ``direction`` is ``"publisher_rates_executor"`` or
        ``"executor_rates_publisher"``. The ROLE NAMES come back resolved from
        the task — an order against a service listing is rated buyer/seller, any
        other task requester/executor — in ``rater_role`` and ``ratee_role``.

        **How to sign, and why it is not obvious.** ``digest`` ALREADY carries
        the EIP-191 envelope and the Facilitator verifies it with
        ``recover_address_from_prehash`` — nothing added. So there are exactly
        two correct ways, and one very tempting wrong one:

        * holding your own key: sign ``digest`` as a RAW prehash
          (``Account.unsafe_sign_hash``);
        * from a wallet: ``personal_sign`` over ``signing_payload``, which is
          that same hash BEFORE the envelope — the wallet applies the envelope
          and lands on ``digest``;
        * ✗ ``personal_sign`` over ``digest`` wraps an already-wrapped hash,
          recovers a stranger, and fails as ``relay_bad_signature`` with no
          hint. Every one of our own clients did this until 2026-08-25.

        Then hand
        everything to :meth:`submit_relayed_rating`.

        When ``delegated`` is ``False`` this is the first rating this wallet has
        ever made on this chain: you must ALSO sign an EIP-7702 authorization
        over ``{chainId, address: delegate, nonce: account_nonce}`` — all three
        are in the response. From the second rating onward it is a single
        signature.

        **Verify ``delegate`` against a list you control before signing that
        authorization.** A 7702 authorization gives the named contract the power
        to act as your account; taking the address on trust from any server
        response means a compromised one could drain you, with a signature that
        is genuinely yours. Canonical addresses ship in the repo at
        ``contracts/deployments/feedback-delegate.json``.
        """
        body: dict[str, Any] = {
            "task_id": task_id,
            "direction": direction,
            "score": score,
        }
        if comment:
            body["comment"] = comment
        return await self._client._request(
            "POST", "/reputation/relay/prepare", json=body
        )

    async def submit_relayed_rating(
        self,
        prepared: dict[str, Any],
        signature: str,
        *,
        task_id: str,
        direction: str,
        score: int,
        authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Relay your signed rating. The Facilitator pays; you are the author.

        ``prepared`` is the response of :meth:`prepare_relayed_rating`, passed
        back whole. Its fields are echoed VERBATIM and that is not redundancy:
        the Facilitator rebuilds the registry calldata from them and requires
        your signature to cover exactly that — it never relays calldata it was
        handed. Recompute any of them (the ``feedback_uri``, say) and the
        signature stops verifying, with no error naming the cause.

        ``authorization`` is required only when ``prepared["delegated"]`` was
        ``False``.

        Returns the on-chain result, including ``authored_by`` — which should be
        your own wallet. That is the whole point, and it is checkable:
        ``getClients(agentId)`` on the destination chain will show you.
        """
        body: dict[str, Any] = {
            "task_id": task_id,
            "direction": direction,
            "score": score,
            "signature": signature,
            **{
                k: prepared[k]
                for k in (
                    "network",
                    "ratee_agent_id",
                    "rater_wallet",
                    "deadline",
                    "nonce",
                    "tag1",
                    "tag2",
                    "endpoint",
                    "feedback_uri",
                    "feedback_hash",
                )
                if k in prepared
            },
        }
        if authorization is not None:
            body["authorization"] = authorization
        return await self._client._request(
            "POST", "/reputation/relay/submit", json=body
        )
