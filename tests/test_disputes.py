"""Tests for the disputes resource (SDK-13)."""

import json

import pytest
import httpx
import respx

from uvd_em_sdk import DisputeReason, EMClient, EMValidationError

BASE = "https://api.execution.market/api/v1"

SUB_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test") as c:
        yield c


class TestDisputes:
    async def test_list(self, mock_router, client):
        route = mock_router.get("/disputes").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [{"id": "d-1", "status": "open", "reason": "other"}],
                    "total": 1,
                },
            )
        )
        result = await client.disputes.list(status="open", limit=10)
        assert result["total"] == 1
        params = route.calls.last.request.url.params
        assert params["status"] == "open"
        assert params["limit"] == "10"

    async def test_available(self, mock_router, client):
        route = mock_router.get("/disputes/available").mock(
            return_value=httpx.Response(200, json={"items": [], "total": 0})
        )
        result = await client.disputes.available(category="research")
        assert result["total"] == 0
        assert route.calls.last.request.url.params["category"] == "research"

    async def test_get(self, mock_router, client):
        mock_router.get("/disputes/d-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "d-1",
                    "status": "open",
                    "reason": "fake_evidence",
                    "arbiter_verdict_data": {"verdict": "INCONCLUSIVE"},
                },
            )
        )
        result = await client.disputes.get("d-1")
        assert result["reason"] == "fake_evidence"

    async def test_create(self, mock_router, client):
        route = mock_router.post("/disputes").mock(
            return_value=httpx.Response(
                201, json={"id": "d-2", "status": "open", "reason": "fake_evidence"}
            )
        )
        result = await client.disputes.create(
            submission_id=SUB_ID,
            reason=DisputeReason.FAKE_EVIDENCE,
            description="GPS metadata does not match the storefront",
        )
        assert result["id"] == "d-2"
        body = json.loads(route.calls.last.request.content)
        assert body == {
            "submission_id": SUB_ID,
            "reason": "fake_evidence",
            "description": "GPS metadata does not match the storefront",
        }

    async def test_create_invalid_reason_raises_locally(self, mock_router, client):
        """The backend 422s on unknown dispute_reason values — fail fast
        locally (no route mocked, no request happens)."""
        with pytest.raises(EMValidationError, match="Invalid dispute reason"):
            await client.disputes.create(
                submission_id=SUB_ID,
                reason="i_dont_like_it",
                description="Not a valid enum value",
            )

    async def test_create_short_description_raises_locally(self, mock_router, client):
        with pytest.raises(EMValidationError, match="5-2000"):
            await client.disputes.create(
                submission_id=SUB_ID,
                reason="other",
                description="hey",
            )

    async def test_resolve_split_sends_split_pct(self, mock_router, client):
        route = mock_router.post("/disputes/d-1/resolve").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "dispute_id": "d-1",
                    "verdict": "split",
                    "agent_refund_usdc": 5.0,
                    "executor_payout_usdc": 5.0,
                    "resolved_at": "2026-07-01T00:00:00Z",
                },
            )
        )
        result = await client.disputes.resolve(
            "d-1", verdict="split", reason="Both parties partially right", split_pct=50
        )
        assert result["verdict"] == "split"
        body = json.loads(route.calls.last.request.content)
        assert body["split_pct"] == 50

    async def test_resolve_split_without_pct_raises_locally(self, mock_router, client):
        with pytest.raises(EMValidationError, match="split_pct"):
            await client.disputes.resolve(
                "d-1", verdict="split", reason="Missing the split percentage"
            )

    async def test_resolve_invalid_verdict_raises_locally(self, mock_router, client):
        with pytest.raises(EMValidationError, match="verdict"):
            await client.disputes.resolve(
                "d-1", verdict="maybe", reason="Not a valid verdict"
            )
