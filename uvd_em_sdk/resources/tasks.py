"""Tasks resource — client.tasks.create(), .list(), .get(), etc."""

from __future__ import annotations

import warnings
from typing import Any, TYPE_CHECKING

from .._envelope import unwrap_envelope
from ..models import (
    CreateTaskParams,
    Task,
    TaskList,
    Application,
    ApplicationList,
    PaymentTimeline,
)
from ..pagination import PageIterator

if TYPE_CHECKING:
    from ..client import EMClient


class TasksResource:
    """Operations on tasks.

    Usage::

        task = await client.tasks.create(CreateTaskParams(...))
        task = await client.tasks.get("task-uuid")

        async for task in client.tasks.list(status="published"):
            print(task.title)
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    async def create(
        self,
        params: CreateTaskParams,
        *,
        payment_auth: str | None = None,
        escrow_timing: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        """Publish a new task.

        Args:
            params: Task parameters.
            payment_auth: Agent-signed EIP-3009 pre-authorization, sent as
                ``X-Payment-Auth`` (ADR-001 fase2 — required in production;
                without it task creation is rejected).
            escrow_timing: ``"lock_on_assignment"`` (default server-side) or
                ``"lock_on_creation"``, sent as ``X-Escrow-Timing``.
            idempotency_key: Sent as ``X-Idempotency-Key`` — the server
                replays the original response for a repeated key instead of
                creating a duplicate task.
        """
        headers: dict[str, str] = {}
        if payment_auth:
            headers["X-Payment-Auth"] = payment_auth
        if escrow_timing:
            headers["X-Escrow-Timing"] = escrow_timing
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        data = await self._client._request(
            "POST",
            "/tasks",
            json=params.model_dump(exclude_none=True),
            headers=headers or None,
        )
        return Task.model_validate(data)

    async def get(self, task_id: str) -> Task:
        """Get task details by ID."""
        data = await self._client._request("GET", f"/tasks/{task_id}")
        return Task.model_validate(data)

    def list(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PageIterator[Task]:
        """List tasks with auto-pagination.

        Returns an async iterator that fetches pages lazily::

            async for task in client.tasks.list(status="published"):
                print(task.title)

            # Or collect all at once
            all_tasks = await client.tasks.list(status="published").collect()
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if category:
            params["category"] = category
        if agent_id:
            params["agent_id"] = agent_id

        async def fetch(p: dict[str, Any]) -> dict[str, Any]:
            return await self._client._request("GET", "/tasks", params=p)

        return PageIterator(fetch, "tasks", Task, params, page_size=limit)

    async def list_page(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TaskList:
        """Get a single page of tasks (non-iterating)."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if category:
            params["category"] = category
        if agent_id:
            params["agent_id"] = agent_id
        data = await self._client._request("GET", "/tasks", params=params)
        return TaskList.model_validate(data)

    async def cancel(self, task_id: str, reason: str | None = None) -> dict[str, Any]:
        """Cancel a task."""
        body: dict[str, Any] = {}
        if reason:
            body["reason"] = reason
        return await self._client._request(
            "POST", f"/tasks/{task_id}/cancel", json=body
        )

    async def assign(
        self,
        task_id: str,
        executor_id: str,
        notes: str | None = None,
        *,
        payment_auth: str | None = None,
    ) -> dict[str, Any]:
        """Assign a worker to a task.

        Args:
            task_id: Task to assign.
            executor_id: Chosen worker.
            notes: Optional note for the worker.
            payment_auth: Fresh escrow authorization signed AT ASSIGNMENT,
                sent as ``X-Payment-Auth`` (build it with
                :func:`~uvd_em_sdk.escrow_signing.build_escrow_pre_auth`).
                ADR-002 protocol constraint: the EIP-3009 nonce is
                ``AuthCaptureEscrow.getHash(paymentInfo)`` which **includes
                the receiver** — the escrow signature can only be created AT
                ASSIGNMENT, when the worker is known. Escrow-mode tasks
                assigned without it are rejected by the server.
        """
        body: dict[str, Any] = {"executor_id": executor_id}
        if notes:
            body["notes"] = notes
        headers = {"X-Payment-Auth": payment_auth} if payment_auth else None
        return await self._client._request(
            "POST", f"/tasks/{task_id}/assign", json=body, headers=headers
        )

    async def batch_create(
        self,
        tasks: list[dict[str, Any]],
        payment_token: str = "USDC",
    ) -> dict[str, Any]:
        """Create multiple tasks in a single request (max 50).

        .. deprecated::
            ``POST /tasks/batch`` is **disabled server-side** and returns a
            fixed HTTP 503 (security hardening API-002 / GR-1.5 — the batch
            path skipped x402 payment validation, ERC-8004 identity checks
            and escrow). Use :meth:`create` per task instead. Kept for
            compatibility; calling it raises :class:`DeprecationWarning`
            and the server responds 503.
        """
        warnings.warn(
            "tasks.batch_create() is deprecated: POST /tasks/batch is "
            "disabled server-side (fixed 503, hardening API-002). "
            "Use tasks.create() per task instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self._client._request(
            "POST",
            "/tasks/batch",
            json={"tasks": tasks, "payment_token": payment_token},
        )

    # -- applications -------------------------------------------------------

    async def apply(
        self,
        task_id: str,
        executor_id: str,
        message: str | None = None,
    ) -> Application:
        """Apply to work on a task (worker operation)."""
        body: dict[str, Any] = {"executor_id": executor_id}
        if message:
            body["message"] = message
        data = await self._client._request("POST", f"/tasks/{task_id}/apply", json=body)
        # POST /tasks/{id}/apply returns the SuccessResponse envelope
        # ({success, message, data}); the Application model is the inner payload.
        return Application.model_validate(unwrap_envelope(data))

    async def list_applications(self, task_id: str) -> ApplicationList:
        """List applications for a task."""
        data = await self._client._request("GET", f"/tasks/{task_id}/applications")
        return ApplicationList.model_validate(data)

    # -- payment info -------------------------------------------------------

    async def get_payment(self, task_id: str) -> PaymentTimeline:
        """Get payment status and timeline for a task."""
        data = await self._client._request("GET", f"/tasks/{task_id}/payment")
        return PaymentTimeline.model_validate(data)

    async def get_transactions(self, task_id: str) -> dict[str, Any]:
        """Get the full transaction history for a task."""
        return await self._client._request("GET", f"/tasks/{task_id}/transactions")

    # -- discovery ----------------------------------------------------------

    async def available(
        self,
        *,
        category: str | None = None,
        min_bounty: float | None = None,
        max_bounty: float | None = None,
        location: str | None = None,
        skills: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Browse available tasks (public, no auth required)."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if category:
            params["category"] = category
        if min_bounty is not None:
            params["min_bounty"] = min_bounty
        if max_bounty is not None:
            params["max_bounty"] = max_bounty
        if location:
            params["location"] = location
        if skills:
            params["skills"] = ",".join(skills)
        return await self._client._request("GET", "/tasks/available", params=params)

    # -- payment channel (Solana MPP) ---------------------------------------

    async def declare_channel(
        self,
        task_id: str,
        *,
        channel_id: str,
        payer: str,
        cap_usdc: float | None = None,
        work_unit: str | None = None,
        price_per_unit_uusdc: int | None = None,
        payee: str | None = None,
    ) -> dict[str, Any]:
        """Declare the pay.sh MPP channel that funds this task (payer only).

        pay.sh strips payment headers before proxying, so this call is the ONLY
        way EM learns the channel id (ADR-007 D3). Until it lands there is no
        binding, and every tick is refused with ``no_channel_bound``.

        Args:
            task_id: Task the channel pays for.
            channel_id: base58 channel pubkey the payer opened.
            payer: base58 address that opened (and funded) the channel.
            cap_usdc: Deposit ceiling, in USDC.
            work_unit: Unit this channel meters (``second``, ``scan``, ...).
            price_per_unit_uusdc: Price of one unit, in micro-USDC.
            payee: Optional. CHECKED against the worker resolved server-side
                from the task; a mismatch is refused, never silently preferred.
        """
        body: dict[str, Any] = {"channel_id": channel_id, "payer": payer}
        if cap_usdc is not None:
            body["cap_usdc"] = cap_usdc
        if work_unit:
            body["work_unit"] = work_unit
        if price_per_unit_uusdc is not None:
            body["price_per_unit_uusdc"] = price_per_unit_uusdc
        if payee:
            body["payee"] = payee
        return await self._client._request(
            "POST", f"/tasks/{task_id}/channel", json=body
        )

    async def tick(
        self,
        task_id: str,
        *,
        evidence: dict[str, Any],
        unit: str | None = None,
        payee: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record ONE verified unit of work against the task's channel.

        One call = one unit. The tick must carry evidence: the meter only
        advances on an observation the server accepted, so a tick whose
        evidence is refused (422) did NOT raise the cumulative voucher and
        therefore did not pay. Callable by the task's publisher or its
        assigned worker.
        """
        body: dict[str, Any] = {"evidence": evidence}
        if unit:
            body["unit"] = unit
        if payee:
            body["payee"] = payee
        if note:
            body["note"] = note
        return await self._client._request("POST", f"/tasks/{task_id}/tick", json=body)
