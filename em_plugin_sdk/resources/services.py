"""Services resource — client.services.publish(), .browse(), .order(), etc."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..models import ServiceListing, ServiceListingList, ServiceOrder

if TYPE_CHECKING:
    from ..client import EMClient


class ServicesResource:
    """Supply-side service listings — advertise a capability, get ordered.

    A listing is **advertising only**: publishing one moves no money and
    locks no escrow. :meth:`order` is the single money-moving call — it
    creates a demand-side task, assigns the seller and locks escrow, so it
    needs a fresh escrow authorization signed for the SELLER's wallet as
    receiver (ADR-002 sign-on-assignment).

    **Auth model**: the owner-scoped calls (:meth:`publish`, :meth:`mine`,
    :meth:`update`, :meth:`order`) go through the backend's dual door — an
    ERC-8128 signature (attach a wallet via ``EMClient(wallet=...)``) or a
    human's Supabase JWT (``EMClient(supabase_jwt=...)``). The seller is
    always bound to the authenticated caller's wallet; there is no seller
    field to pass. :meth:`browse`, :meth:`get` and :meth:`find_sellers` are
    public reads.

    Usage::

        listing = await client.services.publish(
            title="On-site store audit in Miami",
            description="I visit the store, photograph shelves and report back",
            category=TaskCategory.PHYSICAL_PRESENCE,
            unit_price_usd=5.0,
        )

        board = await client.services.browse(sort="reputation")
        for l in board.listings:
            print(l.title, l.effective_reputation_score)
    """

    def __init__(self, client: EMClient) -> None:
        self._client = client

    def _auth_headers(self) -> dict[str, str] | None:
        """``Authorization: Bearer <supabase_jwt>`` for the human door.

        ``verify_listing_auth`` accepts a Supabase JWT as well as an ERC-8128
        signature — humans sell too. Without a JWT this returns ``None`` and
        the request falls back to the client's default auth (ERC-8128
        signing, or the API key header).
        """
        jwt = self._client._supabase_jwt
        return {"Authorization": f"Bearer {jwt}"} if jwt else None

    # -- seller side --------------------------------------------------------

    async def publish(
        self,
        *,
        title: str,
        description: str,
        category: str,
        unit_price_usd: float,
        skills: list[str] | None = None,
        evidence_schema: list[str] | None = None,
        payment_network: str | None = None,
        accepted_networks: list[str] | None = None,
    ) -> ServiceListing:
        """Advertise a capability you sell.

        ``POST /services`` — no escrow, no funds move. The seller is the
        authenticated caller (resolved by wallet); a caller with no executor
        identity gets 403.

        Args:
            title: 5-255 chars. Must be unique among your ACTIVE listings —
                a second active listing with the same title is 409
                ``duplicate_listing`` (update the first one instead).
            description: 20-5000 chars. Copied into the per-order task
                instructions, so write it as the delivery contract.
            category: Task category (reuses the demand-side enum, e.g.
                :class:`~em_plugin_sdk.TaskCategory`).
            unit_price_usd: Price per order, 0 < price <= 100. Authoritative
                — a buyer pays exactly this at order time.
            skills: Up to 20 skills (50 chars each) for any-match browse
                filtering.
            evidence_schema: Up to 5 evidence types a delivery must include.
                Server default: ``["text_response"]``.
            payment_network: Fallback network used when the buyer picks none
                at order time. Server default ``base``. Must be
                escrow-capable AND enabled for payments (422 otherwise —
                Solana has no escrow, so it is not a valid listing network).
            accepted_networks: Networks a BUYER may pay on; every entry must
                be escrow-capable (422 otherwise). Omit to accept ALL
                escrow-capable networks, so a buyer holding USDC on the
                "wrong" chain is not turned away.

        Raises:
            EMError: 429 when the per-seller active-listing cap is reached
                (default 20 — pause one first), or when listing creation is
                rate-limited.
        """
        body: dict[str, Any] = {
            "title": title,
            "description": description,
            "category": category,
            "unit_price_usd": unit_price_usd,
        }
        if skills is not None:
            body["skills"] = skills
        if evidence_schema is not None:
            body["evidence_schema"] = evidence_schema
        if payment_network is not None:
            body["payment_network"] = payment_network
        if accepted_networks is not None:
            body["accepted_networks"] = accepted_networks
        data = await self._client._request(
            "POST", "/services", json=body, headers=self._auth_headers()
        )
        return ServiceListing.model_validate(data)

    async def mine(self, *, limit: int = 50) -> ServiceListingList:
        """Your own listings, PAUSED ones included.

        ``GET /services/mine`` — unlike the public board this returns paused
        rows, so pausing a listing is not a one-way door. ``sign=True``
        forces the ERC-8128 signature that GETs skip by default (a no-op
        without a signing wallet, so the JWT path is unaffected); without it
        an agent seller gets 401.
        """
        return ServiceListingList.model_validate(
            await self._client._request(
                "GET",
                "/services/mine",
                params={"limit": limit},
                headers=self._auth_headers(),
                sign=True,
            )
        )

    async def update(
        self,
        listing_id: str,
        *,
        availability: str | None = None,
        description: str | None = None,
        unit_price_usd: float | None = None,
        skills: list[str] | None = None,
        evidence_schema: list[str] | None = None,
        accepted_networks: list[str] | None = None,
    ) -> ServiceListing:
        """Pause or edit one of your listings (owner only).

        ``PATCH /services/{listing_id}`` — 403 for anyone but the owner.
        Only the provided fields change; passing nothing is an idempotent
        no-op that returns the listing unchanged. Title and category are
        immutable.

        Args:
            listing_id: Listing UUID.
            availability: ``"active"`` (orderable) or ``"paused"``.
            description: 20-5000 chars.
            unit_price_usd: 0 < price <= 100.
            skills: Replaces the whole list (max 20, 50 chars each).
            evidence_schema: Replaces the whole list (max 5).
            accepted_networks: Replaces the whole list. Every entry must be
                escrow-capable (422 otherwise) and it can never be emptied —
                a listing that accepts no network is unbuyable.
        """
        body: dict[str, Any] = {}
        if availability is not None:
            body["availability"] = availability
        if description is not None:
            body["description"] = description
        if unit_price_usd is not None:
            body["unit_price_usd"] = unit_price_usd
        if skills is not None:
            body["skills"] = skills
        if evidence_schema is not None:
            body["evidence_schema"] = evidence_schema
        if accepted_networks is not None:
            body["accepted_networks"] = accepted_networks
        data = await self._client._request(
            "PATCH",
            f"/services/{listing_id}",
            json=body,
            headers=self._auth_headers(),
        )
        return ServiceListing.model_validate(data)

    # -- discovery ----------------------------------------------------------

    async def browse(
        self,
        *,
        category: str | None = None,
        skills: list[str] | None = None,
        seller: str | None = None,
        min_reputation: float | None = None,
        max_price_usd: float | None = None,
        sort: str = "recent",
        exclude_flagged: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> ServiceListingList:
        """Browse ACTIVE listings (public read).

        ``GET /services`` — the discovery board. Rank by the seller, not by
        post count: ``sort="reputation"`` orders by
        ``effective_reputation_score``, which is the number a hiring
        decision should cite.

        Args:
            category: Filter by service category.
            skills: Any-match filter (array overlap), sent as repeated
                ``skills`` query params.
            seller: Filter by seller executor UUID.
            min_reputation: 0-100. Compares against the seller's EFFECTIVE
                score (on-chain reconciled when present, else heuristic).
            max_price_usd: Only listings at or below this unit price.
            sort: ``recent`` (default) | ``reputation`` | ``price``.
            exclude_flagged: Default ``True`` — hides sellers whose completed
                history sits with a single counterparty (the wash-trading
                shape a bare score cannot show). Pass ``False`` to see the
                whole board and judge for yourself.
            limit: Page size, max 100.
            offset: Pagination offset.
        """
        params: dict[str, Any] = {
            "sort": sort,
            "exclude_flagged": exclude_flagged,
            "limit": limit,
            "offset": offset,
        }
        if category:
            params["category"] = category
        if skills:
            params["skills"] = skills
        if seller:
            params["seller"] = seller
        if min_reputation is not None:
            params["min_reputation"] = min_reputation
        if max_price_usd is not None:
            params["max_price_usd"] = max_price_usd
        data = await self._client._request("GET", "/services", params=params)
        return ServiceListingList.model_validate(data)

    async def get(self, listing_id: str) -> ServiceListing:
        """Detail of one listing + the seller's trust picture (public read).

        ``GET /services/{listing_id}`` — this is where a buyer vets before
        committing money, so it SHOWS a flagged seller
        (``seller_correlation``) instead of hiding it, and carries the
        on-chain score alongside the effective one. Browse filters; detail
        explains.
        """
        data = await self._client._request("GET", f"/services/{listing_id}")
        return ServiceListing.model_validate(data)

    async def find_sellers(
        self,
        task_id: str,
        *,
        limit: int = 5,
    ) -> ServiceListingList:
        """Sellers who already offer what one of your tasks asks for.

        ``GET /services/match/for-task/{task_id}`` — same category, price
        within the bounty, ranked by the seller's effective reputation.
        Read-only: ordering one of them is a separate, explicit
        :meth:`order` call. Answers "do I really need to publish this?".

        Args:
            task_id: A published task UUID (404 if unknown).
            limit: Max suggestions, 1-20.
        """
        data = await self._client._request(
            "GET",
            f"/services/match/for-task/{task_id}",
            params={"limit": limit},
        )
        return ServiceListingList.model_validate(data)

    # -- buyer side (the only money-moving call) ----------------------------

    async def order(
        self,
        listing_id: str,
        *,
        payment_auth: str | None = None,
        bounty_usd_override: float | None = None,
        deadline_hours: int | None = None,
        custom_instructions: str | None = None,
        payment_network: str | None = None,
    ) -> ServiceOrder:
        """BUY a listing — creates an escrowed task and assigns the seller.

        ``POST /services/{listing_id}/order`` — the ONLY money-moving call
        in this namespace. It creates a demand-side task
        (``"Order: <listing title>"``), files the seller's application, then
        assigns the seller through the canonical assign handler, which locks
        escrow.

        Args:
            listing_id: Listing to buy (404 unknown, 409 if not ``active``).
            payment_auth: Escrow authorization signed for the SELLER's
                wallet as receiver, sent as ``X-Payment-Auth``. Build it
                with :func:`~em_plugin_sdk.build_escrow_pre_auth` using the
                listing's ``seller_wallet``, the listing price and the
                network you pass in ``payment_network``. Required for the
                escrow lock — the assign handler rejects an escrow-mode
                task without it.
            bounty_usd_override: Optional price confirmation. When present
                it MUST equal the listing's ``unit_price_usd`` (422
                ``price_mismatch`` otherwise) — the listing price is
                authoritative.
            deadline_hours: 1-720, default 24.
            custom_instructions: Appended to the listing description in the
                order's task instructions (max 2000 chars).
            payment_network: The chain YOU pay on. Must be escrow-capable,
                enabled, and in the listing's ``accepted_networks`` — a 422
                names which of the three failed (``NETWORK_NO_ESCROW`` |
                ``NETWORK_DISABLED`` | ``NETWORK_NOT_ACCEPTED``). Omit to
                use the listing's own network.

        Raises:
            EMError: 400 when buyer wallet == seller wallet (you cannot
                order your own listing), 402 when the balance gate rejects
                the payment, 502 ``escrow_setup_failed`` when the escrow
                marker could not be created (nothing is charged; the order's
                task is cancelled — retry). A failed order never leaves a
                live task behind.
        """
        body: dict[str, Any] = {}
        if bounty_usd_override is not None:
            body["bounty_usd_override"] = bounty_usd_override
        if deadline_hours is not None:
            body["deadline_hours"] = deadline_hours
        if custom_instructions is not None:
            body["custom_instructions"] = custom_instructions
        if payment_network is not None:
            body["payment_network"] = payment_network
        headers = dict(self._auth_headers() or {})
        if payment_auth:
            headers["X-Payment-Auth"] = payment_auth
        data = await self._client._request(
            "POST",
            f"/services/{listing_id}/order",
            json=body,
            headers=headers or None,
        )
        return ServiceOrder.model_validate(data)
