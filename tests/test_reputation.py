"""Tests for the reputation resource."""

import json

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


class TestReputationRead:
    async def test_get_agent(self, mock_router, client):
        mock_router.get("/reputation/agents/2106").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": 2106,
                    "count": 42,
                    "score": 87.5,
                    "network": "base",
                },
            )
        )
        rep = await client.reputation.get_agent(2106)
        assert rep.agent_id == 2106
        assert rep.score == 87.5

    async def test_get_agent_identity(self, mock_router, client):
        mock_router.get("/reputation/agents/2106/identity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": 2106,
                    "owner": "0xABC",
                    "agent_uri": "https://execution.market",
                    "network": "base",
                    "name": "Execution Market",
                },
            )
        )
        identity = await client.reputation.get_agent_identity(2106)
        assert identity.name == "Execution Market"
        assert identity.owner == "0xABC"

    async def test_leaderboard(self, mock_router, client):
        mock_router.get("/reputation/leaderboard").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [{"id": "w1", "score": 95}],
                    "total": 1,
                },
            )
        )
        result = await client.reputation.leaderboard(limit=10)
        assert result["total"] == 1

    async def test_info(self, mock_router, client):
        mock_router.get("/reputation/info").mock(
            return_value=httpx.Response(
                200,
                json={
                    "erc8004_available": True,
                    "network": "base",
                },
            )
        )
        info = await client.reputation.info()
        assert info["erc8004_available"] is True

    async def test_networks(self, mock_router, client):
        mock_router.get("/reputation/networks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "networks": ["base", "ethereum", "polygon"],
                },
            )
        )
        nets = await client.reputation.networks()
        assert "base" in nets

    async def test_em_reputation(self, mock_router, client):
        mock_router.get("/reputation/em").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": 2106,
                    "count": 10,
                    "score": 90,
                    "network": "base",
                },
            )
        )
        rep = await client.reputation.em_reputation()
        assert rep.agent_id == 2106

    async def test_em_identity(self, mock_router, client):
        mock_router.get("/reputation/em/identity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": 2106,
                    "owner": "0xABC",
                    "agent_uri": "uri",
                    "network": "base",
                },
            )
        )
        identity = await client.reputation.em_identity()
        assert identity.agent_id == 2106

    async def test_get_feedback(self, mock_router, client):
        mock_router.get("/reputation/feedback/task-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "task_id": "task-1",
                    "score": 85,
                    "comment": "Great",
                },
            )
        )
        fb = await client.reputation.get_feedback("task-1")
        assert fb["score"] == 85


class TestReputationWrite:
    async def test_rate_worker(self, mock_router, client):
        route = mock_router.post("/reputation/workers/rate").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "tx_hash": "0xabc",
                },
            )
        )
        result = await client.reputation.rate_worker(
            "task-1", 90, comment="Excellent", proof_tx="0xdeadbeef"
        )
        assert result["success"] is True
        # The endpoint takes task_id (a submission_id body 422s) and proof_tx
        # is what upgrades the rating to VERIFIED feedback.
        import json as _json

        body = _json.loads(route.calls.last.request.content)
        assert body["task_id"] == "task-1"
        assert body["proof_tx"] == "0xdeadbeef"
        assert "submission_id" not in body

    async def test_rate_agent(self, mock_router, client):
        route = mock_router.post("/reputation/agents/rate").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "tx_hash": "0xdef",
                },
            )
        )
        result = await client.reputation.rate_agent("task-1", 85)
        assert result["success"] is True
        # agent_id omitted → NOT in the payload; the server resolves the
        # requester's identity from the task (erc8004_agent_id, else the
        # publisher wallet).
        import json as _json

        assert _json.loads(route.calls.last.request.content) == {
            "task_id": "task-1",
            "score": 85,
        }

    async def test_rate_agent_with_explicit_agent_id(self, mock_router, client):
        route = mock_router.post("/reputation/agents/rate").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "tx_hash": "0xdef",
                },
            )
        )
        result = await client.reputation.rate_agent(
            "task-1", 85, comment="great", agent_id=2106
        )
        assert result["success"] is True
        import json as _json

        assert _json.loads(route.calls.last.request.content) == {
            "task_id": "task-1",
            "score": 85,
            "comment": "great",
            "agent_id": 2106,
        }

    async def test_register(self, mock_router, client):
        mock_router.post("/reputation/register").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "agent_id": 999,
                },
            )
        )
        result = await client.reputation.register(
            "0x1234567890abcdef1234567890abcdef12345678"
        )
        assert result["agent_id"] == 999

    async def test_prepare_feedback(self, mock_router, client):
        route = mock_router.post("/reputation/prepare-feedback").mock(
            return_value=httpx.Response(
                200,
                json={
                    "prepare_id": "prep-uuid-1",
                    "unsigned_tx": {"to": "0xABC", "data": "0x..."},
                },
            )
        )
        result = await client.reputation.prepare_feedback("task-1", 80)
        assert result["prepare_id"] == "prep-uuid-1"
        # worker_address omitted → NOT in the payload; the server resolves it
        # from the task's assigned executor (else the caller's wallet).
        import json as _json

        assert _json.loads(route.calls.last.request.content) == {
            "task_id": "task-1",
            "score": 80,
        }

    async def test_prepare_feedback_with_explicit_worker_address(
        self, mock_router, client
    ):
        route = mock_router.post("/reputation/prepare-feedback").mock(
            return_value=httpx.Response(
                200,
                json={"prepare_id": "prep-uuid-2"},
            )
        )
        result = await client.reputation.prepare_feedback(
            "task-1", 80, comment="great", worker_address=WALLET
        )
        assert result["prepare_id"] == "prep-uuid-2"
        import json as _json

        assert _json.loads(route.calls.last.request.content) == {
            "task_id": "task-1",
            "score": 80,
            "comment": "great",
            "worker_address": WALLET,
        }

    async def test_confirm_feedback(self, mock_router, client):
        route = mock_router.post("/reputation/confirm-feedback").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                },
            )
        )
        tx_hash = "0x" + "ab" * 32
        result = await client.reputation.confirm_feedback(
            "task-1", tx_hash, prepare_id="prep-uuid-1"
        )
        assert result["success"] is True
        # The server requires all three fields (prepare_id, tx_hash, task_id).
        import json as _json

        assert _json.loads(route.calls.last.request.content) == {
            "prepare_id": "prep-uuid-1",
            "tx_hash": tx_hash,
            "task_id": "task-1",
        }


WALLET = "0x1234567890abcdef1234567890abcdef12345678"


class TestReputationNetworkPreference:
    async def test_get_reputation_network_default_base(self, mock_router, client):
        mock_router.get(f"/workers/{WALLET}/reputation-network").mock(
            return_value=httpx.Response(
                200,
                json={
                    "wallet_address": WALLET,
                    "reputation_network": "base",
                    "selectable_networks": [
                        "base",
                        "ethereum",
                        "polygon",
                        "arbitrum",
                        "celo",
                        "monad",
                        "avalanche",
                        "optimism",
                        "skale",
                        "solana",
                    ],
                    "solana_enabled": False,
                    "feature_enabled": False,
                },
            )
        )
        pref = await client.reputation.get_reputation_network(WALLET)
        # Default chain is "base" and, while disabled, the selector is
        # read-only ("coming soon").
        assert pref.reputation_network == "base"
        assert pref.feature_enabled is False
        assert "solana" in pref.selectable_networks

    async def test_set_reputation_network(self, mock_router, client):
        route = mock_router.patch(f"/workers/{WALLET}/reputation-network").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "Reputation network set to 'arbitrum'",
                    "data": {"reputation_network": "arbitrum", "changed": True},
                },
            )
        )
        result = await client.reputation.set_reputation_network(WALLET, "arbitrum")
        assert result["data"]["reputation_network"] == "arbitrum"
        # Body carries the target network for the ERC-8128-signed PATCH.
        import json as _json

        assert _json.loads(route.calls.last.request.content) == {
            "reputation_network": "arbitrum"
        }

    async def test_set_reputation_network_disabled_conflict(self, mock_router, client):
        mock_router.patch(f"/workers/{WALLET}/reputation-network").mock(
            return_value=httpx.Response(
                409,
                json={"message": "Reputation network preference is not enabled yet"},
            )
        )
        from uvd_em_sdk import EMError

        with pytest.raises(EMError) as exc:
            await client.reputation.set_reputation_network(WALLET, "arbitrum")
        assert exc.value.status_code == 409

    async def test_cross_chain(self, mock_router, client):
        mock_router.get(f"/reputation/wallet/{WALLET}/cross-chain").mock(
            return_value=httpx.Response(
                200,
                json={
                    "wallet_address": WALLET,
                    "final_score": 88.0,
                    "chain_count": 2,
                    "total_reviews": 5,
                    "chains_with_identity": 3,
                    "chains_skipped": 1,
                    "per_chain": {
                        "base": {
                            "average": 90.0,
                            "review_count": 3,
                            "agent_ids": [2106],
                            "scores": [85, 92, 93],
                        },
                        "polygon": {
                            "average": 86.0,
                            "review_count": 2,
                            "agent_ids": [42],
                            "scores": [84, 88],
                        },
                    },
                    "cached": False,
                },
            )
        )
        agg = await client.reputation.cross_chain(WALLET)
        # Display is the cross-chain aggregate + per-chain breakdown, never a
        # single-chain score.
        assert agg.final_score == 88.0
        assert agg.chain_count == 2
        assert set(agg.per_chain.keys()) == {"base", "polygon"}
        assert agg.per_chain["base"]["review_count"] == 3

    async def test_cross_chain_no_evidence_is_none_not_zero(self, mock_router, client):
        """A wallet describe.net holds nothing on parses to ``None``.

        The model used to declare ``final_score: float = 0``, which did two
        bad things at once: a missing field became the WORST possible score,
        and an explicit ``null`` — what the endpoint now sends — raised a
        ValidationError. ``retrieved_at`` is what tells the caller we asked.
        """
        mock_router.get(f"/reputation/wallet/{WALLET}/cross-chain").mock(
            return_value=httpx.Response(
                200,
                json={
                    "wallet_address": WALLET,
                    "final_score": None,
                    "source": None,
                    "policy_version": None,
                    "refreshed_at": None,
                    "retrieved_at": "2026-08-29T04:17:08.132185+00:00",
                    "chain_count": 0,
                    "total_reviews": 0,
                    "chains_with_identity": 2,
                    "chains_skipped": 2,
                    "per_chain": {},
                    "per_chain_retrieved_at": "2026-08-29T04:17:08.132185+00:00",
                },
            )
        )
        agg = await client.reputation.cross_chain(WALLET)
        assert agg.final_score is None
        assert agg.source is None
        assert agg.retrieved_at == "2026-08-29T04:17:08.132185+00:00"
        # Two chains carry an identity and nobody rated them: counted, not
        # scored. Reading this as 0 would call an unrated wallet the worst on
        # the market.
        assert agg.chains_skipped == 2

    async def test_cross_chain_credits_describenet_and_carries_its_age(
        self, mock_router, client
    ):
        mock_router.get(f"/reputation/wallet/{WALLET}/cross-chain").mock(
            return_value=httpx.Response(
                200,
                json={
                    "wallet_address": WALLET,
                    "final_score": 86.637574,
                    "source": "describenet",
                    "policy_version": "equal-weight-per-chain@2",
                    "refreshed_at": "2026-08-29T03:50:02.436805+00:00",
                    "retrieved_at": "2026-08-29T04:17:08.132185+00:00",
                    "chain_count": 2,
                    "total_reviews": 195,
                    "chains_with_identity": 8,
                    "chains_skipped": 6,
                    "per_chain": {
                        "base": {
                            "agent_ids": ["18896", "58349"],
                            "average": 84.698225,
                            "review_count": 169,
                            "distinct_raters": 8,
                        }
                    },
                    "per_chain_retrieved_at": "2026-08-29T04:17:08.132185+00:00",
                },
            )
        )
        agg = await client.reputation.cross_chain(WALLET)
        assert agg.source == "describenet"
        # Provider freshness and OUR fetch time are different facts.
        assert agg.refreshed_at != agg.retrieved_at
        assert agg.policy_version == "equal-weight-per-chain@2"


# ---------------------------------------------------------------------------
# Rater-authored ratings (EIP-7702 relay)
# ---------------------------------------------------------------------------

PREPARED = {
    "digest": "0x" + "ab" * 32,
    "network": "base",
    "ratee_agent_id": 2106,
    "rater_wallet": "0x857fe6150401bFB4641Fe0D2B2621cc3B05543Cd",
    "deadline": 1787600000,
    "nonce": "0x" + "cd" * 32,
    "tag1": "buyer_rating:research",
    "tag2": "0x857fe615",
    "endpoint": "task:task-1",
    "feedback_uri": "https://execution.market/feedback/x.json",
    "feedback_hash": "0x" + "ef" * 32,
    "delegated": True,
}


class TestRelayedRating:
    """The rating carries the rater's name, and the SDK never holds their key."""

    async def test_prepare_sends_the_direction_and_score(self, mock_router, client):
        route = mock_router.post("/reputation/relay/prepare").mock(
            return_value=httpx.Response(200, json=PREPARED)
        )
        await client.reputation.prepare_relayed_rating(
            "task-1", "executor_rates_publisher", 95, comment="clear brief"
        )
        body = json.loads(route.calls.last.request.content)
        assert body["direction"] == "executor_rates_publisher"
        assert body["score"] == 95
        assert body["comment"] == "clear brief"

    async def test_submit_echoes_the_prepared_fields_verbatim(
        self, mock_router, client
    ):
        """Not redundancy: the Facilitator rebuilds the calldata from these.

        It requires the signature to cover exactly them and never relays
        calldata it was handed, so recomputing one here breaks verification with
        no error that names the cause.
        """
        route = mock_router.post("/reputation/relay/submit").mock(
            return_value=httpx.Response(
                200, json={"success": True, "authored_by": PREPARED["rater_wallet"]}
            )
        )
        await client.reputation.submit_relayed_rating(
            PREPARED,
            "0x" + "22" * 65,
            task_id="task-1",
            direction="publisher_rates_executor",
            score=95,
        )
        body = json.loads(route.calls.last.request.content)
        for field in (
            "network",
            "ratee_agent_id",
            "rater_wallet",
            "deadline",
            "nonce",
            "tag1",
            "tag2",
            "endpoint",
            "feedback_uri",
            "feedback_hash",
        ):
            assert body[field] == PREPARED[field], f"{field} was not echoed verbatim"

    async def test_authorization_travels_only_when_given(self, mock_router, client):
        route = mock_router.post("/reputation/relay/submit").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        await client.reputation.submit_relayed_rating(
            PREPARED,
            "0xsig",
            task_id="t",
            direction="publisher_rates_executor",
            score=80,
        )
        assert "authorization" not in json.loads(route.calls.last.request.content)

        auth = {
            "chainId": 8453,
            "address": "0xa7ca33CaE3c5890F25DfD08079DB82701C9deBc6",
            "nonce": 7,
            "yParity": 1,
            "r": "0x" + "aa" * 32,
            "s": "0x" + "bb" * 32,
        }
        await client.reputation.submit_relayed_rating(
            PREPARED,
            "0xsig",
            task_id="t",
            direction="publisher_rates_executor",
            score=80,
            authorization=auth,
        )
        assert json.loads(route.calls.last.request.content)["authorization"] == auth

    def test_the_sdk_never_takes_a_private_key(self):
        """Load-bearing. The point of this rail is that only the rater can sign.

        If a signing parameter ever shows up here, the SDK could author a rating
        on someone else's behalf — precisely the property we just removed from
        the Facilitator.
        """
        import inspect

        from uvd_em_sdk.resources.reputation import ReputationResource

        for name in ("prepare_relayed_rating", "submit_relayed_rating"):
            for param in inspect.signature(
                getattr(ReputationResource, name)
            ).parameters:
                assert "key" not in param.lower(), f"{name} accepts {param}"
                assert "secret" not in param.lower(), f"{name} accepts {param}"
