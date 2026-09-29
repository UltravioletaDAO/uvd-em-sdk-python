"""Workers resource — client.workers.register(), .earnings(), etc."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .._envelope import unwrap_envelope
from ..models import Executor

if TYPE_CHECKING:
    from ..client import EMClient


class WorkersResource:
    """Operations on workers / executors.

    **Auth model**: the worker-scoped methods are verified server-side
    against the *worker's* identity. Humans authenticate with the Supabase
    JWT (``verify_worker_auth``) — pass it via ``EMClient(supabase_jwt=...)``.
    Agent/robot executors authenticate the reads (:meth:`my_submission`,
    :meth:`geo_reference`) with ERC-8128 request signing — attach a wallet
    via ``EMClient(wallet=...)`` and they sign automatically. With
    ``EM_REQUIRE_WORKER_AUTH=true`` the backend rejects them without either.

    Usage::

        worker = await client.workers.register(wallet_address="0x...")
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    def _auth_headers(self) -> dict[str, str] | None:
        """``Authorization: Bearer <supabase_jwt>`` for worker-scoped calls.

        Overrides the client-default header (API-key Bearer) for these
        requests only — the backend's worker routes verify Supabase JWTs.
        """
        jwt = self._client._supabase_jwt
        return {"Authorization": f"Bearer {jwt}"} if jwt else None

    async def register(
        self,
        wallet_address: str,
        name: str | None = None,
        email: str | None = None,
    ) -> Executor:
        """Register a new worker by wallet address."""
        body: dict[str, Any] = {"wallet_address": wallet_address}
        if name:
            body["name"] = name
        if email:
            body["email"] = email
        data = await self._client._request("POST", "/workers/register", json=body)
        # The endpoint returns the generic SuccessResponse envelope whose
        # payload names the id ``executor_id`` — map it to ``Executor.id``
        # locally (the model is reused by direct-shape endpoints elsewhere).
        inner = unwrap_envelope(data)
        return Executor.model_validate(
            {**inner, "id": inner.get("executor_id") or inner.get("id")}
        )

    async def balance(self, wallet_address: str) -> dict[str, Any]:
        """Get USDC balances across all chains for a wallet."""
        return await self._client._request("GET", f"/payments/balance/{wallet_address}")

    async def payment_events(
        self,
        wallet_address: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get payment events (earnings history) for a wallet."""
        return await self._client._request(
            "GET",
            "/payments/events",
            params={"wallet_address": wallet_address, "limit": limit},
        )

    async def my_submission(self, task_id: str, executor_id: str) -> dict[str, Any]:
        """Get the executor's own submission for a task.

        ``GET /workers/tasks/{task_id}/my-submission`` — evidence, verdict
        and verification state. The backend enforces that the caller IS the
        claimed executor: humans via the worker Supabase JWT, agent/robot
        executors via ERC-8128. ``sign=True`` forces the ERC-8128 signature
        that GETs skip by default (a no-op without a signing wallet, so the
        JWT path is unaffected); without it an agent-as-worker gets 401.
        404 if the worker has not submitted.
        """
        return await self._client._request(
            "GET",
            f"/workers/tasks/{task_id}/my-submission",
            params={"executor_id": executor_id},
            headers=self._auth_headers(),
            sign=True,
        )

    async def geo_reference(self, task_id: str, executor_id: str) -> dict[str, Any]:
        """Get the task's reference location + effective GPS radius.

        ``GET /workers/tasks/{task_id}/geo-reference`` — ASSIGNED worker
        only (403 otherwise). Pre-submit location warnings must use exactly
        these values: they are the same point/radius the verification
        pipeline compares against (C-24/C-25). ``sign=True`` forces the
        ERC-8128 signature that GETs skip by default (a no-op without a
        signing wallet, so the JWT path is unaffected); without it an
        agent-as-worker gets 401.
        """
        return await self._client._request(
            "GET",
            f"/workers/tasks/{task_id}/geo-reference",
            params={"executor_id": executor_id},
            headers=self._auth_headers(),
            sign=True,
        )

    async def update_social_links(self, platform: str, handle: str) -> dict[str, Any]:
        """Add or update a social link on the worker profile.

        ``PUT /workers/social-links`` — worker Supabase JWT required (401
        without). ``platform`` is lowercase (e.g. ``'x'``, ``'github'``);
        for X the handle must be ``@username`` (1-15 alphanumeric or
        underscore chars).
        """
        return await self._client._request(
            "PUT",
            "/workers/social-links",
            json={"platform": platform, "handle": handle},
            headers=self._auth_headers(),
        )
