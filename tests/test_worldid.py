"""Tests for the World ID resource (SDK-15)."""

import json

import pytest
import httpx
import respx

from uvd_em_sdk import EMClient

BASE = "https://api.execution.market/api/v1"

EXECUTOR_ID = "11111111-2222-3333-4444-555555555555"
WALLET = "0x" + "ab" * 20


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test") as c:
        yield c


class TestWorldId:
    async def test_rp_signature(self, mock_router, client):
        route = mock_router.get("/world-id/rp-signature").mock(
            return_value=httpx.Response(
                200,
                json={
                    "nonce": "abc123",
                    "created_at": 1751500000,
                    "expires_at": 1751500600,
                    "action": "verify-worker",
                    "signature": "0xsig",
                    "rp_id": "rp_test",
                    "app_id": "app_test",
                },
            )
        )
        result = await client.worldid.rp_signature()
        assert result["nonce"] == "abc123"
        assert route.calls.last.request.url.params["action"] == "verify-worker"

    async def test_verify(self, mock_router, client):
        route = mock_router.post("/world-id/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "verified": True,
                    "verification_level": "orb",
                    "message": "Verified at orb level",
                },
            )
        )
        result = await client.worldid.verify(
            nullifier_hash="0xnull",
            verification_level="orb",
            executor_id=EXECUTOR_ID,
            protocol_version="4.0",
            nonce="abc123",
            responses=[{"proof": "0xproof"}],
        )
        assert result["verified"] is True
        body = json.loads(route.calls.last.request.content)
        assert body["nullifier_hash"] == "0xnull"
        assert body["verification_level"] == "orb"
        assert body["executor_id"] == EXECUTOR_ID
        assert body["protocol_version"] == "4.0"
        assert body["responses"] == [{"proof": "0xproof"}]

    async def test_worker_status(self, mock_router, client):
        route = mock_router.get("/workers/world-status").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "World verification status retrieved",
                    "data": {"verified": True, "level": "orb"},
                },
            )
        )
        result = await client.worldid.worker_status(WALLET)
        assert result["data"]["verified"] is True
        assert route.calls.last.request.url.params["wallet"] == WALLET
