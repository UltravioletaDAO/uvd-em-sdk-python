"""Async HTTP client for the Execution Market REST API.

Resource-based namespacing (Stripe pattern)::

    async with EMClient(api_key="em_...") as client:
        # Tasks
        task = await client.tasks.create(CreateTaskParams(...))
        async for t in client.tasks.list(status="published"):
            print(t.title)

        # Submissions
        await client.submissions.approve("sub-uuid")

        # Workers
        worker = await client.workers.register(wallet_address="0x...")

    # With wallet adapter (optional — requires uvd-x402-sdk[wallet])::
    from uvd_x402_sdk.wallet import EnvKeyAdapter
    wallet = EnvKeyAdapter()

    async with EMClient(wallet=wallet) as client:
        await client.identity.register("my-agent")
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

# Wallet adapter (optional — requires uvd-x402-sdk[wallet]>=0.20.0)
try:
    from uvd_x402_sdk.wallet import WalletAdapter
except ImportError:
    WalletAdapter = None  # type: ignore[assignment,misc]

from .exceptions import (
    EMAuthError,
    EMError,
    EMNotFoundError,
    EMServerError,
    EMValidationError,
)
from .models import HealthResponse, PlatformConfig
from .resources.tasks import TasksResource
from .resources.submissions import SubmissionsResource
from .resources.workers import WorkersResource
from .resources.reputation import ReputationResource
from .resources.evidence import EvidenceResource
from .resources.payments import PaymentsResource
from .resources.webhooks import WebhooksResource
from .resources.h2a import H2AResource, AgentsResource
from .resources.relay import RelayResource
from .resources.identity import IdentityResource
from .resources.escrow import EscrowResource
from .resources.disputes import DisputesResource
from .resources.worldid import WorldIdResource
from .resources.services import ServicesResource
from .retry import request_with_retry, DEFAULT_MAX_RETRIES

DEFAULT_BASE_URL = "https://api.execution.market/api/v1"
DEFAULT_TIMEOUT = 30.0

# ERC-8128 nonce endpoint, relative to base_url (which includes /api/v1)
NONCE_PATH = "/auth/erc8128/nonce"


class EMClient:
    """Async client for the Execution Market API.

    Args:
        api_key: API key for authentication. **Internal-testing fallback
            only** — production runs with ``EM_API_KEYS_ENABLED=false``,
            which rejects every API key with HTTP 403 (INC-2026-03-27).
            Attach a *wallet* for production writes.
        wallet: Optional :class:`~uvd_x402_sdk.wallet.WalletAdapter` for wallet-based
            signing (ERC-8128, EIP-3009).  Requires ``uvd-x402-sdk[wallet]>=0.20.0``.
            When provided without *api_key*, every non-GET request is signed
            per ERC-8128 (RFC 9421): a fresh single-use nonce is fetched from
            ``GET /auth/erc8128/nonce`` and ``Signature`` / ``Signature-Input``
            / ``Content-Digest`` headers are attached (re-signed per retry
            attempt — nonces are single-use).
        supabase_jwt: Supabase JWT of the *human* — sent as
            ``Authorization: Bearer <jwt>`` ONLY by the ``client.h2a``
            namespace (publisher-side, ``verify_jwt_auth``) and the
            worker-scoped methods (``workers.my_submission`` /
            ``.geo_reference`` / ``.update_social_links``,
            ``submissions.get``; ``verify_worker_auth``). Neither API keys
            nor ERC-8128 signatures are accepted on those routes. All
            other namespaces keep API-key / ERC-8128 auth.
        base_url: Base URL for the API.
        timeout: Request timeout in seconds.
        max_retries: Number of retries on transient failures (429, 5xx).
        http_client: Optional pre-configured httpx.AsyncClient.
    """

    def __init__(
        self,
        api_key: str | None = None,
        wallet: Optional["WalletAdapter"] = None,
        supabase_jwt: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self._wallet = wallet
        self._supabase_jwt = supabase_jwt
        # ERC-8128 signing is the production auth path; API key (internal
        # testing only) takes precedence when explicitly provided.
        self._sign_requests = wallet is not None and api_key is None
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._external_client = http_client is not None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._default_headers(),
        )
        if http_client is not None:
            self._client.headers.update(self._default_headers())

        # Resource namespaces
        self.tasks = TasksResource(self)
        self.submissions = SubmissionsResource(self)
        self.workers = WorkersResource(self)
        self.reputation = ReputationResource(self)
        self.evidence = EvidenceResource(self)
        self.payments = PaymentsResource(self)
        self.webhooks = WebhooksResource(self)
        self.h2a = H2AResource(self)
        self.agents = AgentsResource(self)
        self.relay = RelayResource(self)
        self.identity = IdentityResource(self)
        self.escrow = EscrowResource(self)
        self.disputes = DisputesResource(self)
        self.worldid = WorldIdResource(self)
        self.services = ServicesResource(self)

    def _default_headers(self) -> dict[str, str]:
        # Lazy import: __version__ lives in the package __init__ (the single
        # version source, SDK-51); importing it at module level would be
        # circular (__init__ imports this module first).
        from . import __version__

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"em-plugin-sdk/{__version__}",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # -- lifecycle ----------------------------------------------------------

    async def __aenter__(self) -> EMClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if not self._external_client:
            await self._client.aclose()

    # -- request core (used by resources) -----------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        sign: bool | None = None,
    ) -> Any:
        """Send a request, optionally signed per ERC-8128.

        When a WalletAdapter is attached (and no API key), non-GET requests
        are signed automatically. ``sign`` overrides the default: ``True``
        forces a signature on a GET (owner-scoped reads), ``False`` skips it.
        GETs are unsigned by default because most reads are public. The
        nonce endpoint has a dedicated rate limit of 600/min per IP (exempt
        from the global limiter); on 429 the server includes a Retry-After
        header. The one-nonce-per-request pattern is supported at swarm rate.
        """
        use_signing = self._sign_requests and (
            sign if sign is not None else method.upper() not in ("GET", "HEAD")
        )
        headers_factory = None
        if use_signing:

            def headers_factory() -> Any:
                return self._erc8128_headers(method, path, json=json, params=params)

        resp = await request_with_retry(
            self._client,
            method,
            path,
            max_retries=self._max_retries,
            json=json,
            params=params,
            headers=headers,
            headers_factory=headers_factory,
        )
        return self._handle_response(resp)

    async def _erc8128_headers(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Fetch a single-use nonce and sign this request per ERC-8128.

        Called once per attempt (see ``request_with_retry.headers_factory``):
        the server consumes the nonce before verifying the signature, so a
        retried attempt must re-fetch and re-sign.
        """
        from .erc8128 import sign_request

        nonce_resp = await self._client.get(NONCE_PATH)
        data = self._handle_response(nonce_resp)
        nonce = data.get("nonce") if isinstance(data, dict) else None
        if not nonce:
            raise EMAuthError(
                message="ERC-8128 nonce endpoint returned no nonce",
                details=data if isinstance(data, dict) else {"response": data},
            )

        # Build the exact request httpx will send so the signed components
        # (@authority/@path/@query) and the Content-Digest match the wire
        # bytes exactly.
        req = self._client.build_request(method, path, json=json, params=params)
        body = req.content.decode("utf-8") if json is not None else None
        return sign_request(
            self._wallet,
            method=method,
            url=str(req.url),
            body=body,
            nonce=nonce,
        )

    @staticmethod
    def _handle_response(resp: httpx.Response) -> Any:
        if resp.status_code in (401, 403):
            body = _safe_json(resp)
            raise EMAuthError(
                message=body.get("message", "Authentication failed"),
                details=body,
            )
        if resp.status_code == 404:
            body = _safe_json(resp)
            raise EMNotFoundError(
                message=body.get("message", "Not found"),
                details=body,
            )
        if resp.status_code == 422:
            body = _safe_json(resp)
            raise EMValidationError(
                message=body.get("message", "Validation error"),
                details=body,
            )
        if resp.status_code >= 500:
            body = _safe_json(resp)
            raise EMServerError(
                message=body.get("message", "Server error"),
                status_code=resp.status_code,
                details=body,
            )
        if resp.status_code >= 400:
            body = _safe_json(resp)
            raise EMError(
                message=body.get("message", f"HTTP {resp.status_code}"),
                status_code=resp.status_code,
                details=body,
            )
        if resp.status_code == 204:
            return None
        return resp.json()

    # -- top-level endpoints ------------------------------------------------

    async def health(self) -> HealthResponse:
        """Check API health."""
        data = await self._request("GET", "/health")
        return HealthResponse.model_validate(data)

    async def config(self) -> PlatformConfig:
        """Get public platform configuration (bounty limits, networks, tokens)."""
        data = await self._request("GET", "/config")
        return PlatformConfig.model_validate(data)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {"message": resp.text or f"HTTP {resp.status_code}"}
