"""Tests for H2A and agents resources."""

import json

import pytest
import httpx
import respx

from uvd_em_sdk import EMClient, EMValidationError

BASE = "https://api.execution.market/api/v1"


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test") as c:
        yield c


class TestH2A:
    async def test_publish_hits_canonical_route(self, mock_router, client):
        """SDK-40: publish must target POST /api/v1/publish, not the
        deprecated /h2a/tasks alias."""
        route = mock_router.post("/publish").mock(
            return_value=httpx.Response(
                201,
                json={
                    "task_id": "h2a-1",
                    "status": "published",
                    "bounty_usd": 5.0,
                    "fee_usd": 0.65,
                    "total_required_usd": 5.0,
                    "deadline": "2026-04-01",
                },
            )
        )
        result = await client.h2a.publish(
            title="Research competitors",
            instructions="Find pricing data for top 5 competitors in the market",
            category="research",
            bounty_usd=5.0,
        )
        assert result["task_id"] == "h2a-1"
        body = json.loads(route.calls.last.request.content)
        assert body["payment_token"] == "USDC"
        assert body["target_executor_type"] == "agent"

    async def test_publish_full_contract(self, mock_router, client):
        """SDK-32: target_executor_type / publisher_wallet / payment_token /
        verification_mode reach the wire (backend is extra='forbid')."""
        route = mock_router.post("/publish").mock(
            return_value=httpx.Response(
                201,
                json={
                    "task_id": "h2a-2",
                    "status": "published",
                    "bounty_usd": 20.0,
                    "fee_usd": 2.6,
                    "total_required_usd": 20.0,
                    "deadline": "2026-04-01",
                },
            )
        )
        wallet = "0x" + "ab" * 20
        await client.h2a.publish(
            title="Deliver groceries",
            instructions="Pick up and deliver the order to the address",
            category="physical_presence",
            bounty_usd=20.0,
            payment_network="base",
            payment_token="EURC",
            target_executor_type="human",
            publisher_wallet=wallet,
            verification_mode="manual",
        )
        body = json.loads(route.calls.last.request.content)
        assert body["target_executor_type"] == "human"
        assert body["publisher_wallet"] == wallet
        assert body["payment_token"] == "EURC"
        assert body["verification_mode"] == "manual"
        assert body["bounty_usd"] == 20.0

    async def test_publish_bounty_over_500_rejected_locally(self, mock_router, client):
        """SDK-32: backend caps bounty_usd at le=500 — the SDK must fail
        fast locally, before any request. No route is mocked on purpose:
        if publish() hit the network, respx would raise on the unmatched
        request (and assert_all_called would flag a mocked-but-uncalled
        route)."""
        with pytest.raises(EMValidationError, match="500"):
            await client.h2a.publish(
                title="Too expensive",
                instructions="This bounty exceeds the H2A publish cap",
                category="research",
                bounty_usd=501,
            )

    async def test_list(self, mock_router, client):
        mock_router.get("/h2a/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "tasks": [{"id": "h2a-1", "title": "Research"}],
                    "total": 1,
                },
            )
        )
        result = await client.h2a.list()
        assert result["total"] == 1

    async def test_get(self, mock_router, client):
        mock_router.get("/h2a/tasks/h2a-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "h2a-1",
                    "title": "Research",
                    "status": "published",
                },
            )
        )
        result = await client.h2a.get("h2a-1")
        assert result["id"] == "h2a-1"

    async def test_submissions(self, mock_router, client):
        mock_router.get("/h2a/tasks/h2a-1/submissions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "submissions": [{"id": "sub-1", "status": "pending"}],
                    "count": 1,
                },
            )
        )
        result = await client.h2a.submissions("h2a-1")
        assert result["count"] == 1

    async def test_approve_accepted_sends_worker_score(self, mock_router, client):
        """SDK-33: worker_score (1-5) is mandatory for verdict='accepted'
        (EM_H2A_REQUIRE_RATING) and must reach the wire."""
        route = mock_router.post("/h2a/tasks/h2a-1/approve").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "completed",
                    "worker_tx": "0xabc",
                },
            )
        )
        result = await client.h2a.approve(
            "h2a-1", submission_id="sub-1", worker_score=5
        )
        assert result["status"] == "completed"
        body = json.loads(route.calls.last.request.content)
        assert body["verdict"] == "accepted"
        assert body["worker_score"] == 5

    async def test_approve_accepted_without_score_raises_locally(
        self, mock_router, client
    ):
        """SDK-33: accepted without worker_score would 422 in prod — the
        SDK fails fast locally (no route mocked, no request happens)."""
        with pytest.raises(EMValidationError, match="worker_score"):
            await client.h2a.approve("h2a-1", submission_id="sub-1")

    async def test_approve_score_out_of_range_raises_locally(self, mock_router, client):
        for bad_score in (0, 6):
            with pytest.raises(EMValidationError, match="between 1 and 5"):
                await client.h2a.approve(
                    "h2a-1", submission_id="sub-1", worker_score=bad_score
                )

    async def test_approve_invalid_verdict_raises_locally(self, mock_router, client):
        with pytest.raises(EMValidationError, match="verdict"):
            await client.h2a.approve("h2a-1", submission_id="sub-1", verdict="maybe")

    async def test_approve_needs_revision_without_score_ok(self, mock_router, client):
        route = mock_router.post("/h2a/tasks/h2a-1/approve").mock(
            return_value=httpx.Response(200, json={"status": "needs_revision"})
        )
        await client.h2a.approve(
            "h2a-1",
            submission_id="sub-1",
            verdict="needs_revision",
            notes="Add the receipt photo",
        )
        body = json.loads(route.calls.last.request.content)
        assert body["verdict"] == "needs_revision"
        assert "worker_score" not in body

    async def test_reject_delegates_to_approve_verdict_rejected(
        self, mock_router, client
    ):
        """SDK-41: /reject was removed server-side — reject() must emit
        POST .../approve with verdict='rejected' (which refunds the escrow)."""
        route = mock_router.post("/h2a/tasks/h2a-1/approve").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "rejected",
                },
            )
        )
        result = await client.h2a.reject(
            "h2a-1", submission_id="sub-1", notes="Incomplete data"
        )
        assert result["status"] == "rejected"
        body = json.loads(route.calls.last.request.content)
        assert body["verdict"] == "rejected"
        assert body["submission_id"] == "sub-1"
        assert body["notes"] == "Incomplete data"

    async def test_cancel(self, mock_router, client):
        mock_router.post("/h2a/tasks/h2a-1/cancel").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                },
            )
        )
        result = await client.h2a.cancel("h2a-1")
        assert result["success"] is True


class TestH2ALifecycle:
    """SDK-11: applications / assign / rate-publisher / payment-config."""

    TASK = "11111111-2222-3333-4444-555555555555"

    async def test_applications(self, mock_router, client):
        mock_router.get(f"/h2a/tasks/{self.TASK}/applications").mock(
            return_value=httpx.Response(
                200,
                json={
                    "task_id": self.TASK,
                    "applications": [
                        {
                            "id": "app-1",
                            "task_id": self.TASK,
                            "executor_id": "exec-1",
                            "message": "I can do this",
                            "status": "pending",
                            "created_at": "2026-07-01T00:00:00Z",
                            "executor": {
                                "display_name": "Worker One",
                                "reputation_score": 92,
                            },
                        }
                    ],
                    "count": 1,
                },
            )
        )
        result = await client.h2a.applications(self.TASK)
        assert result.count == 1
        assert result.applications[0].executor_id == "exec-1"
        assert result.applications[0].executor["display_name"] == "Worker One"

    async def test_assign_sends_payment_auth_header(self, mock_router, client):
        """Escrow-mode assign: the publisher's EIP-3009 auth travels in the
        X-Payment-Auth header (signed AT ASSIGNMENT — the nonce commits to
        the receiver)."""
        route = mock_router.post(f"/h2a/tasks/{self.TASK}/assign").mock(
            return_value=httpx.Response(
                200, json={"success": True, "escrow_tx": "0xlock"}
            )
        )
        payment_auth = '{"x402Version":2,"scheme":"escrow"}'
        result = await client.h2a.assign(self.TASK, "exec-1", payment_auth=payment_auth)
        assert result["success"] is True
        request = route.calls.last.request
        assert request.headers["x-payment-auth"] == payment_auth
        body = json.loads(request.content)
        assert body == {"executor_id": "exec-1"}

    async def test_assign_without_payment_auth_sends_no_header(
        self, mock_router, client
    ):
        """Legacy (non-escrow) assign is status-only — no X-Payment-Auth.
        On an escrow-mode task the backend answers 402."""
        route = mock_router.post(f"/h2a/tasks/{self.TASK}/assign").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        await client.h2a.assign(self.TASK, "exec-1")
        assert "x-payment-auth" not in route.calls.last.request.headers

    async def test_rate_publisher(self, mock_router, client):
        route = mock_router.post(f"/h2a/tasks/{self.TASK}/rate-publisher").mock(
            return_value=httpx.Response(200, json={"success": True, "tx": "0xfeed"})
        )
        result = await client.h2a.rate_publisher(
            self.TASK, score=5, comment="Great publisher"
        )
        assert result["success"] is True
        body = json.loads(route.calls.last.request.content)
        assert body == {"score": 5, "comment": "Great publisher"}

    async def test_rate_publisher_score_out_of_range_raises_locally(
        self, mock_router, client
    ):
        for bad_score in (0, 6):
            with pytest.raises(EMValidationError, match="between 1 and 5"):
                await client.h2a.rate_publisher(self.TASK, score=bad_score)

    async def test_payment_config(self, mock_router, client):
        mock_router.get("/h2a/payment-config").mock(
            return_value=httpx.Response(
                200,
                json={
                    "treasury": "0x" + "11" * 20,
                    "fee_pct": 0.13,
                    "escrow": {
                        "payment_info_typehash": "0x" + "ab" * 32,
                        "min_fee_bps": 0,
                        "max_fee_bps": 1800,
                        "deposit_limit_usd": 100,
                        "tier_timings": {
                            "micro": {"pre": 3600, "auth": 7200, "refund": 86400}
                        },
                        "networks": {
                            "base": {"chain_id": 8453},
                            "ethereum": {"chain_id": 1},
                        },
                    },
                },
            )
        )
        config = await client.h2a.payment_config()
        assert config.fee_pct == 0.13
        assert config.escrow_networks == ["base", "ethereum"]
        assert config.escrow["max_fee_bps"] == 1800


class TestH2ASupabaseJwtAuth:
    """SDK-34: the h2a namespace requires the human's Supabase JWT —
    EMClient(supabase_jwt=...) attaches it to h2a.* requests only."""

    JWT = "eyJ.test.jwt"

    async def test_h2a_sends_supabase_jwt(self, mock_router):
        route = mock_router.get("/h2a/tasks/h2a-1").mock(
            return_value=httpx.Response(200, json={"id": "h2a-1"})
        )
        async with EMClient(supabase_jwt=self.JWT) as c:
            await c.h2a.get("h2a-1")
        auth = route.calls.last.request.headers["authorization"]
        assert auth == f"Bearer {self.JWT}"

    async def test_h2a_write_sends_supabase_jwt(self, mock_router):
        route = mock_router.post("/h2a/tasks/h2a-1/approve").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        async with EMClient(supabase_jwt=self.JWT) as c:
            await c.h2a.approve("h2a-1", submission_id="sub-1", worker_score=4)
        auth = route.calls.last.request.headers["authorization"]
        assert auth == f"Bearer {self.JWT}"

    async def test_jwt_scoped_to_h2a_namespace_only(self, mock_router):
        """With api_key + supabase_jwt, h2a.* carries the JWT while every
        other namespace keeps the API-key Bearer."""
        h2a_route = mock_router.get("/h2a/tasks").mock(
            return_value=httpx.Response(200, json={"tasks": [], "total": 0})
        )
        health_route = mock_router.get("/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        async with EMClient(api_key="em_test", supabase_jwt=self.JWT) as c:
            await c.h2a.list()
            await c.health()
        assert (
            h2a_route.calls.last.request.headers["authorization"]
            == f"Bearer {self.JWT}"
        )
        assert (
            health_route.calls.last.request.headers["authorization"] == "Bearer em_test"
        )


class TestAgents:
    async def test_directory(self, mock_router, client):
        mock_router.get("/agents/directory").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agents": [{"executor_id": "a-1", "display_name": "ResearchBot"}],
                    "total": 1,
                },
            )
        )
        result = await client.agents.directory()
        assert result["total"] == 1

    async def test_register_executor(self, mock_router, client):
        mock_router.post("/agents/register-executor").mock(
            return_value=httpx.Response(
                201,
                json={
                    "executor_id": "a-1",
                    "display_name": "ResearchBot",
                    "capabilities": ["research"],
                },
            )
        )
        result = await client.agents.register_executor(
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
            capabilities=["research"],
            display_name="ResearchBot",
        )
        assert result["display_name"] == "ResearchBot"
