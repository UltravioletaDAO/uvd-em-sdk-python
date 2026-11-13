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

#: The v4 FeedbackDelegate on Base, written here by hand on purpose: the
#: parity test holds the table to the vendored copy, and a change of this
#: address has to show up in a review of this file too.
BASE_V4 = "0x260D3D0258680aA458D0EBB8BcAE8A2f68bf6163"

#: A throwaway key for the tests' rater: never funded, signs nothing real.
RATER_KEY = bytes.fromhex(
    "74057a8c1b82f5a9322eb37cacaba9d90687f106a8f323bf0a1cfe561f857fd8"
)
RATER = "0x9D3d65532e29f64210C0E0B66118a35828fF39B0"

#: A first rating on Base: the account is not delegated there yet.
FIRST_RATING = {
    **PREPARED,
    "rater_wallet": RATER,
    "delegated": False,
    "delegate": BASE_V4,
    "chain_id": 8453,
    "account_nonce": 7,
    "delegation_notice": (
        f"Sending this rating also delegates your account {RATER} (EIP-7702) "
        f"on base (chain 8453) to the FeedbackDelegate contract {BASE_V4}"
    ),
}

#: RATER's EIP-7702 authorization of (8453, BASE_V4, 7), as eth-account's
#: ``Account.sign_authorization`` produced it (0.14.0): a known answer from
#: an implementation that is not the SDK's.
AUTH = {
    "chainId": 8453,
    "address": BASE_V4,
    "nonce": 7,
    "yParity": 1,
    "r": "0x70a9d76dda57ce50bf602c84bfdc7f79cb4ef7bf841864c16560afb9427e6348",
    "s": "0x1053d60958ac54768a0c47da591be856b1f7c24a3953e3f0785dcc5ae805c336",
}


def _signed(chain_id, address, nonce=7, key=RATER_KEY, **fields):
    """An EIP-7702 authorization of ``(chain_id, address, nonce)`` signed with
    ``key``; ``fields`` overwrite what the dict SAYS, not what was signed.
    ``test_the_helper_signs_what_eth_account_signs`` ties it to AUTH."""
    import rlp
    from eth_keys import keys
    from eth_utils import keccak

    digest = keccak(
        bytes([5]) + rlp.encode([chain_id, bytes.fromhex(address[2:]), nonce])
    )
    signature = keys.PrivateKey(key).sign_msg_hash(digest)
    return {
        "chainId": chain_id,
        "address": address,
        "nonce": nonce,
        "yParity": signature.v,
        "r": hex(signature.r),
        "s": hex(signature.s),
        **fields,
    }


SIG = "0x" + "22" * 65

#: What submit sends for PREPARED with SIG: the caller's three fields, the
#: signature, and the prepared fields echoed verbatim. Nothing else of
#: ``prepared`` (digest, delegated, delegate, chain_id, account_nonce,
#: delegation_notice) travels.
ECHOED = {
    "task_id": "task-1",
    "direction": "publisher_rates_executor",
    "score": 95,
    "signature": SIG,
    "network": "base",
    "ratee_agent_id": 2106,
    "rater_wallet": PREPARED["rater_wallet"],
    "deadline": 1787600000,
    "nonce": "0x" + "cd" * 32,
    "tag1": "buyer_rating:research",
    "tag2": "0x857fe615",
    "endpoint": "task:task-1",
    "feedback_uri": "https://execution.market/feedback/x.json",
    "feedback_hash": "0x" + "ef" * 32,
}

#: The same body for FIRST_RATING, whose rater is RATER.
ECHOED_FIRST = {**ECHOED, "rater_wallet": RATER}

FOREIGN = "0x" + "66" * 20


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

        await client.reputation.submit_relayed_rating(
            FIRST_RATING,
            "0xsig",
            task_id="t",
            direction="publisher_rates_executor",
            score=80,
            authorization=AUTH,
        )
        assert json.loads(route.calls.last.request.content)["authorization"] == AUTH

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


# ---------------------------------------------------------------------------
# The EIP-7702 authorization: pinned delegate, and the owner said yes
# ---------------------------------------------------------------------------


def _submit_route(mock_router):
    return mock_router.post("/reputation/relay/submit").mock(
        return_value=httpx.Response(
            200, json={"success": True, "authored_by": PREPARED["rater_wallet"]}
        )
    )


async def _submit(client, prepared=FIRST_RATING, **kwargs):
    return await client.reputation.submit_relayed_rating(
        prepared,
        SIG,
        task_id="task-1",
        direction="publisher_rates_executor",
        score=95,
        **kwargs,
    )


class TestRelayedRatingDelegation:
    """Signing an EIP-7702 authorization hands the named contract the power to
    act as the account. The SDK lets one out only for the FeedbackDelegate it
    pins on that chain, and says the owner accepted only when told so."""

    @pytest.fixture
    def mock_router(self):
        # The refusals assert the route was NOT called; respx's default
        # teardown check (every route called) would contradict them.
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            yield router

    async def test_the_body_without_an_authorization_is_exact(
        self, mock_router, client
    ):
        route = _submit_route(mock_router)

        await _submit(client, PREPARED)

        assert json.loads(route.calls.last.request.content) == ECHOED

    async def test_the_body_with_an_acknowledged_authorization_is_exact(
        self, mock_router, client
    ):
        route = _submit_route(mock_router)

        await _submit(client, authorization=AUTH, delegation_acknowledged=True)

        assert json.loads(route.calls.last.request.content) == {
            **ECHOED_FIRST,
            "authorization": AUTH,
            "delegation_acknowledged": True,
        }

    async def test_an_authorization_alone_does_not_say_the_owner_accepted(
        self, mock_router, client
    ):
        """Default False: the field is left out, and EM decides (a backend that
        requires it answers 422; an older one ignores the field)."""
        route = _submit_route(mock_router)

        await _submit(client, authorization=AUTH)

        assert json.loads(route.calls.last.request.content) == {
            **ECHOED_FIRST,
            "authorization": AUTH,
        }

    @pytest.mark.parametrize(
        "prepared, sent",
        [(PREPARED, ECHOED), (FIRST_RATING, ECHOED_FIRST)],
        ids=["delegated", "first"],
    )
    async def test_delegation_acknowledged_never_travels_without_an_authorization(
        self, mock_router, client, prepared, sent
    ):
        route = _submit_route(mock_router)

        await _submit(client, prepared, delegation_acknowledged=True)

        assert json.loads(route.calls.last.request.content) == sent

    @pytest.mark.parametrize("said", [1, "true", "yes", [True]])
    async def test_only_true_itself_acknowledges(self, mock_router, client, said):
        """EM takes the JSON literal true (StrictBool): a truthy stand-in is
        not the owner's yes, and is not turned into one here."""
        route = _submit_route(mock_router)

        await _submit(client, authorization=AUTH, delegation_acknowledged=said)

        body = json.loads(route.calls.last.request.content)
        assert "delegation_acknowledged" not in body
        assert body["authorization"] == AUTH

    def test_delegation_acknowledged_is_keyword_only_and_false_by_default(self):
        import inspect

        from uvd_em_sdk.resources.reputation import ReputationResource

        param = inspect.signature(ReputationResource.submit_relayed_rating).parameters[
            "delegation_acknowledged"
        ]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is False

    @pytest.mark.parametrize(
        "change, code",
        [
            pytest.param({"delegate": FOREIGN}, "delegate_not_pinned", id="foreign"),
            pytest.param(
                # The v4 delegate of Ethereum, offered for a rating on Base.
                {"delegate": "0x9577F05D10D2052C75D115e3a0A8f9Dc0cBb7A7b"},
                "delegate_not_pinned",
                id="another-chains-delegate",
            ),
            pytest.param({"delegate": None}, "delegate_not_pinned", id="no-delegate"),
            pytest.param(
                {"delegate": "0X" + BASE_V4[2:]}, "delegate_not_pinned", id="0X-prefix"
            ),
            pytest.param(
                {"delegate": " " + BASE_V4}, "delegate_not_pinned", id="padded"
            ),
            pytest.param(
                {"delegate": BASE_V4[2:]}, "delegate_not_pinned", id="no-prefix"
            ),
            pytest.param({"chain_id": 0}, "chain_id_invalid", id="prepared-chain-0"),
            pytest.param(
                {"chain_id": None}, "chain_id_invalid", id="prepared-chain-none"
            ),
            pytest.param({"chain_id": -8453}, "chain_id_invalid", id="negative"),
            pytest.param({"chain_id": "8453"}, "chain_id_invalid", id="a-string"),
            pytest.param({"chain_id": 8453.0}, "chain_id_invalid", id="a-float"),
            pytest.param({"chain_id": 43114}, "chain_not_pinned", id="avalanche"),
            pytest.param(
                {"network": "ethereum"},
                "network_mismatch",
                id="network-of-another-chain",
            ),
        ],
    )
    async def test_a_prepare_the_sdk_does_not_pin_sends_nothing(
        self, mock_router, client, change, code
    ):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(
                client,
                {**FIRST_RATING, **change},
                authorization=AUTH,
                delegation_acknowledged=True,
            )

        assert exc.value.code == code
        assert not route.called

    async def test_a_prepare_without_a_chain_id_sends_nothing(
        self, mock_router, client
    ):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)
        prepared = {k: v for k, v in FIRST_RATING.items() if k != "chain_id"}

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, prepared, authorization=AUTH)

        assert exc.value.code == "chain_id_invalid"
        assert not route.called

    async def test_true_is_not_chain_1(self, mock_router, client):
        """``True == 1`` in Python, and 1 is Ethereum: neither the prepare nor
        the authorization may pass a bool for a chain id."""
        from uvd_em_sdk import EMDelegateNotPinnedError

        ethereum = FEEDBACK_DELEGATES_BY_NAME()["ethereum"]
        route = _submit_route(mock_router)
        prepared = {
            **FIRST_RATING,
            "network": "ethereum",
            "chain_id": 1,
            "delegate": ethereum.address,
        }
        auth = _signed(1, ethereum.address)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, {**prepared, "chain_id": True}, authorization=auth)
        assert exc.value.code == "chain_id_invalid"

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, prepared, authorization={**auth, "chainId": True})
        assert exc.value.code == "authorization_wrong_chain"
        assert not route.called

        await _submit(client, prepared, authorization=auth)  # the real one goes
        assert route.call_count == 1

    @pytest.mark.parametrize(
        "change, code",
        [
            pytest.param({"chainId": 0}, "authorization_wrong_chain", id="chainId-0"),
            pytest.param(
                {"chainId": 1}, "authorization_wrong_chain", id="another-chain"
            ),
            pytest.param(
                {"chainId": "8453"}, "authorization_wrong_chain", id="a-string"
            ),
            pytest.param(
                {"chainId": 8453.0}, "authorization_wrong_chain", id="a-float"
            ),
            pytest.param({"chainId": None}, "authorization_wrong_chain", id="none"),
            pytest.param(
                {"address": FOREIGN}, "authorization_wrong_delegate", id="foreign"
            ),
            pytest.param(
                {"address": "0X" + BASE_V4[2:]},
                "authorization_wrong_delegate",
                id="0X-prefix",
            ),
            pytest.param(
                {"address": BASE_V4 + "\n"},
                "authorization_wrong_delegate",
                id="trailing-newline",
            ),
            pytest.param(
                {"address": None}, "authorization_wrong_delegate", id="no-address"
            ),
        ],
    )
    async def test_an_authorization_for_another_chain_or_contract_sends_nothing(
        self, mock_router, client, change, code
    ):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(
                client, authorization={**AUTH, **change}, delegation_acknowledged=True
            )

        assert exc.value.code == code
        assert exc.value.status_code is None  # nothing reached the server
        assert not route.called

    async def test_an_authorization_without_chain_id_sends_nothing(
        self, mock_router, client
    ):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)
        auth = {k: v for k, v in AUTH.items() if k != "chainId"}

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, authorization=auth, delegation_acknowledged=True)

        assert exc.value.code == "authorization_wrong_chain"
        assert not route.called

    @pytest.mark.parametrize("authorization", [[("chainId", 8453)], "0xsigned"])
    async def test_an_authorization_that_is_not_a_mapping_sends_nothing(
        self, mock_router, client, authorization
    ):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, authorization=authorization)

        assert exc.value.code == "authorization_invalid"
        assert not route.called

    @pytest.mark.parametrize(
        "spelling",
        [BASE_V4.lower(), "0x" + BASE_V4[2:].upper()],
        ids=["lower", "upper"],
    )
    async def test_the_address_is_compared_without_case(
        self, mock_router, client, spelling
    ):
        route = _submit_route(mock_router)

        await _submit(
            client,
            {**FIRST_RATING, "delegate": spelling},
            authorization={**AUTH, "address": spelling},
            delegation_acknowledged=True,
        )

        body = json.loads(route.calls.last.request.content)
        assert body["authorization"]["address"] == spelling  # sent as signed
        assert body["delegation_acknowledged"] is True

    async def test_without_an_authorization_nothing_is_checked(
        self, mock_router, client
    ):
        """An account already delegated (every rating after the first on a
        chain) sends no authorization: there is no delegation to check, and EM
        does not touch such a submit either."""
        route = _submit_route(mock_router)

        await _submit(client, {**PREPARED, "delegate": FOREIGN, "chain_id": 0})

        assert route.called

    async def test_ems_422_is_raised_with_its_code(self, mock_router, client):
        """EM refuses an authorization the owner did not accept. Nothing was
        sent on-chain; the code says what to do."""
        from uvd_em_sdk import EMValidationError

        mock_router.post("/reputation/relay/submit").mock(
            return_value=httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "relay_delegation_not_acknowledged",
                        "retryable": False,
                        "message": "Show `delegation_notice` to the owner.",
                        "network": "base",
                        "chain_id": 8453,
                        "delegate": BASE_V4,
                        "next_step": (
                            "POST /api/v1/reputation/relay/submit with this same "
                            "body plus delegation_acknowledged: true"
                        ),
                    }
                },
            )
        )

        with pytest.raises(EMValidationError) as exc:
            await _submit(client, authorization=AUTH)

        assert exc.value.status_code == 422
        assert exc.value.details["detail"]["code"] == (
            "relay_delegation_not_acknowledged"
        )
        assert exc.value.details["detail"]["delegate"] == BASE_V4


def FEEDBACK_DELEGATES_BY_NAME():
    from uvd_em_sdk import FEEDBACK_DELEGATES

    return FEEDBACK_DELEGATES


class TestVerifyFeedbackDelegate:
    """The check a caller runs BEFORE signing the authorization."""

    def test_base_pins_the_v4_delegate(self):
        from uvd_em_sdk import FeedbackDelegate, get_feedback_delegate

        assert get_feedback_delegate(8453) == FeedbackDelegate("base", 8453, BASE_V4)
        assert get_feedback_delegate(True) is None  # not Ethereum
        assert get_feedback_delegate(0) is None

    def test_it_returns_what_to_sign_over(self):
        from uvd_em_sdk import verify_feedback_delegate

        pinned = verify_feedback_delegate(FIRST_RATING)

        assert (pinned.chain_id, pinned.address) == (8453, BASE_V4)

    @pytest.mark.parametrize("network", list(FEEDBACK_DELEGATES_BY_NAME()))
    def test_every_pinned_network_verifies(self, network):
        from uvd_em_sdk import verify_feedback_delegate

        d = FEEDBACK_DELEGATES_BY_NAME()[network]
        prepared = {
            "network": network,
            "chain_id": d.chain_id,
            "delegate": d.address,
            "rater_wallet": RATER,
        }

        assert verify_feedback_delegate(prepared, _signed(d.chain_id, d.address)) == d

    def test_the_comparison_is_by_chain_and_address(self):
        """Optimism and Monad pin the same address: it verifies on both.
        Base's address does not verify on Optimism."""
        from uvd_em_sdk import EMDelegateNotPinnedError, verify_feedback_delegate

        table = FEEDBACK_DELEGATES_BY_NAME()
        assert table["optimism"].address == table["monad"].address
        for network in ("optimism", "monad"):
            d = table[network]
            prepared = {
                "network": network,
                "chain_id": d.chain_id,
                "delegate": d.address,
            }
            assert verify_feedback_delegate(prepared) == d

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            verify_feedback_delegate(
                {"network": "optimism", "chain_id": 10, "delegate": BASE_V4}
            )
        assert exc.value.code == "delegate_not_pinned"

    def test_the_network_is_checked_only_when_present(self):
        from uvd_em_sdk import verify_feedback_delegate

        assert verify_feedback_delegate({"chain_id": 8453, "delegate": BASE_V4})

    def test_the_refusal_is_an_em_error_that_names_what_it_compared(self):
        from uvd_em_sdk import (
            EMDelegateNotPinnedError,
            EMError,
            verify_feedback_delegate,
        )

        with pytest.raises(EMError) as exc:
            verify_feedback_delegate(FIRST_RATING, {**AUTH, "address": FOREIGN})

        assert isinstance(exc.value, EMDelegateNotPinnedError)
        assert exc.value.code == "authorization_wrong_delegate"
        assert exc.value.details == {
            "chain_id": 8453,
            "network": "base",
            "authorization_address": FOREIGN,
            "pinned_delegate": BASE_V4,
        }
        assert BASE_V4 in exc.value.message and FOREIGN in exc.value.message


class TestAuthorizationSigner:
    """The dict says chainId, address and nonce; the signature says what was
    signed. A wallet that signed chainId 0 under a dict that says 8453 would
    hand whoever reads the body a delegation valid on every chain."""

    @pytest.fixture
    def mock_router(self):
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            yield router

    def test_the_known_answer_recovers_to_the_rater(self):
        from uvd_em_sdk import verify_feedback_delegate

        assert verify_feedback_delegate(FIRST_RATING, AUTH).address == BASE_V4

    def test_the_helper_signs_what_eth_account_signs(self):
        """``_signed`` builds the EIP-7702 preimage the same way the SDK does;
        AUTH comes from eth-account. Same key, same tuple: same signature."""
        signed = _signed(8453, BASE_V4, 7)
        for field in ("yParity", "r", "s"):
            assert int(str(signed[field]), 0) == int(str(AUTH[field]), 0), field

    @pytest.mark.parametrize(
        "auth",
        [
            # The refuter's case: the wallet signed chainId 0 (every chain),
            # and the dict says this chain.
            pytest.param(_signed(0, BASE_V4, chainId=8453), id="signed-for-chain-0"),
            pytest.param(_signed(1, BASE_V4, chainId=8453), id="signed-for-chain-1"),
            pytest.param(
                {**_signed(8453, FOREIGN), "address": BASE_V4}, id="signed-foreign"
            ),
            pytest.param({**_signed(8453, BASE_V4, nonce=8), "nonce": 7}, id="nonce"),
            pytest.param(
                _signed(8453, BASE_V4, key=bytes.fromhex("22" * 32)), id="another-key"
            ),
            pytest.param({**AUTH, "yParity": 1 - AUTH["yParity"]}, id="flipped-parity"),
            pytest.param({**AUTH, "yParity": 2}, id="parity-2"),
            pytest.param({**AUTH, "yParity": True}, id="parity-bool"),
            pytest.param({**AUTH, "r": "nope"}, id="r-not-hex"),
            pytest.param({**AUTH, "s": None}, id="s-none"),
            pytest.param({**AUTH, "r": "0x0"}, id="r-zero"),
            pytest.param({**AUTH, "s": "0x" + "ff" * 32}, id="s-out-of-range"),
            pytest.param({**AUTH, "nonce": -1}, id="nonce-negative"),
            pytest.param({**AUTH, "nonce": 2**64}, id="nonce-over-uint64"),
            pytest.param({**AUTH, "nonce": "7"}, id="nonce-decimal-string"),
            pytest.param(
                {k: v for k, v in AUTH.items() if k not in ("r", "s")}, id="unsigned"
            ),
        ],
    )
    async def test_an_authorization_the_rater_did_not_sign_sends_nothing(
        self, mock_router, client, auth
    ):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, authorization=auth, delegation_acknowledged=True)

        assert exc.value.code == "authorization_wrong_signer"
        assert not route.called

    @pytest.mark.parametrize(
        "rater", [None, FOREIGN, RATER[2:], "0X" + RATER[2:]], ids=str
    )
    def test_the_signer_must_be_the_prepared_rater(self, rater):
        from uvd_em_sdk import EMDelegateNotPinnedError, verify_feedback_delegate

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            verify_feedback_delegate({**FIRST_RATING, "rater_wallet": rater}, AUTH)

        assert exc.value.code == "authorization_wrong_signer"

    def test_the_rater_is_compared_without_case(self):
        from uvd_em_sdk import verify_feedback_delegate

        for rater in (RATER.lower(), "0x" + RATER[2:].upper()):
            assert verify_feedback_delegate(
                {**FIRST_RATING, "rater_wallet": rater}, AUTH
            )

    def test_hex_and_int_spellings_of_the_signature_verify(self):
        from uvd_em_sdk import verify_feedback_delegate

        as_ints = {**AUTH, "r": int(AUTH["r"], 16), "s": int(AUTH["s"], 16)}
        as_hex_parity = {**AUTH, "yParity": hex(AUTH["yParity"])}
        assert verify_feedback_delegate(FIRST_RATING, as_ints)
        assert verify_feedback_delegate(FIRST_RATING, as_hex_parity)

    async def test_snake_case_keys_are_read_as_execution_market_reads_them(
        self, mock_router, client
    ):
        """EM's model takes ``chain_id`` and ``y_parity`` too (populate_by_name),
        and so does what ``model_dump()`` of it writes. 0.10.0 sent such a dict
        and EM accepted it: it is checked, not refused for its key names."""
        route = _submit_route(mock_router)
        snake = {
            "chain_id": AUTH["chainId"],
            "address": AUTH["address"],
            "nonce": AUTH["nonce"],
            "y_parity": AUTH["yParity"],
            "r": AUTH["r"],
            "s": AUTH["s"],
        }

        await _submit(client, authorization=snake, delegation_acknowledged=True)

        assert json.loads(route.calls.last.request.content)["authorization"] == snake

    async def test_camel_case_wins_over_snake_case(self, mock_router, client):
        """Both present: EM reads ``chainId``, and so does the check."""
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(
                client, authorization={**AUTH, "chainId": 0, "chain_id": 8453}
            )

        assert exc.value.code == "authorization_wrong_chain"
        assert not route.called
        await _submit(client, authorization={**AUTH, "chain_id": 0})  # chainId: 8453
        assert route.called

    async def test_what_is_checked_is_what_is_sent(self, mock_router, client):
        """A mapping whose ``get`` disagrees with what JSON writes of it is
        checked, and sent, as JSON writes it."""
        from uvd_em_sdk import EMDelegateNotPinnedError

        class Lying(dict):
            def get(self, key, default=None):
                return AUTH.get(key, default)

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, authorization=Lying({**AUTH, "chainId": 0}))

        assert exc.value.code == "authorization_wrong_chain"
        assert not route.called

    async def test_a_read_only_mapping_is_sent_as_a_plain_object(
        self, mock_router, client
    ):
        from types import MappingProxyType

        route = _submit_route(mock_router)

        await _submit(client, authorization=MappingProxyType(dict(AUTH)))

        assert json.loads(route.calls.last.request.content)["authorization"] == AUTH

    async def test_a_value_json_cannot_carry_sends_nothing(self, mock_router, client):
        from uvd_em_sdk import EMDelegateNotPinnedError

        route = _submit_route(mock_router)

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            await _submit(client, authorization={**AUTH, "extra": object()})

        assert exc.value.code == "authorization_invalid"
        assert not route.called

    @pytest.mark.parametrize("prepared", [None, [], "prepared"], ids=str)
    def test_a_prepared_that_is_not_a_mapping_is_refused_typed(self, prepared):
        from uvd_em_sdk import EMDelegateNotPinnedError, verify_feedback_delegate

        with pytest.raises(EMDelegateNotPinnedError) as exc:
            verify_feedback_delegate(prepared, AUTH)

        assert exc.value.code == "prepared_invalid"
