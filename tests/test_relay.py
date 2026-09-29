"""Tests for the relay-chain resource."""

import pytest
import httpx
import respx

from uvd_em_sdk import EMClient

BASE = "https://api.execution.market/api/v1"


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test") as c:
        yield c


class TestRelay:
    async def test_create(self, mock_router, client):
        mock_router.post("/relay-chains").mock(
            return_value=httpx.Response(
                201,
                json={
                    "chain_id": "rc-1",
                    "parent_task_id": "task-1",
                    "legs": 3,
                },
            )
        )
        result = await client.relay.create("task-1", legs=3)
        assert result["chain_id"] == "rc-1"

    async def test_get(self, mock_router, client):
        mock_router.get("/relay-chains/rc-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "chain_id": "rc-1",
                    "status": "in_progress",
                    "legs": [
                        {"number": 1, "status": "completed"},
                        {"number": 2, "status": "active"},
                    ],
                },
            )
        )
        result = await client.relay.get("rc-1")
        assert len(result["legs"]) == 2

    async def test_assign_leg(self, mock_router, client):
        mock_router.post("/relay-chains/rc-1/legs/1/assign").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                },
            )
        )
        result = await client.relay.assign_leg("rc-1", 1, "exec-1")
        assert result["success"] is True

    async def test_handoff(self, mock_router, client):
        mock_router.post("/relay-chains/rc-1/legs/1/handoff").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "next_leg": 2,
                },
            )
        )
        result = await client.relay.handoff(
            "rc-1", 1, notes="Package delivered to checkpoint"
        )
        assert result["next_leg"] == 2
