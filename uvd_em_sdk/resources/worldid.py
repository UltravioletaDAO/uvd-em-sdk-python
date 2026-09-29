"""World ID resource — client.worldid.rp_signature(), .verify(), etc."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import EMClient


class WorldIdResource:
    """World ID 4.0 proof-of-humanity operations.

    **Why it matters**: with ``EM_WORLD_ID_ENABLED`` (production default),
    applying to a task with **bounty >= $500 requires Orb-level World ID
    verification** (threshold served as
    ``worldid_min_bounty_for_orb_usd`` in ``GET /config``). Unverified
    workers are rejected at apply time.

    Mirrors ``mcp_server/api/routers/worldid.py`` (RP signing + Cloud API
    v4 verify, nullifier uniqueness = anti-sybil) and the
    ``GET /workers/world-status`` lookup.

    Usage::

        rp = await client.worldid.rp_signature()
        # ...run IDKit with rp, then forward its result:
        await client.worldid.verify(
            nullifier_hash="0x...",
            verification_level="orb",
            executor_id="executor-uuid",
        )
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    async def rp_signature(self, action: str = "verify-worker") -> dict[str, Any]:
        """Get a signed RP request for World ID IDKit initialization.

        ``GET /world-id/rp-signature`` — returns nonce, timestamps, action,
        secp256k1 signature, rp_id and app_id. The frontend/agent uses this
        to configure IDKit before prompting the human.
        """
        return await self._client._request(
            "GET", "/world-id/rp-signature", params={"action": action}
        )

    async def verify(
        self,
        *,
        nullifier_hash: str,
        verification_level: str,
        executor_id: str,
        protocol_version: str = "3.0",
        nonce: str = "",
        action: str = "verify-worker",
        responses: list[Any] | None = None,
        proof: str = "",
        merkle_root: str = "",
        signal: str = "",
    ) -> dict[str, Any]:
        """Verify a World ID ZK proof via the Cloud API.

        ``POST /world-id/verify`` — checks nullifier uniqueness
        (**409 = sybil attempt**), stores the verification and updates the
        executor profile. With ``EM_WORLDID_REQUIRE_AUTH`` on, the backend
        requires a worker Supabase JWT and derives the executor from it
        (the body ``executor_id`` is then advisory only).

        Args:
            nullifier_hash: Unique nullifier for this person+app.
            verification_level: ``'orb'`` or ``'device'`` — only Orb
                satisfies the >= $500 bounty gate.
            executor_id: UUID of the executor being verified.
            protocol_version: IDKit protocol version (``'3.0'``/``'4.0'``).
            nonce: Nonce from the IDKit result.
            action: Action string used during proof generation.
            responses: Raw IDKit responses array (v4 Cloud API).
            proof: ZK proof (for DB storage).
            merkle_root: Merkle root (for DB storage).
            signal: Signal used during proof generation.
        """
        body: dict[str, Any] = {
            "nullifier_hash": nullifier_hash,
            "verification_level": verification_level,
            "executor_id": executor_id,
            "protocol_version": protocol_version,
            "nonce": nonce,
            "action": action,
            "proof": proof,
            "merkle_root": merkle_root,
            "signal": signal,
        }
        if responses is not None:
            body["responses"] = responses
        return await self._client._request("POST", "/world-id/verify", json=body)

    async def worker_status(self, wallet: str) -> dict[str, Any]:
        """Check World human-verification status for a wallet.

        ``GET /workers/world-status?wallet=0x...`` — looks up the AgentBook
        contract on Base. Use it before applying to Orb-gated tasks
        (bounty >= $500) to know whether the human needs to verify first.
        """
        return await self._client._request(
            "GET", "/workers/world-status", params={"wallet": wallet}
        )
