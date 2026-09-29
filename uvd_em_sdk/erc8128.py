"""ERC-8128 HTTP Message Signature signer (RFC 9421).

Signs HTTP requests per ERC-8128 (Signed HTTP Requests with Ethereum) so the
SDK can authenticate against production, where API keys are rejected
(``EM_API_KEYS_ENABLED=false``) and only wallet signing is accepted.

Port of the backend reference implementation
(``mcp_server/integrations/erc8128/signer.py``) adapted to the
:class:`~uvd_x402_sdk.wallet.WalletAdapter` protocol — the private key never
leaves the adapter; only ``get_address()`` and ``sign_message()`` (EIP-191
personal_sign) are used.

Flow:
  1. Fetch a fresh single-use nonce from ``GET /api/v1/auth/erc8128/nonce``
  2. Build the RFC 9421 signature base from request components
  3. Sign with EIP-191 personal_sign via the WalletAdapter
  4. Produce ``Signature`` + ``Signature-Input`` (+ ``Content-Digest``) headers

Usage::

    from uvd_x402_sdk.wallet import EnvKeyAdapter
    from uvd_em_sdk.erc8128 import fetch_nonce, sign_request

    wallet = EnvKeyAdapter()
    nonce = await fetch_nonce("https://api.execution.market")
    headers = sign_request(
        wallet,
        method="POST",
        url="https://api.execution.market/api/v1/tasks",
        body='{"title": "test"}',
        nonce=nonce,
    )
    # headers = {"Signature": "...", "Signature-Input": "...", "Content-Digest": "..."}

The server nonce is single-use and expires after 5 minutes — fetch a fresh
one per signed request (:class:`~uvd_em_sdk.client.EMClient` does this
automatically, including on retries).

Wire format: pinned by F3-1 (``shared/test-vectors/erc8128.json``) —
``alg="eip191"`` emitted, keyid ALWAYS lowercase, params in the order
``created;expires;nonce;keyid;alg``. The CANONICAL signer of the fleet is
``uvd_x402_sdk.erc8128`` (F3-2, uvd-x402-sdk >= 0.34.0). uvd-x402-sdk was
the optional ``[wallet]`` extra until 0.8.0, so this module kept its own
implementation for a base install without the SDK; since 0.9.0 the SDK is a
hard dependency, and the local fallback stays until the signer is delegated
(``docs/signer-comparison.md``).
Delegation happens only where behavior is indistinguishable —
:func:`fetch_nonce` is re-exported from the canonical module when the SDK is
importable (fallback below otherwise); :func:`sign_request` stays local
because it reads ``time`` from THIS module's globals (the frozen-time
monkeypatch contract of the test suite). Byte-equality against the canonical
implementation and the golden vectors is enforced in
``tests/test_erc8128.py`` + ``tests/test_erc8128_canonical_parity.py`` and in
the backend conformance suite
(``mcp_server/tests/test_erc8128_conformance.py``).

Reference:
  - ERC-8128: https://eip.tools/eip/8128
  - RFC 9421: https://www.rfc-editor.org/rfc/rfc9421
  - ERC-191: https://eips.ethereum.org/EIPS/eip-191
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:
    from uvd_x402_sdk.wallet import WalletAdapter

# Default label for ERC-8128 signatures
DEFAULT_LABEL = "eth"

# Signature algorithm parameter — pinned by F3-1 (always emitted)
ALG = "eip191"

# Default validity window (seconds) — server policy caps at 300
DEFAULT_VALIDITY_SEC = 300

# keyid chain binding (Base mainnet, the production auth chain)
DEFAULT_CHAIN_ID = 8453


def sign_request(
    wallet: "WalletAdapter",
    method: str,
    url: str,
    body: Optional[str] = None,
    nonce: Optional[str] = None,
    chain_id: int = DEFAULT_CHAIN_ID,
    label: str = DEFAULT_LABEL,
    validity_sec: int = DEFAULT_VALIDITY_SEC,
) -> dict[str, str]:
    """Sign an HTTP request per ERC-8128.

    Args:
        wallet: WalletAdapter that signs the RFC 9421 signature base with
            EIP-191 personal_sign. The key stays inside the adapter.
        method: HTTP method (GET, POST, etc.).
        url: Full URL of the request (authority + path + query are covered).
        body: Request body (for POST/PUT/PATCH). ``None`` for bodyless
            requests. Must be byte-identical to what is sent on the wire —
            the ``Content-Digest`` covers it.
        nonce: Single-use nonce from the server. Required by the EM backend
            (replayable signatures are rejected).
        chain_id: EVM chain ID for the keyid (default: 8453 = Base).
        label: Signature label (default: ``"eth"``).
        validity_sec: Signature validity window in seconds (default: 300).

    Returns:
        Dict with keys ``"Signature"``, ``"Signature-Input"``, and (when a
        body is present) ``"Content-Digest"``. Merge these into the request
        headers before sending.
    """
    address = wallet.get_address().lower()

    parsed = urlparse(url)
    authority = parsed.netloc
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else None

    now = int(time.time())
    created = now
    expires = now + validity_sec

    keyid = f"erc8128:{chain_id}:{address}"

    # Determine covered components
    covered = ["@method", "@authority", "@path"]
    if query:
        covered.append("@query")

    extra_headers = {}

    if body is not None:
        digest = _compute_content_digest(body)
        extra_headers["Content-Digest"] = digest
        covered.append("content-digest")

    # Build signature base
    sig_base = _build_signature_base(
        method=method,
        authority=authority,
        path=path,
        query=query,
        content_digest=extra_headers.get("Content-Digest"),
        label=label,
        covered=covered,
        created=created,
        expires=expires,
        nonce=nonce,
        keyid=keyid,
    )

    # EIP-191 personal_sign via the wallet adapter
    sig_hex = wallet.sign_message(sig_base)
    sig_bytes = bytes.fromhex(sig_hex.removeprefix("0x"))

    # Encode signature as base64 (RFC 8941 byte sequence)
    sig_b64 = base64.b64encode(sig_bytes).decode("ascii")

    # Build headers
    sig_params = _build_signature_params(
        covered=covered,
        created=created,
        expires=expires,
        nonce=nonce,
        keyid=keyid,
    )

    extra_headers["Signature"] = f"{label}=:{sig_b64}:"
    extra_headers["Signature-Input"] = f"{label}={sig_params}"

    return extra_headers


async def fetch_nonce(api_base: str, timeout: float = 10.0) -> str:
    """Fetch a fresh single-use nonce from the server.

    Args:
        api_base: Origin of the API (e.g., ``"https://api.execution.market"``)
            — the ``/api/v1`` prefix is appended here.
        timeout: Request timeout in seconds.

    Returns:
        The nonce value (single-use, 5-minute TTL).
    """
    import httpx

    url = f"{api_base.rstrip('/')}/api/v1/auth/erc8128/nonce"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data["nonce"]


# F3-2 delegation: uvd-x402-sdk >= 0.34.0 ships the canonical
# ``uvd_x402_sdk.erc8128``. ``fetch_nonce`` is a pure re-export (no time
# dependency, byte-identical behavior); the definition above is the fallback
# for an environment without the SDK, which 0.9.0 no longer allows (the
# package imports the SDK first).
# ``sign_request`` is NOT delegated — see the module docstring.
try:
    from uvd_x402_sdk.erc8128 import fetch_nonce as fetch_nonce  # noqa: F811
except ImportError:  # base install: keep the local fallback defined above
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_content_digest(body: str) -> str:
    """Compute Content-Digest header value (SHA-256, RFC 9530)."""
    digest = hashlib.sha256(body.encode("utf-8")).digest()
    b64 = base64.b64encode(digest).decode("ascii")
    return f"sha-256=:{b64}:"


def _build_signature_base(
    method: str,
    authority: str,
    path: str,
    query: Optional[str],
    content_digest: Optional[str],
    label: str,
    covered: list[str],
    created: int,
    expires: int,
    nonce: Optional[str],
    keyid: str,
) -> str:
    """Build the RFC 9421 signature base string."""
    lines = []

    for component in covered:
        if component == "@method":
            lines.append(f'"@method": {method.upper()}')
        elif component == "@authority":
            lines.append(f'"@authority": {authority}')
        elif component == "@path":
            lines.append(f'"@path": {path}')
        elif component == "@query":
            lines.append(f'"@query": {query or "?"}')
        elif component == "content-digest":
            lines.append(f'"content-digest": {content_digest or ""}')

    sig_params = _build_signature_params(
        covered=covered,
        created=created,
        expires=expires,
        nonce=nonce,
        keyid=keyid,
    )
    lines.append(f'"@signature-params": {sig_params}')

    return "\n".join(lines)


def _build_signature_params(
    covered: list[str],
    created: int,
    expires: int,
    nonce: Optional[str],
    keyid: str,
) -> str:
    """Build the @signature-params value per RFC 9421."""
    comp_str = " ".join(f'"{c}"' for c in covered)
    parts = [f"({comp_str})"]
    parts.append(f"created={created}")
    parts.append(f"expires={expires}")
    if nonce:
        parts.append(f'nonce="{nonce}"')
    parts.append(f'keyid="{keyid}"')
    parts.append(f'alg="{ALG}"')
    return ";".join(parts)
