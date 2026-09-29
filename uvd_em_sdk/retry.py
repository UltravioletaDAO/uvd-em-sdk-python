"""Retry logic with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable

import httpx

# Status codes that trigger automatic retry
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_FACTOR = 0.5  # seconds
DEFAULT_BACKOFF_MAX = 8.0  # max wait between retries


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    retryable_codes: frozenset[int] = RETRYABLE_STATUS_CODES,
    headers_factory: Callable[[], Awaitable[dict[str, str]]] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with automatic retry on transient failures.

    ``headers_factory`` produces per-attempt headers merged over the static
    ``headers`` kwarg. Single-use credentials (an ERC-8128 signature carries
    a nonce the server consumes before verifying) MUST be regenerated for
    every attempt — a replayed signature is rejected, so re-sending the same
    headers on retry would turn a transient 5xx into a hard 401.

    Static ``headers`` (e.g. ``X-Idempotency-Key``) are re-sent unchanged on
    every attempt so the server can dedupe a retried write; only the
    ``headers_factory`` output is regenerated per attempt.

    A retryable status whose body already carries a transaction hash is
    returned as-is instead of retried: the Facilitator can answer 5xx AFTER
    broadcasting the tx, and a second attempt would risk a double-settle
    (mirrors ``mcp_server/integrations/_http_retry.py``).
    """
    base_headers: dict[str, str] | None = kwargs.pop("headers", None)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        headers = base_headers
        if headers_factory is not None:
            headers = {**(base_headers or {}), **await headers_factory()}
        try:
            resp = await client.request(method, url, headers=headers, **kwargs)

            if resp.status_code not in retryable_codes or attempt == max_retries:
                return resp

            # Never retry a response that already reports a broadcast tx —
            # the money moved even though the status looks retryable.
            if _response_has_tx_hash(resp):
                return resp

            # Use Retry-After header if present (for 429s)
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = _backoff_delay(attempt, backoff_factor, backoff_max)
            else:
                wait = _backoff_delay(attempt, backoff_factor, backoff_max)

            await asyncio.sleep(wait)

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            await asyncio.sleep(_backoff_delay(attempt, backoff_factor, backoff_max))

    # Should not reach here, but satisfy type checker
    if last_exc:
        raise last_exc
    raise RuntimeError("Retry loop exited unexpectedly")  # pragma: no cover


def _response_has_tx_hash(resp: httpx.Response) -> bool:
    """True if the response body already reports a broadcast transaction.

    The Facilitator can respond ``{success: false, transaction: {hash: ...}}``
    with an error status when the on-chain tx went through but a non-fatal
    post-settle hook failed. Same key set as the backend guard
    (``mcp_server/integrations/_http_retry.py::_response_has_tx_hash``).
    """
    try:
        body = resp.json()
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    tx = body.get("transaction")
    if isinstance(tx, dict) and tx.get("hash"):
        return True
    if isinstance(tx, str) and tx:
        return True
    return bool(
        body.get("txHash") or body.get("tx_hash") or body.get("transaction_hash")
    )


def _backoff_delay(attempt: int, factor: float, maximum: float) -> float:
    """Calculate delay with exponential backoff + jitter."""
    delay = factor * (2**attempt)
    delay = min(delay, maximum)
    # Add ±25% jitter
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0, delay + jitter)
