"""Equivalence suite for the single retry policy (F3-4).

ONE decision table (status/exception -> retry yes/no) run against the three
client-side implementations:

  1. ``uvd_em_sdk.retry.request_with_retry``  (canonical)
  2. ``execution_market._signer.with_backoff``   (sdk/python, zero-dep)
  3. ``em_cli.api.EMAPIClient._request_with_retry`` (CLI)

plus explicit tests of the tx-hash double-settle guard (invariant 2) and the
Idempotency-Key persistence across attempts. The backend policy in
``mcp_server/integrations/_http_retry.py`` stays as the reference — its own
suite lives in ``mcp_server/tests/test_facilitator_retries.py``.

Like ``test_networks_sync.py``, the cross-package legs run wherever the repo
checkout includes ``sdk/python`` and ``cli/src`` (CI does); they are skipped
for a standalone SDK install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from uvd_em_sdk.retry import request_with_retry

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_PY = REPO_ROOT / "sdk" / "python"
CLI_SRC = REPO_ROOT / "cli" / "src"
_HAS_SIBLINGS = SDK_PY.is_dir() and CLI_SRC.is_dir()

if _HAS_SIBLINGS:
    for _p in (str(SDK_PY), str(CLI_SRC)):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    from execution_market._signer import with_backoff
    from em_cli.api import APIError, EMAPIClient

requires_siblings = pytest.mark.skipif(
    not _HAS_SIBLINGS,
    reason="sdk/python or cli/src not present (standalone SDK install)",
)

# ---------------------------------------------------------------------------
# THE decision table — every implementation must agree with it.
# ---------------------------------------------------------------------------

STATUS_TABLE = [
    # (status, should_retry)
    (400, False),
    (401, False),
    (403, False),
    (404, False),
    (409, False),
    (422, False),
    (429, True),
    (500, True),
    (502, True),
    (503, True),
    (504, True),
]

EXCEPTION_TABLE = [
    # (exception type, should_retry)
    (httpx.ConnectError, True),
    (httpx.ReadTimeout, True),
]

TX_HASH_BODIES = [
    {"transaction": {"hash": "0xabc"}},
    {"transaction": "0xabc"},
    {"txHash": "0xabc"},
    {"tx_hash": "0xabc"},
    {"transaction_hash": "0xabc"},
]


def _status_error(status: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://t.local/api/v1/x")
    response = httpx.Response(
        status,
        json=body if body is not None else {"detail": "boom"},
        request=request,
    )
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


# ---------------------------------------------------------------------------
# 1. Canonical — uvd_em_sdk.retry.request_with_retry
# ---------------------------------------------------------------------------


def _counting_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://t.local"
    )


@pytest.mark.parametrize("status,should_retry", STATUS_TABLE)
async def test_plugin_sdk_status_table(status: int, should_retry: bool):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"detail": "boom"})

    async with _counting_client(handler) as client:
        resp = await request_with_retry(
            client, "GET", "/x", max_retries=1, backoff_factor=0
        )
    assert resp.status_code == status
    assert calls == (2 if should_retry else 1)


@pytest.mark.parametrize("exc_type,should_retry", EXCEPTION_TABLE)
async def test_plugin_sdk_exception_table(exc_type, should_retry: bool):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise exc_type("boom")

    async with _counting_client(handler) as client:
        with pytest.raises(exc_type):
            await request_with_retry(
                client, "GET", "/x", max_retries=1, backoff_factor=0
            )
    assert calls == (2 if should_retry else 1)


@pytest.mark.parametrize("body", TX_HASH_BODIES)
async def test_plugin_sdk_tx_hash_guard(body: dict):
    """5xx WITH a tx hash in the body is returned as-is — never retried."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json=body)

    async with _counting_client(handler) as client:
        resp = await request_with_retry(
            client, "POST", "/x", max_retries=2, backoff_factor=0
        )
    assert resp.status_code == 502
    assert calls == 1, "response with tx hash must not be retried (double-settle)"


async def test_plugin_sdk_5xx_without_tx_hash_still_retries():
    """Control: same 502 but no tx hash in the body — retry proceeds."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json={"success": False, "transaction": {}})

    async with _counting_client(handler) as client:
        resp = await request_with_retry(
            client, "POST", "/x", max_retries=1, backoff_factor=0
        )
    assert resp.status_code == 502
    assert calls == 2


async def test_plugin_sdk_idempotency_key_persists_signature_regenerates():
    """Static headers (X-Idempotency-Key) are re-sent unchanged on every
    attempt so the server can dedupe the retried write; headers_factory
    output (single-use ERC-8128 signature) is regenerated per attempt."""
    seen_keys: list[str | None] = []
    seen_sigs: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get("X-Idempotency-Key"))
        seen_sigs.append(request.headers.get("Signature"))
        return httpx.Response(503, json={"detail": "flaky"})

    n = 0

    async def factory() -> dict[str, str]:
        nonlocal n
        n += 1
        return {"Signature": f"sig-{n}"}

    async with _counting_client(handler) as client:
        await request_with_retry(
            client,
            "POST",
            "/x",
            max_retries=1,
            backoff_factor=0,
            headers={"X-Idempotency-Key": "k-1"},
            headers_factory=factory,
        )
    assert seen_keys == ["k-1", "k-1"]
    assert seen_sigs == ["sig-1", "sig-2"]


# ---------------------------------------------------------------------------
# 2. sdk/python — execution_market._signer.with_backoff (zero-dep, in-place fix)
# ---------------------------------------------------------------------------


@requires_siblings
@pytest.mark.parametrize("status,should_retry", STATUS_TABLE)
async def test_sdk_python_with_backoff_status_table(status: int, should_retry: bool):
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _status_error(status)

    with pytest.raises(httpx.HTTPStatusError):
        await with_backoff(fn, tries=2, base=0)
    assert calls == (2 if should_retry else 1)


@requires_siblings
@pytest.mark.parametrize("exc_type,should_retry", EXCEPTION_TABLE)
async def test_sdk_python_with_backoff_exception_table(exc_type, should_retry: bool):
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise exc_type("boom")

    with pytest.raises(exc_type):
        await with_backoff(fn, tries=2, base=0)
    assert calls == (2 if should_retry else 1)


# ---------------------------------------------------------------------------
# 3. CLI — em_cli.api.EMAPIClient._request_with_retry
# ---------------------------------------------------------------------------


class _StubOws:
    """Stands in for OwsEM8128Client: raises what the test dictates."""

    def __init__(self, exc_factory):
        self.calls = 0
        self._exc_factory = exc_factory

    async def get(self, path):
        self.calls += 1
        raise self._exc_factory()

    async def post(self, path, data=None, extra_headers=None):
        self.calls += 1
        raise self._exc_factory()


def _cli_client(exc_factory, max_retries: int = 1):
    client = EMAPIClient(
        wallet_name="test-wallet",
        wallet_address="0x" + "ab" * 20,
        chain_id=8453,
        base_url="https://t.local",
        max_retries=max_retries,
    )
    client.RETRY_DELAY = 0.0  # instance attr shadows the class constant
    stub = _StubOws(exc_factory)
    client._ows = stub
    return client, stub


@requires_siblings
@pytest.mark.parametrize("status,should_retry", STATUS_TABLE)
def test_cli_status_table(status: int, should_retry: bool):
    client, stub = _cli_client(lambda: _status_error(status))
    with pytest.raises(APIError) as excinfo:
        client._request_with_retry("GET", "/api/v1/x")
    assert excinfo.value.status_code == status
    assert stub.calls == (2 if should_retry else 1)


@requires_siblings
@pytest.mark.parametrize("exc_type,should_retry", EXCEPTION_TABLE)
def test_cli_exception_table(exc_type, should_retry: bool):
    client, stub = _cli_client(lambda: exc_type("boom"))
    with pytest.raises(APIError):
        client._request_with_retry("GET", "/api/v1/x")
    assert stub.calls == (2 if should_retry else 1)


@requires_siblings
@pytest.mark.parametrize("body", TX_HASH_BODIES)
def test_cli_tx_hash_guard(body: dict):
    """5xx WITH a tx hash raises immediately — never retried."""
    client, stub = _cli_client(lambda: _status_error(500, body), max_retries=2)
    with pytest.raises(APIError) as excinfo:
        client._request_with_retry("POST", "/api/v1/x", json_body={})
    assert excinfo.value.status_code == 500
    assert stub.calls == 1, "response with tx hash must not be retried (double-settle)"


@requires_siblings
def test_cli_5xx_without_tx_hash_still_retries():
    """Control: same 500 but no tx hash in the body — retry proceeds."""
    client, stub = _cli_client(
        lambda: _status_error(500, {"success": False, "transaction": {}})
    )
    with pytest.raises(APIError):
        client._request_with_retry("POST", "/api/v1/x", json_body={})
    assert stub.calls == 2
