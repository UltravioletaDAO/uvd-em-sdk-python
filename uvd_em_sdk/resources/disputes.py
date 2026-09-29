"""Disputes resource — client.disputes.create(), .resolve(), etc."""

from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING

from ..exceptions import EMValidationError
from ..models import DisputeReason

if TYPE_CHECKING:
    from ..client import EMClient


class DisputesResource:
    """Publisher-initiated disputes and human-arbiter resolution.

    Mirrors ``mcp_server/api/routers/disputes.py``. Reads use
    ``verify_agent_auth_read`` and writes ``verify_agent_auth_write`` — in
    production that means ERC-8128 wallet signing (attach a
    ``WalletAdapter``). Agents only see disputes on their own tasks;
    ``/available`` lists open disputes any eligible human arbiter can pick
    up (eligibility is enforced at resolve time).

    Usage::

        dispute = await client.disputes.create(
            submission_id="sub-uuid",
            reason="fake_evidence",
            description="GPS metadata does not match the storefront",
        )
        await client.disputes.resolve(dispute["id"], verdict="refund",
                                      reason="Evidence clearly fabricated")
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    async def list(
        self,
        *,
        status: str | None = None,
        task_id: str | None = None,
        submission_id: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List disputes visible to the authenticated agent.

        ``GET /disputes`` — scoped to the caller's own tasks
        (agent_id / wallet). Returns ``{items: [...], total}``.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if task_id:
            params["task_id"] = task_id
        if submission_id:
            params["submission_id"] = submission_id
        if category:
            params["category"] = category
        return await self._client._request("GET", "/disputes", params=params)

    async def available(
        self,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List open disputes available for human arbiters to pick up.

        ``GET /disputes/available`` — only ``status='open'`` disputes that
        carry a Ring 2 arbiter verdict (escalated, not manually opened).
        """
        params: dict[str, Any] = {"limit": limit}
        if category:
            params["category"] = category
        return await self._client._request("GET", "/disputes/available", params=params)

    async def get(self, dispute_id: str) -> dict[str, Any]:
        """Get full details of a single dispute.

        ``GET /disputes/{id}`` — includes the arbiter verdict snapshot,
        both parties' evidence and the resolution amounts. Agents can only
        view disputes on their own tasks (403 otherwise).
        """
        return await self._client._request("GET", f"/disputes/{dispute_id}")

    async def create(
        self,
        *,
        submission_id: str,
        reason: DisputeReason | str,
        description: str,
    ) -> dict[str, Any]:
        """Open a publisher-initiated dispute against a submission.

        ``POST /disputes`` (201) — the publisher explicitly decides a
        submission is fraudulent/non-compliant; the linked submission's
        verdict becomes ``disputed``. Requires ERC-8128 wallet signing.

        Args:
            submission_id: UUID of the submission being disputed.
            reason: One of :class:`~uvd_em_sdk.DisputeReason` (typed
                ``dispute_reason`` enum — anything else is a 422
                server-side, so it is validated locally first).
            description: 5-2000 chars explaining the dispute.

        Raises:
            EMValidationError: Locally, if *reason* is not a valid
                ``dispute_reason`` value or *description* is out of bounds.
        """
        try:
            reason = DisputeReason(reason)
        except ValueError:
            raise EMValidationError(
                message=(
                    f"Invalid dispute reason {reason!r} — valid reasons: "
                    f"{[r.value for r in DisputeReason]}"
                ),
                details={"reason": str(reason)},
            ) from None
        if not 5 <= len(description) <= 2000:
            raise EMValidationError(
                message=(f"description must be 5-2000 chars (got {len(description)})"),
                details={"description_length": len(description)},
            )
        return await self._client._request(
            "POST",
            "/disputes",
            json={
                "submission_id": submission_id,
                "reason": reason.value,
                "description": description,
            },
        )

    async def resolve(
        self,
        dispute_id: str,
        *,
        verdict: Literal["release", "refund", "split"],
        reason: str,
        split_pct: float | None = None,
    ) -> dict[str, Any]:
        """Deliver a human arbiter's verdict on an INCONCLUSIVE dispute.

        ``POST /disputes/{id}/resolve``:

        * ``release`` — worker wins, full bounty released.
        * ``refund``  — agent wins, full bounty refunded.
        * ``split``   — partial both ways; *split_pct* = the AGENT's refund
          percent (0-100), required for this verdict.

        Args:
            dispute_id: The dispute to resolve.
            verdict: ``release | refund | split``.
            reason: 5-2000 chars justifying the verdict.
            split_pct: Agent refund % — required when ``verdict='split'``.

        Raises:
            EMValidationError: Locally, on an unknown verdict or a split
                without *split_pct*.
        """
        if verdict not in ("release", "refund", "split"):
            raise EMValidationError(
                message=f"verdict must be release|refund|split (got {verdict!r})",
                details={"verdict": verdict},
            )
        if verdict == "split" and split_pct is None:
            raise EMValidationError(
                message="split_pct (agent refund %, 0-100) is required for "
                "verdict='split'",
                details={"verdict": verdict},
            )
        body: dict[str, Any] = {"verdict": verdict, "reason": reason}
        if split_pct is not None:
            body["split_pct"] = split_pct
        return await self._client._request(
            "POST",
            f"/disputes/{dispute_id}/resolve",
            json=body,
        )
