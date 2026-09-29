"""ERC-8128 (RFC 9421) request signing — signer unit tests + client wiring.

The signing key is a THROWAWAY test key generated in-test with
``eth_account.Account.create()`` — never a real wallet, never printed.
Determinism comes from rebuilding the expected signature base independently
and (a) re-signing it with the same key (RFC 6979 ECDSA is deterministic),
(b) recovering the signer address from the header signature.
"""

import base64
import json
import re
import types
from pathlib import Path

import httpx
import pytest
import respx
from eth_account import Account
from eth_account.messages import encode_defunct
from uvd_x402_sdk.wallet import EnvKeyAdapter

import uvd_em_sdk.erc8128 as erc8128_mod
from uvd_em_sdk import EMClient
from uvd_em_sdk.erc8128 import sign_request

BASE = "https://api.execution.market/api/v1"
FIXED_NOW = 1760000000

# RFC 9530 §Section B.1 known vector: sha-256 of '{"hello": "world"}'
RFC9530_DIGEST = "sha-256=:X48E9qOokqqrvdts8nOJRJN3OWDUoyWxBf7kbu9DBPE=:"


@pytest.fixture
def account():
    return Account.create()


@pytest.fixture
def wallet(account):
    key_hex = account.key.hex()
    return EnvKeyAdapter(private_key=key_hex)


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr(
        erc8128_mod, "time", types.SimpleNamespace(time=lambda: FIXED_NOW)
    )
    return FIXED_NOW


def _decode_signature(headers: dict[str, str]) -> bytes:
    m = re.fullmatch(r"eth=:(?P<b64>[A-Za-z0-9+/=]+):", headers["Signature"])
    assert m, headers["Signature"]
    return base64.b64decode(m.group("b64"))


# ---------------------------------------------------------------------------
# Signer unit tests
# ---------------------------------------------------------------------------


class TestSignRequest:
    def test_content_digest_rfc9530_vector(self, wallet):
        headers = sign_request(
            wallet,
            method="POST",
            url="https://api.execution.market/api/v1/tasks",
            body='{"hello": "world"}',
            nonce="n1",
        )
        assert headers["Content-Digest"] == RFC9530_DIGEST

    def test_known_vector_signature_base(self, wallet, account, frozen_time):
        """Deterministic fixed-request vector: exact Signature-Input string,
        signature over the independently rebuilt RFC 9421 base, and ECDSA
        recovery back to the signer address."""
        body = '{"hello": "world"}'
        headers = sign_request(
            wallet,
            method="POST",
            url="https://api.execution.market/api/v1/tasks",
            body=body,
            nonce="test-nonce",
        )

        address = account.address.lower()
        expected_params = (
            '("@method" "@authority" "@path" "content-digest")'
            f";created={FIXED_NOW};expires={FIXED_NOW + 300}"
            f';nonce="test-nonce";keyid="erc8128:8453:{address}";alg="eip191"'
        )
        assert headers["Signature-Input"] == f"eth={expected_params}"

        expected_base = "\n".join(
            [
                '"@method": POST',
                '"@authority": api.execution.market',
                '"@path": /api/v1/tasks',
                f'"content-digest": {RFC9530_DIGEST}',
                f'"@signature-params": {expected_params}',
            ]
        )
        sig = _decode_signature(headers)

        # (a) deterministic re-sign of the rebuilt base matches byte-for-byte
        expected_sig = account.sign_message(encode_defunct(text=expected_base))
        assert sig == expected_sig.signature

        # (b) EIP-191 recovery of the header signature yields the signer
        recovered = Account.recover_message(
            encode_defunct(text=expected_base), signature=sig
        )
        assert recovered.lower() == address

    def test_query_component_covered(self, wallet):
        headers = sign_request(
            wallet,
            method="GET",
            url="https://api.execution.market/api/v1/tasks?status=published&limit=5",
            nonce="n1",
        )
        assert '"@query"' in headers["Signature-Input"]
        assert "Content-Digest" not in headers

    def test_no_body_no_content_digest(self, wallet):
        headers = sign_request(
            wallet,
            method="POST",
            url="https://api.execution.market/api/v1/tasks/abc/cancel",
            nonce="n1",
        )
        assert "Content-Digest" not in headers
        assert '"content-digest"' not in headers["Signature-Input"]


# ---------------------------------------------------------------------------
# F3-1/F3-2 golden-vector conformance — byte-equality against the canonical
# wire format pinned in shared/test-vectors/erc8128.json. This module is the
# INTERIM CANONICAL signer of the fleet: any drift here fails first.
# ---------------------------------------------------------------------------

_VECTORS_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "test-vectors" / "erc8128.json"
)


@pytest.fixture
def golden_vectors():
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not _VECTORS_PATH.exists(),
    reason="shared/test-vectors/erc8128.json only exists in the monorepo checkout",
)
class TestGoldenVectorConformance:
    @pytest.mark.parametrize("request_name", ["get_query", "post_body"])
    def test_headers_match_canonical_vector(
        self, golden_vectors, request_name, monkeypatch
    ):
        fixture_data = golden_vectors
        frozen = fixture_data["frozen"]
        # Key stored 0x-less so the secret scanner never sees 0x + 64 hex.
        wallet = EnvKeyAdapter(private_key="0x" + frozen["private_key"])
        monkeypatch.setattr(
            erc8128_mod,
            "time",
            types.SimpleNamespace(time=lambda: float(frozen["created"])),
        )
        spec = fixture_data["requests"][request_name]
        headers = sign_request(
            wallet,
            method=spec["method"],
            url=spec["url"],
            body=spec["body"],
            nonce=frozen["nonce"],
            chain_id=frozen["chain_id"],
        )
        expected = fixture_data["vectors"]["canonical"][request_name]["headers"]
        assert headers == expected


# ---------------------------------------------------------------------------
# Client wiring — automatic signing with WalletAdapter
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


def _rebuild_base_from_request(request: httpx.Request) -> str:
    """Rebuild the RFC 9421 signature base from the request as sent."""
    sig_input = request.headers["signature-input"]
    params = sig_input.removeprefix("eth=")
    covered = re.findall(r'"([^"]+)"', params.split(")")[0])
    lines = []
    for component in covered:
        if component == "@method":
            lines.append(f'"@method": {request.method}')
        elif component == "@authority":
            lines.append(f'"@authority": {request.url.netloc.decode()}')
        elif component == "@path":
            lines.append(f'"@path": {request.url.path}')
        elif component == "@query":
            lines.append(f'"@query": ?{request.url.query.decode()}')
        elif component == "content-digest":
            lines.append(f'"content-digest": {request.headers["content-digest"]}')
    lines.append(f'"@signature-params": {params}')
    return "\n".join(lines)


class TestClientSigning:
    async def test_write_is_signed_and_recoverable(self, mock_router, wallet, account):
        nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
            return_value=httpx.Response(
                200, json={"nonce": "nonce-1", "ttl_seconds": 300}
            )
        )
        post_route = mock_router.post("/tasks/abc-123/cancel").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        async with EMClient(wallet=wallet) as client:
            await client.tasks.cancel("abc-123", reason="done")

        assert nonce_route.call_count == 1
        req = post_route.calls.last.request
        assert 'nonce="nonce-1"' in req.headers["signature-input"]
        assert (
            f'keyid="erc8128:8453:{account.address.lower()}"'
            in req.headers["signature-input"]
        )
        assert "content-digest" in req.headers

        # Signature verifies against the request exactly as sent
        base = _rebuild_base_from_request(req)
        sig = base64.b64decode(
            req.headers["signature"].removeprefix("eth=:").removesuffix(":")
        )
        recovered = Account.recover_message(encode_defunct(text=base), signature=sig)
        assert recovered.lower() == account.address.lower()

    async def test_get_not_signed_by_default(self, mock_router, wallet):
        # No nonce route registered: a nonce fetch would fail the test loudly.
        mock_router.get("/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        async with EMClient(wallet=wallet) as client:
            await client.health()
        req = mock_router.calls.last.request
        assert "signature" not in req.headers

    async def test_get_signed_when_forced(self, mock_router, wallet):
        mock_router.get("/auth/erc8128/nonce").mock(
            return_value=httpx.Response(200, json={"nonce": "nonce-1"})
        )
        route = mock_router.get("/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        async with EMClient(wallet=wallet) as client:
            await client._request("GET", "/health", sign=True)
        assert "signature" in route.calls.last.request.headers

    async def test_submissions_list_is_signed(self, mock_router, wallet):
        """``GET /tasks/{id}/submissions`` is owner-scoped (verify_agent_auth_read),
        so submissions.list must force the ERC-8128 signature (sign=True) that
        GETs skip by default — otherwise the server answers 401/403."""
        nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
            return_value=httpx.Response(200, json={"nonce": "nonce-1"})
        )
        list_route = mock_router.get("/tasks/abc-123/submissions").mock(
            return_value=httpx.Response(200, json={"submissions": [], "count": 0})
        )
        async with EMClient(wallet=wallet) as client:
            result = await client.submissions.list("abc-123")
        assert result.count == 0
        assert nonce_route.call_count == 1
        assert "signature" in list_route.calls.last.request.headers

    async def test_my_submission_is_signed(self, mock_router, wallet):
        """``GET /workers/tasks/{id}/my-submission`` is worker-scoped: an
        agent-as-worker authenticates via ERC-8128, so my_submission must force
        the signature GETs skip by default (sign=True) — else 401 (BACK-23)."""
        nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
            return_value=httpx.Response(200, json={"nonce": "nonce-1"})
        )
        route = mock_router.get("/workers/tasks/task-1/my-submission").mock(
            return_value=httpx.Response(200, json={"success": True, "data": {}})
        )
        async with EMClient(wallet=wallet) as client:
            await client.workers.my_submission("task-1", "exec-1")
        assert nonce_route.call_count == 1
        assert "signature" in route.calls.last.request.headers

    async def test_submission_detail_is_signed(self, mock_router, wallet):
        """``GET /submissions/{id}`` — same worker-scoped rule: an agent
        executor has no Supabase JWT, so submissions.get must force the
        ERC-8128 signature (sign=True) — else 401 (BACK-23 follow-up)."""
        nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
            return_value=httpx.Response(200, json={"nonce": "nonce-1"})
        )
        route = mock_router.get("/submissions/sub-1").mock(
            return_value=httpx.Response(200, json={"success": True, "data": {}})
        )
        async with EMClient(wallet=wallet) as client:
            await client.submissions.get("sub-1")
        assert nonce_route.call_count == 1
        assert "signature" in route.calls.last.request.headers

    async def test_geo_reference_is_signed(self, mock_router, wallet):
        """``GET /workers/tasks/{id}/geo-reference`` — same worker-scoped rule."""
        nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
            return_value=httpx.Response(200, json={"nonce": "nonce-1"})
        )
        route = mock_router.get("/workers/tasks/task-1/geo-reference").mock(
            return_value=httpx.Response(200, json={"success": True, "data": {}})
        )
        async with EMClient(wallet=wallet) as client:
            await client.workers.geo_reference("task-1", "exec-1")
        assert nonce_route.call_count == 1
        assert "signature" in route.calls.last.request.headers

    async def test_retry_resigns_with_fresh_nonce(self, mock_router, wallet):
        """The server consumes the nonce before verification — every retry
        attempt must fetch a fresh nonce and re-sign."""
        nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
            side_effect=[
                httpx.Response(200, json={"nonce": "nonce-1"}),
                httpx.Response(200, json={"nonce": "nonce-2"}),
            ]
        )
        post_route = mock_router.post("/tasks/abc-123/cancel").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(200, json={"success": True}),
            ]
        )
        async with EMClient(wallet=wallet, max_retries=2) as client:
            result = await client.tasks.cancel("abc-123")
        assert result["success"] is True
        assert nonce_route.call_count == 2
        first, second = post_route.calls
        assert 'nonce="nonce-1"' in first.request.headers["signature-input"]
        assert 'nonce="nonce-2"' in second.request.headers["signature-input"]

    async def test_api_key_takes_precedence_over_wallet(self, mock_router, wallet):
        """Explicit API key = internal-testing fallback: Bearer, no signing.

        No nonce route registered: a nonce fetch would fail the test loudly.
        """
        route = mock_router.post("/tasks/abc-123/cancel").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        async with EMClient(api_key="em_test", wallet=wallet) as client:
            await client.tasks.cancel("abc-123")
        req = route.calls.last.request
        assert req.headers["authorization"] == "Bearer em_test"
        assert "signature" not in req.headers
