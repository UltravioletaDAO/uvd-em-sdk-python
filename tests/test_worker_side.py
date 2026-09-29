"""Tests for the worker-side endpoints (SDK-18)."""

import json

import pytest
import httpx
import respx

from uvd_em_sdk import EMClient

BASE = "https://api.execution.market/api/v1"

TASK_ID = "11111111-2222-3333-4444-555555555555"
SUB_ID = "66666666-7777-8888-9999-000000000000"
EXECUTOR_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
JWT = "eyJ.worker.jwt"


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test", supabase_jwt=JWT) as c:
        yield c


class TestWorkerSide:
    async def test_my_submission(self, mock_router, client):
        route = mock_router.get(f"/workers/tasks/{TASK_ID}/my-submission").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "Submission retrieved",
                    "data": {
                        "id": SUB_ID,
                        "task_id": TASK_ID,
                        "executor_id": EXECUTOR_ID,
                        "agent_verdict": "pending",
                    },
                },
            )
        )
        result = await client.workers.my_submission(TASK_ID, EXECUTOR_ID)
        assert result["data"]["id"] == SUB_ID
        request = route.calls.last.request
        assert request.url.params["executor_id"] == EXECUTOR_ID
        # Worker-scoped read carries the worker's Supabase JWT, not the API key.
        assert request.headers["authorization"] == f"Bearer {JWT}"

    async def test_geo_reference(self, mock_router, client):
        route = mock_router.get(f"/workers/tasks/{TASK_ID}/geo-reference").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "Geo reference retrieved",
                    "data": {
                        "task_id": TASK_ID,
                        "has_reference": True,
                        "location_lat": 25.77,
                        "location_lng": -80.19,
                        "geo_match_mode": "strict",
                        "radius_m": 500,
                    },
                },
            )
        )
        result = await client.workers.geo_reference(TASK_ID, EXECUTOR_ID)
        assert result["data"]["radius_m"] == 500
        assert route.calls.last.request.url.params["executor_id"] == EXECUTOR_ID

    async def test_update_social_links(self, mock_router, client):
        route = mock_router.put("/workers/social-links").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "Social link for x updated",
                    "data": {"platform": "x", "handle": "@worker_one"},
                },
            )
        )
        result = await client.workers.update_social_links("x", "@worker_one")
        assert result["data"]["handle"] == "@worker_one"
        request = route.calls.last.request
        assert json.loads(request.content) == {
            "platform": "x",
            "handle": "@worker_one",
        }
        assert request.headers["authorization"] == f"Bearer {JWT}"

    async def test_submission_get(self, mock_router, client):
        route = mock_router.get(f"/submissions/{SUB_ID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "Submission retrieved",
                    "data": {
                        "id": SUB_ID,
                        "task_id": TASK_ID,
                        "status": "approved",
                        "ai_verification_result": {"verdict": "PASS"},
                        "arbiter_verdict": "release",
                        "payment_tx": "0xpay",
                    },
                },
            )
        )
        result = await client.submissions.get(SUB_ID)
        assert result["data"]["ai_verification_result"]["verdict"] == "PASS"
        assert route.calls.last.request.headers["authorization"] == f"Bearer {JWT}"

    async def test_worker_side_without_jwt_keeps_api_key(self, mock_router):
        """Without supabase_jwt the client default (API-key Bearer) rides
        along — the backend then applies its staged-rollout fallback."""
        route = mock_router.get(f"/submissions/{SUB_ID}").mock(
            return_value=httpx.Response(
                200, json={"message": "ok", "data": {"id": SUB_ID}}
            )
        )
        async with EMClient(api_key="em_test") as c:
            await c.submissions.get(SUB_ID)
        assert route.calls.last.request.headers["authorization"] == "Bearer em_test"
