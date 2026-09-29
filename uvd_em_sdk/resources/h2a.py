"""H2A (Human-to-Agent) resource — client.h2a.publish(), .approve(), etc."""

from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING

from ..exceptions import EMValidationError
from ..models import H2AApplicationList, PaymentConfig

if TYPE_CHECKING:
    from ..client import EMClient


class H2AResource:
    """Human-to-Agent marketplace operations.

    **Auth model (per namespace)**: every H2A write is verified server-side
    with the *human publisher's* Supabase JWT (``verify_jwt_auth``) — API
    keys and ERC-8128 wallet signatures are NOT accepted here (they get
    401). Pass the JWT via ``EMClient(supabase_jwt=...)``; this namespace
    then sends ``Authorization: Bearer <jwt>`` on its requests (other
    namespaces keep API-key / ERC-8128 auth). ``GET /h2a/tasks`` is public
    except with ``my_tasks=true``.

    Usage::

        client = EMClient(supabase_jwt="eyJ...")
        task = await client.h2a.publish(
            title="Research competitor pricing",
            instructions="...",
            category="research",
            bounty_usd=5.00,
        )
        submissions = await client.h2a.submissions("task-uuid")
        await client.h2a.approve(
            "task-uuid",
            submission_id="sub-uuid",
            verdict="accepted",
            worker_score=5,
        )
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    def _auth_headers(self) -> dict[str, str] | None:
        """``Authorization: Bearer <supabase_jwt>`` for the h2a namespace.

        Overrides the client-default header (API-key Bearer) for these
        requests only — the backend's H2A routes verify Supabase JWTs.
        """
        jwt = self._client._supabase_jwt
        return {"Authorization": f"Bearer {jwt}"} if jwt else None

    async def publish(
        self,
        title: str,
        instructions: str,
        category: str,
        bounty_usd: float,
        *,
        deadline_hours: int = 24,
        required_capabilities: list[str] | None = None,
        evidence_required: list[str] | None = None,
        payment_network: str = "base",
        payment_token: str = "USDC",
        target_agent_id: str | None = None,
        target_executor_type: Literal["any", "human", "agent", "robot"] = "agent",
        publisher_wallet: str | None = None,
        verification_mode: str | None = None,
    ) -> dict[str, Any]:
        """Publish a task for another party (human/agent/robot) to execute.

        Targets the canonical universal route ``POST /api/v1/publish``
        (``/h2a/tasks`` survives only as a deprecated alias). Mirrors the
        backend ``PublishH2ATaskRequest`` (``mcp_server/models.py``,
        ``extra="forbid"``).

        Args:
            title: 5-255 chars.
            instructions: 10-10000 chars.
            category: Task category.
            bounty_usd: Bounty in USD — **max $500** (validated locally;
                the backend enforces the same ``le=500``). In escrow-mode
                (``EM_H2A_ESCROW_ENABLED``) the on-chain deposit limit is
                **$100** — publishes above that cannot lock escrow.
            deadline_hours: 1-720 (default 24).
            required_capabilities: Up to 10 capability tags.
            evidence_required: 1-5 evidence types (server default
                ``["json_response"]``).
            payment_network: Payment network (default ``base``).
            payment_token: Stablecoin symbol to escrow
                (``USDC | USDT | EURC | AUSD | PYUSD``) — must exist on
                ``payment_network``.
            target_agent_id: Pin the task to a specific agent.
            target_executor_type: Who may execute — ``any | human | agent
                | robot``. Only ``human`` enables H2H; the backend maps
                everything else to ``agent``.
            publisher_wallet: The publisher's funding ``0x`` wallet. The
                Supabase JWT is anonymous (no wallet claim), so the client
                asserts the ACTIVE wallet here — it is the one that signs
                the EIP-3009 escrow lock at assignment (the nonce commits
                to the receiver, so signing only happens when the worker
                is known).
            verification_mode: ``manual`` (server default) or ``auto``.

        Raises:
            EMValidationError: If ``bounty_usd`` exceeds $500 (raised
                locally, before any request).
        """
        if bounty_usd > 500:
            raise EMValidationError(
                message=(
                    f"bounty_usd must be <= 500 (got {bounty_usd}); the "
                    "backend rejects higher bounties with 422. Note: "
                    "escrow-mode caps at $100 (on-chain deposit limit)."
                ),
                details={"bounty_usd": bounty_usd, "max_bounty_usd": 500},
            )
        body: dict[str, Any] = {
            "title": title,
            "instructions": instructions,
            "category": category,
            "bounty_usd": bounty_usd,
            "deadline_hours": deadline_hours,
            "payment_network": payment_network,
            "payment_token": payment_token,
            "target_executor_type": target_executor_type,
        }
        if required_capabilities:
            body["required_capabilities"] = required_capabilities
        if evidence_required:
            body["evidence_required"] = evidence_required
        if target_agent_id:
            body["target_agent_id"] = target_agent_id
        if publisher_wallet:
            body["publisher_wallet"] = publisher_wallet
        if verification_mode:
            body["verification_mode"] = verification_mode
        return await self._client._request(
            "POST", "/publish", json=body, headers=self._auth_headers()
        )

    async def list(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List H2A tasks."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return await self._client._request(
            "GET", "/h2a/tasks", params=params, headers=self._auth_headers()
        )

    async def get(self, task_id: str) -> dict[str, Any]:
        """Get H2A task details."""
        return await self._client._request(
            "GET", f"/h2a/tasks/{task_id}", headers=self._auth_headers()
        )

    async def submissions(self, task_id: str) -> dict[str, Any]:
        """View agent submissions for an H2A task."""
        return await self._client._request(
            "GET",
            f"/h2a/tasks/{task_id}/submissions",
            headers=self._auth_headers(),
        )

    async def applications(self, task_id: str) -> H2AApplicationList:
        """List the workers who applied to an H2A task.

        ``GET /h2a/tasks/{id}/applications`` — publisher-only (the backend
        verifies ownership against the Supabase JWT). Each application is
        enriched with the executor profile (display_name, wallet,
        reputation_score, tasks_completed, avg_rating).
        """
        data = await self._client._request(
            "GET",
            f"/h2a/tasks/{task_id}/applications",
            headers=self._auth_headers(),
        )
        return H2AApplicationList.model_validate(data)

    async def assign(
        self,
        task_id: str,
        executor_id: str,
        *,
        payment_auth: str | None = None,
    ) -> dict[str, Any]:
        """Assign an applied worker to an H2A task.

        ``POST /h2a/tasks/{id}/assign`` — publisher-only.

        **Escrow-mode tasks** (published with ``EM_H2A_ESCROW_ENABLED``)
        REQUIRE *payment_auth*: the publisher's signed EIP-3009 escrow
        authorization for THIS worker, sent as the ``X-Payment-Auth``
        header. Without it the backend answers **402** and nothing is
        assigned. Build it with
        :func:`~uvd_em_sdk.build_escrow_pre_auth` from the config
        returned by :meth:`payment_config`.

        >>> PROTOCOL CONSTRAINT (ADR-002, verbatim) <<<
        The EIP-3009 nonce is ``AuthCaptureEscrow.getHash(paymentInfo)``
        which **includes the receiver** — the escrow signature can only be
        created AT ASSIGNMENT, when the worker is known. Never design flows
        that sign an escrow auth before the worker is chosen ("stored
        pre-auth with late receiver fill" is on-chain unsound).

        Legacy tasks (no escrow marker) are assigned status-only — funds
        move at approval (sign-on-approval drain path).

        Args:
            task_id: The H2A task (must be ``published``).
            executor_id: A worker who applied to the task.
            payment_auth: Raw JSON ``X-Payment-Auth`` value (output of
                :func:`~uvd_em_sdk.build_escrow_pre_auth`). Required for
                escrow-mode tasks; ignored for legacy tasks.
        """
        headers = self._auth_headers() or {}
        if payment_auth:
            headers["X-Payment-Auth"] = payment_auth
        return await self._client._request(
            "POST",
            f"/h2a/tasks/{task_id}/assign",
            json={"executor_id": executor_id},
            headers=headers or None,
        )

    async def approve(
        self,
        task_id: str,
        *,
        submission_id: str,
        verdict: Literal["accepted", "rejected", "needs_revision"] = "accepted",
        notes: str | None = None,
        worker_score: int | None = None,
    ) -> dict[str, Any]:
        """Deliver the publisher's verdict on an agent's submission.

        Mirrors the backend ``ApproveH2ASubmissionRequest``:

        * ``verdict='accepted'`` — releases the escrow to the worker.
          **Requires** ``worker_score`` (1-5 stars): with
          ``EM_H2A_REQUIRE_RATING`` active (production default) the
          backend 422s without it — bidirectional ERC-8004 reputation is
          what makes the market trustless. Validated locally, fail fast.
        * ``verdict='rejected'`` — refunds the escrow to the publisher
          and closes the task.
        * ``verdict='needs_revision'`` — no escrow operation; the worker
          may resubmit.

        Args:
            task_id: The H2A task.
            submission_id: The submission being judged (UUID).
            verdict: ``accepted | rejected | needs_revision``.
            notes: Optional feedback (max 2000 chars).
            worker_score: Publisher's star rating of the worker (1-5).
                Required when ``verdict='accepted'``.

        Raises:
            EMValidationError: Locally, if ``verdict`` is unknown, if
                ``worker_score`` is missing for an accepted verdict, or if
                it is outside 1-5.
        """
        if verdict not in ("accepted", "rejected", "needs_revision"):
            raise EMValidationError(
                message=(
                    f"verdict must be accepted|rejected|needs_revision "
                    f"(got {verdict!r})"
                ),
                details={"verdict": verdict},
            )
        if verdict == "accepted" and worker_score is None:
            raise EMValidationError(
                message=(
                    "worker_score (1-5) is required for verdict='accepted' — "
                    "the backend rejects unrated approvals with 422 "
                    "(EM_H2A_REQUIRE_RATING)."
                ),
                details={"verdict": verdict},
            )
        if worker_score is not None and not 1 <= worker_score <= 5:
            raise EMValidationError(
                message=f"worker_score must be between 1 and 5 (got {worker_score})",
                details={"worker_score": worker_score},
            )
        body: dict[str, Any] = {"submission_id": submission_id, "verdict": verdict}
        if notes:
            body["notes"] = notes
        if worker_score is not None:
            body["worker_score"] = worker_score
        return await self._client._request(
            "POST",
            f"/h2a/tasks/{task_id}/approve",
            json=body,
            headers=self._auth_headers(),
        )

    async def reject(
        self,
        task_id: str,
        *,
        submission_id: str,
        notes: str,
    ) -> dict[str, Any]:
        """Reject an agent's submission.

        ``POST /h2a/tasks/{id}/reject`` was removed server-side (404) —
        rejection is now ``POST .../approve`` with ``verdict='rejected'``,
        which also refunds the escrow to the publisher (fund-loss fix
        2026-06-12). This method delegates to :meth:`approve` accordingly.
        """
        return await self.approve(
            task_id,
            submission_id=submission_id,
            verdict="rejected",
            notes=notes,
        )

    async def cancel(self, task_id: str) -> dict[str, Any]:
        """Cancel an H2A task."""
        return await self._client._request(
            "POST", f"/h2a/tasks/{task_id}/cancel", headers=self._auth_headers()
        )

    async def rate_publisher(
        self,
        task_id: str,
        *,
        score: int,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Rate the human publisher after being paid (worker -> publisher).

        ``POST /h2a/tasks/{id}/rate-publisher`` — the WORKER side of the
        bidirectional ERC-8004 reputation loop, gasless via the Facilitator
        and bound to the release TX (counterparty proof). Authenticated with
        the *worker's* Supabase JWT (``EMClient(supabase_jwt=...)``), and
        only after the task is ``completed``.

        Args:
            task_id: The completed task.
            score: Star rating of the publisher, 1-5 (the backend scales it
                to the 0-100 ERC-8004 range).
            comment: Optional feedback (max 1000 chars).

        Raises:
            EMValidationError: Locally, if *score* is outside 1-5.
        """
        if not 1 <= score <= 5:
            raise EMValidationError(
                message=f"score must be between 1 and 5 (got {score})",
                details={"score": score},
            )
        body: dict[str, Any] = {"score": score}
        if comment:
            body["comment"] = comment
        return await self._client._request(
            "POST",
            f"/h2a/tasks/{task_id}/rate-publisher",
            json=body,
            headers=self._auth_headers(),
        )

    async def payment_config(self) -> PaymentConfig:
        """Fetch the public H2A payment/escrow config.

        ``GET /h2a/payment-config`` — treasury address, fee percent and the
        per-network escrow signing parameters (chain_id, operator, escrow,
        token_collector, USDC address + EIP-712 domain) needed to build the
        sign-on-assignment EIP-3009 authorization. Public endpoint.

        Returns:
            :class:`~uvd_em_sdk.PaymentConfig` — pass it (or its
            ``model_dump()``) to :func:`~uvd_em_sdk.build_escrow_pre_auth`.
        """
        data = await self._client._request("GET", "/h2a/payment-config")
        return PaymentConfig.model_validate(data)


class AgentsResource:
    """Agent directory and registration.

    Usage::

        agents = await client.agents.directory(limit=20)
        await client.agents.register_executor(wallet_address="0x...", capabilities=["research"])
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    async def directory(
        self,
        *,
        capabilities: list[str] | None = None,
        limit: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Browse the public AI agent directory."""
        params: dict[str, Any] = {"limit": limit, "page": page}
        if capabilities:
            params["capabilities"] = ",".join(capabilities)
        return await self._client._request("GET", "/agents/directory", params=params)

    async def register_executor(
        self,
        wallet_address: str,
        capabilities: list[str],
        display_name: str,
        *,
        agent_card_url: str | None = None,
        mcp_endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        """Register an AI agent as an executor in the marketplace."""
        body: dict[str, Any] = {
            "wallet_address": wallet_address,
            "capabilities": capabilities,
            "display_name": display_name,
        }
        if agent_card_url:
            body["agent_card_url"] = agent_card_url
        if mcp_endpoint_url:
            body["mcp_endpoint_url"] = mcp_endpoint_url
        return await self._client._request(
            "POST", "/agents/register-executor", json=body
        )
