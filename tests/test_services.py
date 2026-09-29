"""Tests for the services resource (supply side — service listings)."""

import json

import pytest
import httpx
import respx

from uvd_em_sdk import EMClient

BASE = "https://api.execution.market/api/v1"

LISTING_ID = "11111111-2222-3333-4444-555555555555"
TASK_ID = "99999999-8888-7777-6666-555555555555"
SELLER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SELLER_WALLET = "0x" + "ab" * 20
JWT = "eyJ.seller.jwt"

LISTING_JSON = {
    "id": LISTING_ID,
    "seller_executor_id": SELLER_ID,
    "seller_wallet": SELLER_WALLET,
    "title": "On-site store audit in Miami",
    "description": "I visit the store, photograph the shelves and report back",
    "category": "physical_presence",
    "unit_price_usd": 5.0,
    "skills": ["photography", "retail"],
    "evidence_schema": ["photo_geo"],
    "payment_network": "base",
    "accepted_networks": ["base", "polygon"],
    "availability": "active",
    "orders_count": 3,
    "created_at": "2026-08-01T00:00:00Z",
    "updated_at": None,
    "seller_reputation": {
        "reputation": 87.5,
        "tasks_completed": 12,
        "avg_rating": 4.6,
    },
    "effective_reputation_score": 87.5,
    "onchain_reputation_score": 90.0,
    "seller_correlation": {
        "total_completed": 12,
        "distinct_counterparties": 7,
        "top_counterparty_share": 0.25,
        "flagged": False,
    },
}


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test") as c:
        yield c


@pytest.fixture
async def jwt_client(mock_router):
    """Human seller — the Supabase JWT door of ``verify_listing_auth``."""
    async with EMClient(api_key="em_test", supabase_jwt=JWT) as c:
        yield c


class TestServiceListings:
    async def test_publish(self, mock_router, client):
        route = mock_router.post("/services").mock(
            return_value=httpx.Response(201, json=LISTING_JSON)
        )
        listing = await client.services.publish(
            title="On-site store audit in Miami",
            description="I visit the store, photograph the shelves and report back",
            category="physical_presence",
            unit_price_usd=5.0,
            skills=["photography", "retail"],
            evidence_schema=["photo_geo"],
        )
        assert listing.id == LISTING_ID
        assert listing.seller_wallet == SELLER_WALLET
        body = json.loads(route.calls.last.request.content)
        assert body == {
            "title": "On-site store audit in Miami",
            "description": "I visit the store, photograph the shelves and report back",
            "category": "physical_presence",
            "unit_price_usd": 5.0,
            "skills": ["photography", "retail"],
            "evidence_schema": ["photo_geo"],
        }
        # Never a seller field — the backend binds it to the caller's wallet.
        assert "seller_executor_id" not in body

    async def test_publish_omits_unset_optionals(self, mock_router, client):
        route = mock_router.post("/services").mock(
            return_value=httpx.Response(201, json=LISTING_JSON)
        )
        await client.services.publish(
            title="On-site store audit in Miami",
            description="I visit the store, photograph the shelves and report back",
            category="physical_presence",
            unit_price_usd=5.0,
        )
        body = json.loads(route.calls.last.request.content)
        # Server defaults apply (payment_network=base, evidence=['text_response'],
        # accepted_networks=all escrow-capable) — the SDK must not shadow them.
        assert set(body) == {"title", "description", "category", "unit_price_usd"}

    async def test_publish_with_networks(self, mock_router, client):
        route = mock_router.post("/services").mock(
            return_value=httpx.Response(201, json=LISTING_JSON)
        )
        await client.services.publish(
            title="On-site store audit in Miami",
            description="I visit the store, photograph the shelves and report back",
            category="physical_presence",
            unit_price_usd=5.0,
            payment_network="polygon",
            accepted_networks=["base", "polygon"],
        )
        body = json.loads(route.calls.last.request.content)
        assert body["payment_network"] == "polygon"
        assert body["accepted_networks"] == ["base", "polygon"]

    async def test_publish_sends_jwt_for_human_seller(self, mock_router, jwt_client):
        route = mock_router.post("/services").mock(
            return_value=httpx.Response(201, json=LISTING_JSON)
        )
        await jwt_client.services.publish(
            title="On-site store audit in Miami",
            description="I visit the store, photograph the shelves and report back",
            category="physical_presence",
            unit_price_usd=5.0,
        )
        assert route.calls.last.request.headers["authorization"] == f"Bearer {JWT}"

    async def test_browse(self, mock_router, client):
        route = mock_router.get("/services").mock(
            return_value=httpx.Response(
                200, json={"listings": [LISTING_JSON], "count": 1, "offset": 0}
            )
        )
        result = await client.services.browse()
        assert result.count == 1
        assert result.listings[0].effective_reputation_score == 87.5
        params = route.calls.last.request.url.params
        assert params["sort"] == "recent"
        assert params["exclude_flagged"] == "true"
        assert params["limit"] == "20"
        assert params["offset"] == "0"

    async def test_browse_filters(self, mock_router, client):
        route = mock_router.get("/services").mock(
            return_value=httpx.Response(
                200, json={"listings": [], "count": 0, "offset": 20}
            )
        )
        await client.services.browse(
            category="physical_presence",
            skills=["photography", "retail"],
            seller=SELLER_ID,
            min_reputation=80,
            max_price_usd=10,
            sort="reputation",
            exclude_flagged=False,
            limit=50,
            offset=20,
        )
        params = route.calls.last.request.url.params
        assert params["category"] == "physical_presence"
        # Any-match filter travels as REPEATED query params, not a CSV string.
        assert params.get_list("skills") == ["photography", "retail"]
        assert params["seller"] == SELLER_ID
        assert params["min_reputation"] == "80"
        assert params["max_price_usd"] == "10"
        assert params["sort"] == "reputation"
        assert params["exclude_flagged"] == "false"

    async def test_get(self, mock_router, client):
        mock_router.get(f"/services/{LISTING_ID}").mock(
            return_value=httpx.Response(200, json=LISTING_JSON)
        )
        listing = await client.services.get(LISTING_ID)
        assert listing.title == "On-site store audit in Miami"
        # Detail carries the trust picture browse can only filter on.
        assert listing.onchain_reputation_score == 90.0
        assert listing.seller_correlation is not None
        assert listing.seller_correlation.flagged is False
        assert listing.seller_reputation is not None
        assert listing.seller_reputation.tasks_completed == 12

    async def test_mine_includes_paused(self, mock_router, jwt_client):
        paused = {**LISTING_JSON, "availability": "paused"}
        route = mock_router.get("/services/mine").mock(
            return_value=httpx.Response(
                200, json={"listings": [paused], "count": 1, "offset": 0}
            )
        )
        result = await jwt_client.services.mine(limit=10)
        assert result.listings[0].availability == "paused"
        request = route.calls.last.request
        assert request.url.params["limit"] == "10"
        assert request.headers["authorization"] == f"Bearer {JWT}"

    async def test_update(self, mock_router, client):
        route = mock_router.patch(f"/services/{LISTING_ID}").mock(
            return_value=httpx.Response(
                200, json={**LISTING_JSON, "availability": "paused"}
            )
        )
        listing = await client.services.update(
            LISTING_ID, availability="paused", unit_price_usd=7.5
        )
        assert listing.availability == "paused"
        body = json.loads(route.calls.last.request.content)
        assert body == {"availability": "paused", "unit_price_usd": 7.5}

    async def test_update_empty_is_noop_body(self, mock_router, client):
        route = mock_router.patch(f"/services/{LISTING_ID}").mock(
            return_value=httpx.Response(200, json=LISTING_JSON)
        )
        await client.services.update(LISTING_ID)
        assert json.loads(route.calls.last.request.content) == {}

    async def test_find_sellers(self, mock_router, client):
        route = mock_router.get(f"/services/match/for-task/{TASK_ID}").mock(
            return_value=httpx.Response(
                200, json={"listings": [LISTING_JSON], "count": 1, "offset": 0}
            )
        )
        result = await client.services.find_sellers(TASK_ID, limit=3)
        assert result.listings[0].id == LISTING_ID
        assert route.calls.last.request.url.params["limit"] == "3"


class TestServiceOrder:
    ORDER_JSON = {
        "task_id": TASK_ID,
        "listing_id": LISTING_ID,
        "seller_executor_id": SELLER_ID,
        "bounty_usd": 5.0,
        "escrow_status": "locked",
        "payment_network": "base",
        "task_status": "accepted",
    }

    async def test_order_sends_payment_auth_header(self, mock_router, client):
        route = mock_router.post(f"/services/{LISTING_ID}/order").mock(
            return_value=httpx.Response(200, json=self.ORDER_JSON)
        )
        order = await client.services.order(
            LISTING_ID,
            payment_auth="0xsignedescrowauth",
            payment_network="base",
            deadline_hours=12,
        )
        assert order.task_id == TASK_ID
        assert order.escrow_status == "locked"
        request = route.calls.last.request
        # The escrow authorization rides in the header, never the body.
        assert request.headers["x-payment-auth"] == "0xsignedescrowauth"
        assert json.loads(request.content) == {
            "deadline_hours": 12,
            "payment_network": "base",
        }

    async def test_order_async_lock(self, mock_router, client):
        mock_router.post(f"/services/{LISTING_ID}/order").mock(
            return_value=httpx.Response(
                200,
                json={
                    **self.ORDER_JSON,
                    "escrow_status": "assigning",
                    "task_status": "assigning",
                },
            )
        )
        order = await client.services.order(LISTING_ID, payment_auth="0xauth")
        assert order.escrow_status == "assigning"

    async def test_order_price_mismatch_raises(self, mock_router, client):
        from uvd_em_sdk import EMValidationError

        mock_router.post(f"/services/{LISTING_ID}/order").mock(
            return_value=httpx.Response(
                422,
                json={
                    "message": (
                        "price_mismatch: bounty_usd_override (1.0) must equal "
                        "the listing unit_price_usd (5.0)."
                    )
                },
            )
        )
        with pytest.raises(EMValidationError) as exc:
            await client.services.order(
                LISTING_ID, payment_auth="0xauth", bounty_usd_override=1.0
            )
        assert "price_mismatch" in str(exc.value)
