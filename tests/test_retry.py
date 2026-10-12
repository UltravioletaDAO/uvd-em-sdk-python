"""Equivalence suite for the single retry policy (F3-4).

ONE decision table (status/exception -> retry yes/no) that every client-side
implementation must agree with:

  1. ``uvd_em_sdk.retry.request_with_retry``  (canonical) — tested here;
  2. ``execution_market._signer.with_backoff``   (EM's sdk/python, zero-dep)
  3. ``em_cli.api.EMAPIClient._request_with_retry`` (EM's CLI)

plus explicit tests of the tx-hash double-settle guard (invariant 2) and the
Idempotency-Key persistence across attempts. The backend policy in
``mcp_server/integrations/_http_retry.py`` stays as the reference — its own
suite lives in ``mcp_server/tests/test_facilitator_retries.py``.

Legs 2 and 3 live where their code does: Execution Market (private; this
repository is public, so its code is not copied here) runs all three legs in
``cli/tests/test_retry.py``, job ``sdk-tests``, against the uvd-em-sdk it pins.
Here they skipped (they looked for ``sdk/python`` and ``cli/src`` next to the
checkout), so they are gone; what ties them to this file is the table:
``vendor/em/retry.json`` is EM's, at ``vendor/em/SOURCE``'s commit, and
:func:`test_the_decision_table_is_execution_markets` requires this one to be
the same. Nothing here skips (``tests/test_parity_runs.py`` checks it).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uvd_em_sdk.retry import request_with_retry

#: EM's decision table (scripts/vendor_em.py, from EM's cli/tests/test_retry.py).
VENDORED = Path(__file__).resolve().parents[1] / "vendor" / "em" / "retry.json"

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


def _as_vendored() -> dict:
    """This file's tables in ``vendor/em/retry.json``'s shape."""
    return {
        "STATUS_TABLE": [list(row) for row in STATUS_TABLE],
        "EXCEPTION_TABLE": [
            [f"httpx.{exc.__name__}", retry] for exc, retry in EXCEPTION_TABLE
        ],
        "TX_HASH_BODIES": TX_HASH_BODIES,
    }


def _tables_that_differ(em: dict) -> list[str]:
    """The names of this file's tables that are not EM's ``em``."""
    return [name for name, table in _as_vendored().items() if em.get(name) != table]


def test_the_decision_table_is_execution_markets():
    """EM's CI proves its sdk/python and its CLI against EM's table; this file
    proves the SDK against this one. They must be the same table, so a policy
    change here that EM has not made fails here, in this PR."""
    em = json.loads(VENDORED.read_text(encoding="utf-8"))
    assert all(getattr(httpx, exc.__name__) is exc for exc, _ in EXCEPTION_TABLE)
    assert _tables_that_differ(em) == [], (
        "differs from Execution Market's (vendor/em/retry.json): change the "
        "policy in EM's sdk/python and CLI too, then re-vendor with "
        "python scripts/vendor_em.py --em-repo <EM clone>"
    )


@pytest.mark.parametrize("name", ["STATUS_TABLE", "EXCEPTION_TABLE", "TX_HASH_BODIES"])
def test_a_drift_of_each_table_is_named(name: str):
    """Every table is compared: one row less in it, that table is named."""
    em = json.loads(json.dumps(_as_vendored()))
    em[name] = em[name][:-1]
    assert _tables_that_differ(em) == [name]


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
