"""Escrow resource — client.escrow.config(), .refund(), etc."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import EMClient


class EscrowResource:
    """x402r escrow configuration and lifecycle operations.

    **Auth model**: writes (:meth:`release`, :meth:`refund`,
    :meth:`update_task_escrow`) are verified server-side with
    ``verify_agent_auth_write`` — in production that means ERC-8128 wallet
    signing (attach a ``WalletAdapter`` to :class:`~em_plugin_sdk.EMClient`;
    API keys are rejected with ``EM_API_KEYS_ENABLED=false``). Reads are
    public.

    Several legacy direct-contract endpoints answer **410 Gone** since all
    payments moved to the x402 SDK + Facilitator (gasless) — they are kept
    here because the routes still exist, with their live status documented
    per method.

    Usage::

        config = await client.escrow.config()
        await client.escrow.refund(deposit_id="0x...")
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    async def config(self, network: str | None = None) -> dict[str, Any]:
        """Get the x402r escrow configuration for a network.

        ``GET /escrow/config`` — contract addresses (AuthCaptureEscrow,
        TokenStore factory, EM PaymentOperator, USDC) derived live from the
        server's network registry. Useful for agents to know where funds
        are held. **404** for unknown networks or networks without x402r
        escrow (e.g. Solana).

        Args:
            network: Payment network (e.g. ``"base"``, ``"polygon"``).
                Defaults to the server default (``base``).
        """
        params = {"network": network} if network else None
        return await self._client._request("GET", "/escrow/config", params=params)

    async def payment_extension(self) -> dict[str, Any]:
        """Get the x402r refund extension for payment payloads.

        ``GET /escrow/payment-extension`` — **410 Gone** on the live server:
        payment extensions were deprecated when everything moved to the
        x402 SDK + Facilitator. Raises :class:`~em_plugin_sdk.EMError`
        (status 410) against production.
        """
        return await self._client._request("GET", "/escrow/payment-extension")

    async def deposit(self, deposit_id: str) -> dict[str, Any]:
        """Get information about a deposit in escrow.

        ``GET /escrow/deposits/{deposit_id}`` — **410 Gone** on the live
        server: direct contract queries were removed; escrow state is
        tracked in the ``escrows`` table. Use ``client.tasks.get(task_id)``
        (``escrow_status`` field) instead.
        """
        return await self._client._request("GET", f"/escrow/deposits/{deposit_id}")

    async def balance(self, merchant: str | None = None) -> dict[str, Any]:
        """Get the USDC balance held in escrow for a merchant.

        ``GET /escrow/balance`` — **410 Gone** on the live server: direct
        contract balance queries were removed (check a block explorer).
        """
        params = {"merchant": merchant} if merchant else None
        return await self._client._request("GET", "/escrow/balance", params=params)

    async def release(
        self,
        deposit_id: str,
        worker_address: str,
        amount: str,
    ) -> dict[str, Any]:
        """Release escrowed funds to a worker (LEGACY — do not use).

        ``POST /escrow/release`` — marked ``deprecated`` server-side and
        answers **410 Gone**: releases happen via
        ``POST /submissions/{id}/approve`` (gasless via the Facilitator,
        atomic 87/13 fee split on-chain). Requires ERC-8128 wallet signing.
        """
        return await self._client._request(
            "POST",
            "/escrow/release",
            json={
                "deposit_id": deposit_id,
                "worker_address": worker_address,
                "amount": amount,
            },
        )

    async def refund(self, deposit_id: str) -> dict[str, Any]:
        """Refund escrowed funds to the original payer (agent).

        ``POST /escrow/refund`` — LIVE endpoint, gasless via the x402 SDK +
        Facilitator. Requires ERC-8128 wallet signing
        (``verify_agent_auth_write``) and the escrow must belong to the
        calling agent (ownership check, P0-1). Used when a task is
        cancelled, a dispute resolves in the agent's favor, or no worker
        accepted before the deadline.

        Args:
            deposit_id: Escrow deposit ID (bytes32 hex, with or without
                ``0x`` prefix).
        """
        return await self._client._request(
            "POST",
            "/escrow/refund",
            json={"deposit_id": deposit_id},
        )

    async def update_task_escrow(
        self,
        task_id: str,
        *,
        payment_info: dict[str, Any] | None = None,
        escrow_tx: str | None = None,
    ) -> dict[str, Any]:
        """Update escrow metadata (payment_info / escrow_tx) for a task.

        ``PATCH /tasks/{task_id}/escrow`` — task-owner only
        (``verify_agent_auth_write``, ERC-8128), and only while the escrow
        is ``deposited`` or ``authorized`` (409 otherwise). Used to persist
        the release-capable ``payment_info`` snapshot (operator, salt, ...)
        the Facilitator needs for release/refund.

        Args:
            task_id: The task whose escrow row is updated.
            payment_info: Payment info metadata for escrow release/refund.
            escrow_tx: Escrow transaction hash to record.
        """
        body: dict[str, Any] = {}
        if payment_info is not None:
            body["payment_info"] = payment_info
        if escrow_tx is not None:
            body["escrow_tx"] = escrow_tx
        return await self._client._request(
            "PATCH",
            f"/tasks/{task_id}/escrow",
            json=body,
        )
