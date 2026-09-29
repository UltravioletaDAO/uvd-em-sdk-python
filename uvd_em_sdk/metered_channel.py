"""Bill a task BY MEASURED UNIT OF WORK on a Solana payment channel.

Every other rail pays ONE lump at the end (escrow release on Base, pay.sh
*charge* on Solana). A payment channel is the opposite shape: the buyer opens a
capped channel, the executor reserves a delivery per unit of work completed,
and the money lands on-chain once — when pay.sh idle-closes the channel. So a
job that finishes early bills less and one that runs longer bills more, without
renegotiating anything.

>>> THIS IS NOT ABOUT ANY PARTICULAR EXECUTOR <<<
A unit is whatever the buyer declared in ``payment_streaming.unit`` when the
task was published: a *second* of elapsed work, or a discrete confirmation —
one ``scan``, one ``token``, one ``byte``. Metering elapsed time and metering
"a camera confirmed the job is happening" are the same mechanism with a
different unit, which is why the unit is the buyer's field and not a constant
compiled into whatever does the work.

>>> WHAT THE EXECUTOR CAN AND CANNOT DO ON THIS RAIL <<<
Read the backend's client before changing anything here:
``mcp_server/integrations/solana/pay_shell_client.py`` — its docstring
enumerates the pay-v0.27.0 control plane from the upstream router
(``rust/crates/cli/src/commands/server/start.rs:1478-1517``). Three facts
decide this module's whole shape:

1. **There is no "open session" endpoint.** A channel is opened by the PAYER,
   as an ``Authorization`` action replayed against a *metered business path*.
   All an executor can do is provoke the 402 and hand the offer over verbatim.
   :meth:`MeteredChannel.open_session` is exactly that — a probe, not a write.
2. **There is no session-state endpoint.** The settle receipt is the only
   readable signal, and it answers ``200`` with nulls for a channel that never
   existed. So "not settled yet" is reported as *unknown*, never as *open*.
   A zero is an absence, and an absence is not a receipt.
3. **There is no close.** Settlement happens out-of-band when pay.sh's own
   lifecycle runloop idle-closes the channel; finishing the work simply means
   the executor stops ticking and starts polling. Anything named ``close_*``
   on this class would be calling an endpoint that does not exist — a test in
   ``tests/test_metered_channel.py`` fails if one is ever added, mirroring
   ``mcp_server/tests/test_payshell_session_channels.py``.

**The executor signs NOTHING on this rail.** The payer signs the channel open
and every cumulative voucher; the executor only *names where its 87% lands*.
That address is a destination, not a key: no ed25519 signer is needed on the
machine doing the work, which is why a device, a container or a person's phone
can all get paid the same way.

>>> WHY THE PAYEE TRAVELS AS ``em_worker_wallet`` <<<
The gateway spec (``infrastructure/pay/em-gateway.yml``) declares the payee of
the 87% split as the recipient alias ``worker: {account: "${EM_WORKER_WALLET}"}``
**deliberately without that env var set**, so our patched gate resolves it per
request — env first, query params second (the same indirection the *charge*
intent already used; upstream issue ``solana-foundation/pay#424``). The live
canary accepts three spellings of that query param — ``em_worker_wallet``,
``em_worker`` and ``worker``. This module always sends ``em_worker_wallet``:

  * it is the exact lowercase of the ``${EM_WORKER_WALLET}`` alias in the YAML,
    so the wire name and the config name grep as one thing;
  * ``worker`` is a plausible *business* query parameter, and the gateway
    proxies unmatched requests to the EM API (``routing.url``) — a bare
    ``worker=`` could one day mean something to the backend too;
  * ``em_worker`` matches neither the alias nor the other conventions.

A wallet the gate does not resolve is NOT an error upstream: it silently falls
back to the configured payout. That is precisely why
:func:`verify_session_splits` re-reads the minted challenge instead of trusting
that the parameter was honoured — config is not evidence.

Config (env):
  ``EM_METERED_BILLING``          force metering on/off; unset decides per task
                                  (see :func:`streaming_requested`)
  ``EM_METERED_GATEWAY_URL``      pay.sh gateway base (default
                                  ``http://127.0.0.1:7081`` — the control plane
                                  is task-internal)
  ``EM_METERED_PATH``             metered ApiSpec path used to mint the offer
                                  (default ``hello``, the cheap smoke endpoint
                                  — never a task write)
  ``EM_METERED_ROUTE``            value for the ``X-EM-Route`` header when the
                                  gateway sits behind the canary rule
  ``EM_METERED_TICK_SECONDS``     seconds between ticks (default 30)
  ``EM_METERED_UNIT``             unit of work (default ``second``)
  ``EM_METERED_PRICE_PER_UNIT``   price of one unit in USD (default 0.000333)
  ``EM_METERED_PAYOUT_ADDRESS``   the executor's Solana address — where the 87%
                                  lands. MUST be the same address bound to
                                  ``executors.solana_payout_address``
  ``EM_METERED_SETTLE_TIMEOUT_S`` how long to wait for the settle (default 240)
  ``EM_METERED_RECEIPT_POLL_S``   receipt poll interval (default 10)
  ``EM_METERED_STOP_GRACE_S``     how long the exit of :meth:`billing` waits for
                                  a tick already under way before cancelling it
                                  (default 10) — see "STOPPING THE METER" below
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Optional, Union

import httpx

logger = logging.getLogger("em.metered.channel")

# Control-plane prefix. Everything pay.sh serves for orchestration lives under
# `/__402/`; the metered business endpoints come from the ApiSpec.
_CP = "/__402"

DEFAULT_GATEWAY_URL = "http://127.0.0.1:7081"
DEFAULT_CHANNEL_PATH = "hello"
DEFAULT_TICK_SECONDS = 30.0

# What a unit of work is. TWO KINDS, and the difference decides how a tick is
# priced — which is the whole reason `unit` is a field and not a constant:
#   * time      — a tick bills the fraction of a unit the interval covers.
#   * countable — a tick bills exactly ONE unit: one scan, one token, one byte.
#                 There is no seconds-per-unit for these; the count IS the meter.
# Mirrors api/routers/_models.py::STREAMING_UNITS; the E2E pins the two lists
# against each other so a unit can never be publishable and unbillable.
UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}
COUNT_UNITS = frozenset({"scan", "token", "byte"})
DEFAULT_UNIT = "second"

# $0.02/minute expressed per SECOND — the same money, in the default unit.
DEFAULT_PRICE_PER_UNIT = Decimal("0.000333")
DEFAULT_SETTLE_TIMEOUT_S = 240.0
DEFAULT_RECEIPT_POLL_S = 10.0

# How long the exit of `billing()` lets a tick already under way finish before
# it cancels the meter. A tick is two HTTP calls — reserve, then mirror — and
# each is bounded by the client's own timeout; this bounds the PAIR, so closing
# the meter never hangs on a gateway that stopped answering.
DEFAULT_STOP_GRACE_S = 10.0

# The canonical 13% (integrations/x402/fees.py::FASE5_FEE_BPS). Mirrored rather
# than imported — this package does not depend on the backend — and pinned
# against the backend constant by the metered-channel E2E.
FASE5_FEE_BPS = 1300

# The query parameter that names the payee of the 87% split. See the module
# docstring for why this spelling and not the other two the gate accepts.
WORKER_QUERY_PARAM = "em_worker_wallet"

# Solana pubkeys, base58. Used for both channel ids and payout addresses:
# validating before interpolating into a control-plane URL is what stops a
# hostile channel id from injecting path segments.
_B58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

FLAG_ENV = "EM_METERED_BILLING"

# What a tick hands EM so the meter can be WATCHED. A tick that only reserves
# against pay.sh is invisible: the gateway has no push channel and no session
# state endpoint, so a reservation that is not mirrored leaves no trace anyone
# can read until the channel settles — which is after the work is over. The
# provider returns the evidence for THIS unit (the same shape a delivery uses),
# and may be sync or async because the thing producing it is usually a camera.
EvidenceProvider = Callable[[], Union[dict[str, Any], Awaitable[dict[str, Any]]]]


class MeteredChannelError(RuntimeError):
    """The channel rail refused an operation the caller cannot recover from."""


class MeteredChannelUnavailable(MeteredChannelError):
    """Streaming was asked for but cannot run (no payout address, no rate…)."""


class ChannelClosedByServer(MeteredChannelError):
    """EM says this channel no longer meters work, so stop reserving.

    The platform caps how many session channels may be open at once and how
    long each one holds its slot (EM_SOLANA_SESSION_MAX_OPEN /
    _MAX_AGE_S). When a channel ages out, EM answers 409 CHANNEL_EXPIRED.

    That answer HAS to stop the work, and this class exists because the
    mirror's own rule — never let a failed mirror stop the tick — makes
    exactly the wrong call here. A tick reserves against pay.sh BEFORE it
    reports to EM, so treating this 409 like a transient mirror failure means
    the executor keeps booking deliveries the platform has stopped counting:
    money moving on one side of the receipt and not the other. That is the
    "espejo perdido" shape, and the ceiling would be the thing causing it.
    """


class SessionNotFound(MeteredChannelError):
    """No open channel matches the id we ticked against (upstream 404)."""


# ---------------------------------------------------------------------------
# The 402 session offer — wire shapes mirrored from the payer SDK
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionSplitOffer:
    """One entry of ``methodDetails.distributionSplits``.

    ``shareBps`` — NOT ``bps``. The payer SDK
    (``solana_pay_kit/protocols/mpp/intents/session.py::SessionSplit.to_dict``)
    is authoritative here: it is what the payer signs.
    """

    recipient: str
    share_bps: int


@dataclass(frozen=True)
class SessionChallenge:
    """A parsed ``intent="session"`` 402 offer — what the payer needs to open."""

    challenge_id: str
    www_authenticate: str
    amount: str = ""
    currency: str = ""
    recipient: str = ""
    network: Optional[str] = None
    channel_id: Optional[str] = None
    decimals: Optional[int] = None
    minimum_deposit: Optional[str] = None
    suggested_deposit: Optional[str] = None
    idle_timeout_seconds: Optional[int] = None
    splits: tuple[SessionSplitOffer, ...] = ()
    request: dict = field(default_factory=dict)

    def describe(self) -> str:
        paid = ", ".join(f"{s.recipient[:6]}…={s.share_bps}bps" for s in self.splits)
        return (
            f"channel offer {self.challenge_id} on {self.network or '?'} · "
            f"primary={self.recipient[:6] if self.recipient else '?'}… · "
            f"splits[{paid or 'none'}]"
        )


def parse_session_challenges(header_values: Iterable[str]) -> list[SessionChallenge]:
    """Every ``intent="session"`` challenge in a set of WWW-Authenticate values.

    One 402 can advertise several schemes: the gate pushes one header per
    session MPP plus the charge ones, and upstream also joins challenges with
    newlines inside a single value. Both shapes are handled — same permissive
    parse as the backend's ``pay_shell_client.parse_session_challenges``.
    """
    out: list[SessionChallenge] = []
    for value in header_values or []:
        if not isinstance(value, str):
            continue
        for raw in [part.strip() for part in value.split("\n")]:
            if not raw.lower().startswith("payment "):
                continue
            challenge = _parse_session_challenge(raw)
            if challenge is not None:
                out.append(challenge)
    return out


def _parse_auth_params(challenge: str) -> dict:
    """``Payment id="x", intent="session", request="…"`` → dict.

    Base64url has no commas, so the naive comma split cannot break the blob.
    """
    params: dict = {}
    for segment in challenge[len("Payment ") :].split(","):
        key, sep, raw = segment.strip().partition("=")
        if not sep:
            continue
        params[key.strip()] = raw.strip().strip('"').strip("'")
    return params


def _parse_session_challenge(raw: str) -> Optional[SessionChallenge]:
    params = _parse_auth_params(raw)
    if params.get("intent") != "session":
        return None
    request = _decode_request_blob(params.get("request", ""))
    if request is None:
        logger.warning("Session challenge carried an undecodable request blob")
        return None

    details = request.get("methodDetails")
    details = details if isinstance(details, dict) else {}
    splits = tuple(
        SessionSplitOffer(
            recipient=str(split.get("recipient")),
            share_bps=int(split.get("shareBps") or 0),
        )
        for split in details.get("distributionSplits") or []
        if isinstance(split, dict) and split.get("recipient")
    )
    return SessionChallenge(
        challenge_id=str(params.get("id") or ""),
        www_authenticate=raw,
        amount=str(request.get("amount") or ""),
        currency=str(request.get("currency") or ""),
        recipient=str(request.get("recipient") or ""),
        network=details.get("network"),
        channel_id=details.get("channelId"),
        decimals=details.get("decimals"),
        minimum_deposit=request.get("minimumDeposit"),
        suggested_deposit=request.get("suggestedDeposit"),
        idle_timeout_seconds=details.get("idleTimeoutSeconds"),
        splits=splits,
        request=request,
    )


def _decode_request_blob(raw: str) -> Optional[dict]:
    """base64url JSON → dict, or ``None`` when it cannot be read.

    An undecodable offer is NOT an offer: never guess the terms of something
    the payer is going to sign.
    """
    if not raw:
        return None
    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def verify_session_splits(
    challenge: SessionChallenge,
    *,
    worker_address: str,
    fee_bps: int = FASE5_FEE_BPS,
) -> None:
    """Assert the MINTED offer really pays this executor 87%. Raises otherwise.

    The offer the payer signs is what binds on-chain, so this checks the
    challenge and not our own request: the worker must carry
    ``10000 - fee_bps`` bps, and the advertised splits must leave exactly
    ``fee_bps`` for the primary recipient (EM's fee — it is the REMAINDER,
    never a split of its own; splits totalling 100% make the payer refuse with
    "splits consume the entire amount").
    """
    worker_bps = 10000 - fee_bps
    matches = [s for s in challenge.splits if s.recipient == worker_address]
    if not matches:
        raise MeteredChannelError(
            f"the session offer does not pay {worker_address}: advertised splits "
            f"are {[s.recipient for s in challenge.splits]} — the gateway fell "
            f"back to its configured payout, so the ?{WORKER_QUERY_PARAM} "
            "parameter was not honoured"
        )
    if len(matches) > 1:
        # The on-chain program rejects duplicate distributionSplits recipients.
        raise MeteredChannelError(
            f"the session offer lists {worker_address} more than once"
        )
    if matches[0].share_bps != worker_bps:
        raise MeteredChannelError(
            f"the session offer pays this executor {matches[0].share_bps}bps, expected {worker_bps}bps"
        )
    remainder = 10000 - sum(s.share_bps for s in challenge.splits)
    if remainder != fee_bps:
        raise MeteredChannelError(
            f"the session splits leave {remainder}bps to the primary recipient, "
            f"expected the {fee_bps}bps platform fee"
        )


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeteringDirective:
    """The reservation handle ``POST /__402/session/deliveries`` returns.

    A reservation is NOT payment: the payer still has to commit a cumulative
    voucher echoing ``delivery_id``, and the money only lands on-chain when
    pay.sh idle-closes the channel.
    """

    delivery_id: str
    session_id: str
    amount: str
    currency: str = ""
    sequence: int = 0
    expires_at: Optional[int] = None

    @classmethod
    def from_wire(cls, body: dict) -> "MeteringDirective":
        return cls(
            delivery_id=str(body.get("deliveryId") or ""),
            session_id=str(body.get("sessionId") or ""),
            amount=str(body.get("amount") or ""),
            currency=str(body.get("currency") or ""),
            sequence=int(body.get("sequence") or 0),
            expires_at=body.get("expiresAt"),
        )


@dataclass(frozen=True)
class SettleReceipt:
    """What the receipt poll proves — and what it deliberately does not.

    ``state`` is two-valued and fails closed: ``settled`` when a signature
    exists, ``unknown`` for everything else. The receipt endpoint answers 200
    with nulls for channels that never existed, so "not settled" can never be
    upgraded to "open".
    """

    channel_id: str
    state: str
    settled_signature: Optional[str] = None
    finalized: bool = False

    @property
    def settled(self) -> bool:
        return self.state == "settled"


# The codes EM answers with when a channel has stopped metering for good.
# Matched on the CODE, never on the prose: the message is written for a human
# and gets rewritten; the code is the contract.
_CHANNEL_OVER_CODES = ("CHANNEL_EXPIRED", "CHANNEL_NOT_REUSABLE")


def _is_channel_closed_by_server(exc: BaseException) -> bool:
    """Whether this failure means "stop", as opposed to "try again"."""
    text = str(exc)
    return any(code in text for code in _CHANNEL_OVER_CODES)


def tick_delivery_id(task_id: str, sequence: int) -> str:
    """Deterministic idempotency key for one metered tick of a task.

    Identical to the backend's ``session_tick_delivery_id`` — upstream keys
    reservations by ``deliveryId``, and a stable key is what stops a retried
    tick from reserving twice.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise MeteredChannelError("task_id is required to build a tick delivery id")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise MeteredChannelError(
            f"sequence must be a non-negative int, got {sequence!r}"
        )
    return f"em-tick-{task_id.strip()}-{sequence}"


def usdc_to_base_units(amount_usdc: Decimal) -> int:
    """USDC decimal → base units (6 decimals), truncating sub-micro dust.

    Truncates rather than rounds: never bill a payer more than the rate agreed.
    """
    return int(
        (Decimal(amount_usdc) * 1_000_000).to_integral_value(rounding="ROUND_DOWN")
    )


def streaming_requested(task: dict[str, Any]) -> bool:
    """Does THIS task pay by channel? (master plan 5.1 → 5.2)

    Two things must be true, and the executor checks both rather than trusting
    either alone:

    * the task pays on **Solana** — channels exist on no other rail here;
    * the buyer asked for streaming, which the publish endpoint records as
      ``metadata.payment_streaming`` (``POST /tasks``, field
      ``payment_streaming``). An executor polls raw task rows
      (``GET /executors/{id}/tasks`` does ``select("*")``), so it reads the
      stored metadata, and also accepts the flat spelling an API response
      model may echo.

    ``EM_METERED_BILLING`` overrides in both directions: truthy bills by
    channel even on a task that did not ask (useful for a rehearsal), falsy
    never bills (useful when the gateway is unreachable).
    """
    flag = (os.environ.get(FLAG_ENV) or "").strip().lower()
    if flag in _TRUTHY:
        return True
    if flag in _FALSY:
        return False

    metadata = task.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = None
    asked = bool(
        (metadata or {}).get("payment_streaming")
        if isinstance(metadata, dict)
        else False
    ) or bool(task.get("payment_streaming"))
    if not asked:
        return False
    network = str(task.get("payment_network") or "").strip().lower()
    if network != "solana":
        logger.warning(
            "Task %s asks for streaming but pays on '%s' — payment channels only "
            "exist on the Solana rail; billing the usual way",
            str(task.get("id"))[:8],
            network or "base",
        )
        return False
    return True


class MeteredChannel:
    """Meters one task on a pay.sh payment channel: open, tick, poll.

    Lifecycle, in the order the loop drives it:

    1. :meth:`open_session` — mint the 402 offer naming this executor's Solana
       payout as the 87% split, and check the minted splits really say so.
    2. :meth:`billing` — one :meth:`tick` per unit while the work is happening.
       Ticks are reservations; nothing moves on-chain yet.
    3. :meth:`await_settlement` — once the work stops, poll the receipt until
       pay.sh's idle-close settles. There is no close to call and none is
       offered.

    Nothing here may cost the executor its delivery: the loop wraps every call
    so a broken gateway degrades to "billed nothing", never to "did not
    deliver".
    """

    def __init__(
        self,
        worker_address: str,
        *,
        task_id: str,
        bounty_usd: Optional[Decimal] = None,
        gateway_url: Optional[str] = None,
        path: Optional[str] = None,
        tick_seconds: Optional[float] = None,
        unit: Optional[str] = None,
        price_per_unit: Optional[Decimal] = None,
        fee_bps: int = FASE5_FEE_BPS,
        http: Optional[httpx.AsyncClient] = None,
        route_header: Optional[str] = None,
        settle_timeout_s: Optional[float] = None,
        receipt_poll_s: Optional[float] = None,
        stop_grace_s: Optional[float] = None,
        em_client: Optional[Any] = None,
        evidence_provider: Optional[EvidenceProvider] = None,
    ) -> None:
        if not isinstance(worker_address, str) or not _B58_RE.fullmatch(worker_address):
            raise MeteredChannelUnavailable(
                "the executor's Solana payout address is not a base58 pubkey "
                f"({worker_address!r}) — set EM_METERED_PAYOUT_ADDRESS to the "
                "same address bound with PATCH /account/solana-payout-address"
            )
        self.worker_address = worker_address
        self.task_id = str(task_id)
        self.bounty_usd = Decimal(bounty_usd) if bounty_usd is not None else None
        self._base_url = (
            gateway_url
            or os.environ.get("EM_METERED_GATEWAY_URL")
            or DEFAULT_GATEWAY_URL
        ).rstrip("/")
        self.path = (
            path or os.environ.get("EM_METERED_PATH") or DEFAULT_CHANNEL_PATH
        ).strip()
        self.tick_seconds = float(
            tick_seconds
            if tick_seconds is not None
            else _env_float("EM_METERED_TICK_SECONDS", DEFAULT_TICK_SECONDS)
        )
        self.unit = (
            (unit or os.environ.get("EM_METERED_UNIT") or DEFAULT_UNIT).strip().lower()
        )
        if self.unit not in UNIT_SECONDS and self.unit not in COUNT_UNITS:
            raise MeteredChannelUnavailable(
                f"unknown unit of work {self.unit!r} — expected one of "
                f"{sorted(set(UNIT_SECONDS) | COUNT_UNITS)}. Refused rather "
                "than defaulted: metering the wrong unit bills the wrong "
                "amount and nothing downstream would notice."
            )
        self.price_per_unit = (
            Decimal(price_per_unit)
            if price_per_unit is not None
            else _env_decimal("EM_METERED_PRICE_PER_UNIT", DEFAULT_PRICE_PER_UNIT)
        )
        self.fee_bps = fee_bps
        self._route_header = (
            route_header
            if route_header is not None
            else (os.environ.get("EM_METERED_ROUTE") or "").strip()
        )
        self.settle_timeout_s = float(
            settle_timeout_s
            if settle_timeout_s is not None
            else _env_float("EM_METERED_SETTLE_TIMEOUT_S", DEFAULT_SETTLE_TIMEOUT_S)
        )
        self.receipt_poll_s = float(
            receipt_poll_s
            if receipt_poll_s is not None
            else _env_float("EM_METERED_RECEIPT_POLL_S", DEFAULT_RECEIPT_POLL_S)
        )
        self.stop_grace_s = float(
            stop_grace_s
            if stop_grace_s is not None
            else _env_float("EM_METERED_STOP_GRACE_S", DEFAULT_STOP_GRACE_S)
        )
        self._http = http
        self._owns_http = http is None
        self._em = em_client
        self._evidence_provider = evidence_provider

        self.challenge: Optional[SessionChallenge] = None
        self.channel_id: Optional[str] = None
        self.sequence = 0
        self.billed_base_units = 0
        self._cap_logged = False
        self.mirrored_ticks = 0
        self.mirror_failures = 0
        self.recovered_mirrors = 0
        self._mirror_off_logged = False
        # Set once EM answers that this channel no longer meters work. Sticky:
        # the platform's age ceiling is not a transient condition, so a later
        # tick must not "retry into" a channel that already lost its slot.
        self.channel_closed_by_server = False
        # Reservations pay.sh CONFIRMED that EM has not accepted yet, keyed by
        # tick number. An entry is written before the mirror is attempted and
        # removed only when EM takes it, so an interrupted or refused mirror
        # leaves a debt behind instead of a hole. See `_drain_owed_mirrors`.
        self._owed_mirrors: dict[int, dict[str, Any]] = {}
        # Set on the way out of `billing()`; the meter loop only ever reads it
        # while IDLE, which is what keeps a tick from being torn in half.
        self._stop = asyncio.Event()

        if self.tick_seconds <= 0:
            raise MeteredChannelUnavailable(
                f"EM_METERED_TICK_SECONDS must be positive, got {self.tick_seconds}"
            )
        if self.tick_amount_base_units <= 0:
            raise MeteredChannelUnavailable(
                f"a tick of {self.units_per_tick} {self.unit}(s) at "
                f"${self.price_per_unit}/{self.unit} floors to 0 USDC base "
                "units — upstream rejects a zero reservation, so raise the "
                "price or lengthen the interval"
            )

    # -- config ------------------------------------------------------------

    @classmethod
    def for_task(
        cls,
        task: dict[str, Any],
        *,
        worker_address: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional["MeteredChannel"]:
        """A channel for ``task``, or ``None`` when this task does not stream.

        Returns ``None`` (never raises) when the task is not a streaming one —
        that is the common case and not an error. A task that DOES ask for
        streaming with no payout address configured raises
        :class:`MeteredChannelUnavailable`: silently billing nothing would look
        exactly like a job that billed correctly.
        """
        if not streaming_requested(task):
            return None
        address = (
            worker_address
            or (os.environ.get("EM_METERED_PAYOUT_ADDRESS") or "").strip()
        )
        if not address:
            raise MeteredChannelUnavailable(
                "this task bills by channel but the executor has no Solana payout "
                "address: set EM_METERED_PAYOUT_ADDRESS (the same address bound "
                "to executors.solana_payout_address) or the 87% would land on the "
                "gateway's configured payout instead"
            )
        bounty = _as_decimal(task.get("bounty_usd"))
        return cls(address, task_id=str(task.get("id")), bounty_usd=bounty, **kwargs)

    @property
    def units_per_tick(self) -> Decimal:
        """How many units of work one tick represents.

        A TIME unit bills the fraction of a unit the interval covers, so a 30s
        tick on a per-minute price bills half a unit. A COUNTABLE unit bills
        exactly one per tick — the caller ticks once per confirmation, and the
        interval is only how often the loop wakes up.
        """
        if self.unit in COUNT_UNITS:
            return Decimal(1)
        return Decimal(str(self.tick_seconds)) / Decimal(UNIT_SECONDS[self.unit])

    @property
    def tick_amount_base_units(self) -> int:
        """USDC base units one tick reserves — the price applied to the units."""
        return usdc_to_base_units(self.price_per_unit * self.units_per_tick)

    @property
    def cap_base_units(self) -> Optional[int]:
        """The most this task may bill: the bounty the buyer agreed to.

        ``None`` when the task carries no bounty — then only the channel's own
        on-chain cap limits the meter, which is pay.sh's business, not ours.
        """
        if self.bounty_usd is None:
            return None
        return usdc_to_base_units(self.bounty_usd)

    @property
    def billed_usdc(self) -> Decimal:
        return Decimal(self.billed_base_units) / Decimal(1_000_000)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            headers = {"X-EM-Route": self._route_header} if self._route_header else None
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0, connect=5.0),
                headers=headers,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    # -- 5.2: open the channel before the work starts -----------------------

    async def open_session(self) -> SessionChallenge:
        """Mint the session offer that pays THIS executor, and verify it does.

        A probe, not a write: on 402 the gate short-circuits before forwarding
        to the backend, so nothing is executed upstream. The returned
        ``www_authenticate`` is what the payer replays with an
        ``Authorization`` header to actually open the channel — EM cannot open
        it and neither can the executor.
        """
        url = "/" + self.path.lstrip("/")
        resp = await self._client().get(
            url, params={WORKER_QUERY_PARAM: self.worker_address}
        )
        if resp.status_code != 402:
            raise MeteredChannelError(
                f"expected a 402 session challenge from {self.path!r}, got "
                f"{resp.status_code}: {_error_message(resp)}. A 200 means the path "
                "is not metered; a 404 means it is absent from the gateway ApiSpec"
            )
        challenges = parse_session_challenges(resp.headers.get_list("www-authenticate"))
        if not challenges:
            raise MeteredChannelError(
                f'pay.sh 402\'d {self.path!r} without an intent="session" challenge: '
                "the gateway spec has no parseable `session:` block, or this "
                "endpoint accepts charge only"
            )
        challenge = challenges[0]
        # Config is not evidence: the gate falls back to its configured payout
        # when it cannot resolve the parameter, and that failure is SILENT.
        verify_session_splits(
            challenge, worker_address=self.worker_address, fee_bps=self.fee_bps
        )
        self.challenge = challenge
        if challenge.channel_id:
            self.bind_channel(challenge.channel_id)
        logger.info(
            "Channel offer minted for task %s: %s · this executor takes %dbps of every voucher",
            self.task_id[:8],
            challenge.describe(),
            10000 - self.fee_bps,
        )
        return challenge

    def bind_channel(self, channel_id: str) -> str:
        """Record the channel the PAYER opened — ticks need its id.

        The id is a Solana pubkey and gets interpolated into a control-plane
        URL, so it is validated here rather than at the request.
        """
        if not isinstance(channel_id, str) or not _B58_RE.fullmatch(channel_id):
            raise MeteredChannelError(f"invalid channel id: {channel_id!r}")
        self.channel_id = channel_id
        return channel_id

    # -- 5.3: one tick per unit of work -------------------------------------

    async def tick(self) -> MeteringDirective:
        """Reserve one unit of work against the open channel.

        ``POST /__402/session/deliveries`` with a deterministic ``deliveryId``
        (``em-tick-<task>-<n>``) so a retried tick reserves the SAME delivery
        instead of double-billing.
        """
        if self.channel_closed_by_server:
            # BEFORE the reservation, deliberately. Checking afterwards would
            # book the delivery first and refuse to report it — the exact
            # asymmetry this flag exists to prevent.
            raise ChannelClosedByServer(
                "EM stopped metering this channel (it reached the platform's "
                "maximum age). Reserving more deliveries would move money the "
                "platform is no longer counting."
            )
        if not self.channel_id:
            raise MeteredChannelError(
                "no channel to bill: the payer has not opened one yet (or its id "
                "never reached the executor — call bind_channel)"
            )
        amount = self._next_tick_amount()
        if amount is None:
            raise MeteredChannelError("this task already billed its whole bounty")

        delivery_id = tick_delivery_id(self.task_id, self.sequence)
        payload = {
            "sessionId": self.channel_id,
            # Upstream parses this with `str::parse::<u64>()` — a base-unit
            # integer rendered as a string, never a decimal.
            "amount": str(amount),
            "deliveryId": delivery_id,
        }
        resp = await self._client().post(f"{_CP}/session/deliveries", json=payload)
        if resp.status_code == 404:
            raise SessionNotFound(
                f"no open pay.sh channel {self.channel_id}: {_error_message(resp)}"
            )
        if resp.status_code not in (200, 201):
            raise MeteredChannelError(
                f"tick {self.sequence} for task {self.task_id[:8]} failed: "
                f"{resp.status_code} {_error_message(resp)}"
            )
        directive = MeteringDirective.from_wire(resp.json())
        self.sequence += 1
        self.billed_base_units += amount
        logger.info(
            "Tick %d: reserved %s USDC (%s total) on channel %s…",
            directive.sequence or self.sequence,
            Decimal(amount) / Decimal(1_000_000),
            self.billed_usdc,
            self.channel_id[:8],
        )
        await self._report_tick(delivery_id=delivery_id, tick=self.sequence)
        return directive

    # -- 5.3b: the same tick, told to EM ------------------------------------

    def attach_em_mirror(
        self, em_client: Any, evidence_provider: EvidenceProvider
    ) -> None:
        """Wire the mirror after construction — for a channel built earlier.

        The evidence a tick carries depends on the TASK (its schema decides
        which types the server will accept), so the provider is often only
        knowable once the task is in hand. A channel handed to the loop
        pre-built would otherwise bill correctly and mirror nothing, which is
        the one failure that looks exactly like success.
        """
        self._em = em_client
        self._evidence_provider = evidence_provider
        self._mirror_off_logged = False

    async def _mirror_tick_to_em(
        self, *, delivery_id: Optional[str] = None, tick: Optional[int] = None
    ) -> bool:
        """Tell EM the unit that pay.sh just reserved. Never raises.

        The reservation and the mirror answer different questions and BOTH are
        needed: the gateway holds the money, EM holds the only readable history
        (``mpp_session_events``), because pay.sh exposes no session state and no
        push — a reservation nobody mirrors is invisible until settlement, which
        lands after the work is over.

        Same contract as :meth:`try_tick`: a failure here is a log line. The
        mirror is a REPORT of a reservation that already happened, so letting it
        raise would turn "EM was unreachable" into "the work was not billed" —
        and the money is already reserved either way.

        >>> WHAT CANNOT BE REPORTED BECOMES A DEBT, NOT A HOLE <<<
        The reading is captured and the tick written into ``_owed_mirrors``
        BEFORE EM is called, and removed only once EM accepts it. Cancellation
        is held off by :meth:`_report_tick`, but a shield does not protect
        against FAILURE — so the other road to the same state, EM refusing or
        the network dropping, leaves the identical visible record and
        :meth:`_drain_owed_mirrors` resends it. One ledger, both roads.

        The evidence travels WITH the tick instead of being re-read at resend
        time on purpose: a reading taken later belongs to a later moment, and
        pinning it to an earlier unit of work would make the receipt describe
        something that did not happen.

        Returns True only when EM accepted the tick. Anything else — no client,
        no evidence, a 422 the server refused — returns False, and False means
        the meter did not advance in EM. It never means the reservation failed.
        """
        if self._em is None or self._evidence_provider is None:
            if not self._mirror_off_logged:
                self._mirror_off_logged = True
                logger.info(
                    "Ticks for task %s are NOT mirrored to EM (no %s): the "
                    "channel still bills, but nothing can watch it until the "
                    "channel settles",
                    self.task_id[:8],
                    "em_client" if self._em is None else "evidence_provider",
                )
            return False
        tick = self.sequence if tick is None else tick
        try:
            evidence = self._evidence_provider()
            if asyncio.iscoroutine(evidence) or isinstance(evidence, Awaitable):
                evidence = await evidence
            if not isinstance(evidence, dict) or not evidence:
                # An empty dict is an absence, and the server refuses it for the
                # same reason: a tick with no evidence is a claim, not a receipt.
                raise MeteredChannelError(
                    "the evidence provider returned nothing for this tick"
                )
        except Exception as exc:  # noqa: BLE001 - the mirror never stops the work
            # No reading means there is nothing a resend could carry: this tick
            # is unmirrorable rather than owed. Counted, never queued.
            self._note_mirror_failure(tick, exc)
            return False
        self._owed_mirrors[tick] = {
            "delivery_id": delivery_id or tick_delivery_id(self.task_id, tick - 1),
            "evidence": evidence,
        }
        try:
            await self._send_mirror(evidence)
        except ChannelClosedByServer:
            # The one mirror failure that MUST stop the work. Everything else
            # here is "EM did not hear us"; this is "EM will not count us".
            raise
        except Exception as exc:  # noqa: BLE001 - the mirror never stops the work
            self._note_mirror_failure(tick, exc)
            return False
        self._owed_mirrors.pop(tick, None)
        self.mirrored_ticks += 1
        return True

    async def _report_tick(self, *, delivery_id: str, tick: int) -> bool:
        """:meth:`_mirror_tick_to_em`, finished even if the caller is cancelled.

        By the time this runs the tally already counts a delivery pay.sh
        accepted, so a cancellation landing anywhere in the report step used to
        leave the meter reading LESS than the money — and leave it silently:
        no counter moved, no line was logged.

        Two details decide whether this works:

        * The shielded future is NOT the one to wait on once the cancellation
          lands — that one is already cancelled and would only raise again. The
          INNER task is, which is why it is kept in hand.
        * The wait is bounded by ``stop_grace_s``. An unbounded shield trades
          "the meter is one tick short" for "the caller never gets control
          back", and on something with a physical job to finish that is the
          worse of the two.

        The cancellation is always re-raised: whoever asked for it still wants
        the meter to stop. What changes is that the report goes out first.
        """
        report = asyncio.ensure_future(
            self._mirror_tick_to_em(delivery_id=delivery_id, tick=tick)
        )
        try:
            return await asyncio.shield(report)
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(report, timeout=self.stop_grace_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - degraded, and already owed
                logger.warning(
                    "The report for tick %d of task %s was cancelled and did "
                    "not finish on its own either (%s) — it stays owed",
                    tick,
                    self.task_id[:8],
                    exc,
                )
            raise

    async def _send_mirror(self, evidence: dict) -> None:
        """The one call that puts a tick in ``mpp_session_events``."""
        try:
            await self._em.tasks.tick(
                self.task_id,
                evidence=evidence,
                unit=self.unit,
                # Declaring the payee is a free cross-check: EM refuses LOUDLY
                # if it disagrees with the worker assigned to the task, which is
                # how a gateway paying one address and a task naming another
                # gets caught on the first tick instead of at settlement.
                payee=self.worker_address,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised, classified first
            if _is_channel_closed_by_server(exc):
                self.channel_closed_by_server = True
                raise ChannelClosedByServer(str(exc)) from exc
            raise

    def _note_mirror_failure(self, tick: int, exc: BaseException) -> None:
        self.mirror_failures += 1
        logger.warning(
            "Tick %d of task %s reserved but NOT mirrored to EM (%s): the "
            "money moved, the meter did not",
            tick,
            self.task_id[:8],
            exc,
        )

    @property
    def unmirrored_ticks(self) -> int:
        """Reservations pay.sh confirmed that EM has still not been told about.

        The honest closing number of an executor's report. ``mirror_failures``
        counts ATTEMPTS that went wrong, so a tick that failed once and was
        resent successfully is a 1 there and a 0 here. THIS is the count that
        means the money moved and the meter never caught up.
        """
        return len(self._owed_mirrors)

    async def _drain_owed_mirrors(self) -> int:
        """Resend the mirrors still owed, then NAME whatever is left.

        Runs on the way out of :meth:`billing`, once the meter is down. One
        pass and no retry loop on purpose: if EM is unreachable it will still be
        unreachable on the fourth attempt, and the work has already finished —
        handing back control must not hang behind a bookkeeping retry.

        What survives the pass is logged BY ``deliveryId``. That id is
        deterministic (``em-tick-<task>-<n>``) and the reservation behind it is
        idempotent, so a named leftover is something a human or a job can
        actually finish. An unnamed one is only a number that does not add up.
        """
        if not self._owed_mirrors or self._em is None:
            return len(self._owed_mirrors)
        logger.warning(
            "%d tick(s) of task %s are reserved on pay.sh and unknown to EM — "
            "resending before the channel goes idle",
            len(self._owed_mirrors),
            self.task_id[:8],
        )
        for tick, owed in sorted(self._owed_mirrors.items()):
            try:
                # Bounded: the reason this tick is owed may well be that EM
                # stopped answering, and an unbounded resend would trade "the
                # meter is short" for "the work never hands back".
                await asyncio.wait_for(
                    self._send_mirror(owed["evidence"]), timeout=self.stop_grace_s
                )
            except Exception as exc:  # noqa: BLE001 - already a degraded path
                logger.warning(
                    "Resending tick %d of task %s still failed (%s)",
                    tick,
                    self.task_id[:8],
                    exc,
                )
                continue
            self._owed_mirrors.pop(tick, None)
            self.mirrored_ticks += 1
            self.recovered_mirrors += 1
        if self._owed_mirrors:
            left = ", ".join(
                owed["delivery_id"] for _, owed in sorted(self._owed_mirrors.items())
            )
            logger.error(
                "Task %s ends with %d reservation(s) pay.sh accepted and EM "
                "never recorded: %s. The meter is short by that much; those "
                "deliveryIds are idempotent, so replaying them is safe.",
                self.task_id[:8],
                len(self._owed_mirrors),
                left,
            )
        return len(self._owed_mirrors)

    def _next_tick_amount(self) -> Optional[int]:
        """Base units the next tick may reserve — ``None`` once the cap is hit.

        The cap is the bounty the buyer agreed to. Billing past it would be the
        executor charging for a job nobody bought; the last tick is trimmed to
        whatever is left instead of being skipped, so the meter lands exactly
        on the agreed total.
        """
        amount = self.tick_amount_base_units
        cap = self.cap_base_units
        if cap is None:
            return amount
        remaining = cap - self.billed_base_units
        if remaining <= 0:
            return None
        return min(amount, remaining)

    async def try_tick(self) -> Optional[MeteringDirective]:
        """:meth:`tick`, but a failure is a log line — never a lost delivery."""
        try:
            return await self.tick()
        except MeteredChannelError as exc:
            logger.warning("Metering tick skipped: %s", exc)
        except Exception:  # noqa: BLE001 - metering must never stop the work
            logger.exception("Metering tick failed; the work continues unbilled")
        return None

    @asynccontextmanager
    async def billing(self) -> AsyncIterator["MeteredChannel"]:
        """Tick every interval for as long as the block inside is running.

        The meter is a task this context manager OWNS: no ticking survives the
        work and no exception disappears into a detached task.

        >>> STOPPING THE METER IS ASKED FOR, NOT IMPOSED <<<
        This used to end with a bare ``meter.cancel()``, and a tick is not one
        await — it reserves against pay.sh, raises the tally, and only then
        reports to EM. Cancelling blind tore that apart wherever it happened to
        land, and BOTH halves of the tear lost money in the same direction:

          * inside the report  -> pay.sh reserved, EM never told. Measured:
            ``sequence=1, mirrored=0, mirror_failures=0`` — no counter moved and
            no line was logged, which is why it took a CI runner to find it.
          * inside the RESERVATION -> worse and quieter still: pay.sh books the
            delivery and the executor never learns it did. Measured:
            ``pay.sh reserved=1, sdk sequence=0``. Nothing downstream can even
            name that one, because the local tally never counted it.

        So the exit ASKS the loop to stop and lets the tick under way finish.
        The loop only reads the request while IDLE, which is what makes a tick
        indivisible. The wait is bounded by ``stop_grace_s`` — a report is worth
        waiting for, not worth stalling the caller on — and only when that grace
        runs out does the cancel come back, with the debt ledger to catch
        whatever tore anyway.
        """
        self._stop.clear()
        meter = asyncio.create_task(
            self._meter_loop(), name=f"em-metered-{self.task_id[:8]}"
        )
        try:
            yield self
        finally:
            self._stop.set()
            try:
                await asyncio.wait_for(meter, timeout=self.stop_grace_s)
            except asyncio.TimeoutError:
                # wait_for already cancelled it and waited for the unwind.
                logger.warning(
                    "A tick for task %s was still under way %.0fs after the work "
                    "ended and had to be cancelled — anything it reserved is in "
                    "the debt below, or in neither ledger if the gateway never "
                    "answered",
                    self.task_id[:8],
                    self.stop_grace_s,
                )
            except asyncio.CancelledError:
                # Our own caller is going down; wait_for took the meter with it.
                raise
            except Exception:  # noqa: BLE001 - already logged inside the loop
                logger.exception("The meter ended badly; the work is unaffected")
            await self._drain_owed_mirrors()
            logger.info(
                "Meter stopped for task %s after %d tick(s) — %s USDC reserved, "
                "%d mirrored to EM, %d still unmirrored",
                self.task_id[:8],
                self.sequence,
                self.billed_usdc,
                self.mirrored_ticks,
                self.unmirrored_ticks,
            )

    async def _nap(self) -> bool:
        """Wait one billing interval, or until the meter is asked to stop.

        Returns True when it was the stop request that ended the wait — the
        loop is idle at that instant, which is precisely what makes a tick
        indivisible.

        >>> THE INTERVAL IS A SLEEP, NEVER A TIMEOUT <<<
        The obvious spelling is ``wait_for(self._stop.wait(), tick_seconds)``,
        and it is wrong on Windows. Measured here (py3.11.4, ProactorEventLoop):
        a **10 ms timeout** fires in ~0.05 ms — the loop treats a timer shorter
        than the clock resolution as already due — while ``sleep(0.01)`` takes
        its ~11 ms. Same call, 250x apart. Written with a timeout, a channel on
        a short interval would reserve hundreds of deliveries a second against
        the buyer's cap: a far worse money bug than the missing mirror this all
        started from, and one that only shows up on the platform nobody runs
        the suite on. Racing the two is exact at every interval AND stops on the
        instant.
        """
        stopping = asyncio.ensure_future(self._stop.wait())
        napping = asyncio.ensure_future(asyncio.sleep(self.tick_seconds))
        try:
            await asyncio.wait({stopping, napping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopping.cancel()
            napping.cancel()
        return self._stop.is_set()

    async def _meter_loop(self) -> None:
        """Sleep, tick, repeat. Stops on its own once the bounty is exhausted.

        The wait answers the stop request as well as the clock, so being asked
        to stop is obeyed at once when idle — and NEVER mid-tick, which is the
        whole point.
        """
        while True:
            if await self._nap():
                return  # asked to stop while idle — nothing is half-done
            if self._next_tick_amount() is None:
                if not self._cap_logged:
                    logger.warning(
                        "Task %s has billed its whole bounty (%s USDC) — the meter "
                        "stops here; the work is finishing on the executor's own time",
                        self.task_id[:8],
                        self.billed_usdc,
                    )
                    self._cap_logged = True
                return
            await self.try_tick()

    # -- 5.4: work ends → idle-close → poll the receipt ----------------------

    async def receipt(self) -> SettleReceipt:
        """Poll the settle signature once. ``unknown`` until pay.sh settles."""
        if not self.channel_id:
            raise MeteredChannelError("no channel to poll a receipt for")
        resp = await self._client().get(
            f"{_CP}/payment-channels/receipt/{self.channel_id}"
        )
        if resp.status_code != 200:
            raise MeteredChannelError(
                f"receipt poll for {self.channel_id[:8]}… returned "
                f"{resp.status_code}: {_error_message(resp)}"
            )
        body = resp.json() if resp.content else {}
        body = body if isinstance(body, dict) else {}
        signature = body.get("settledSignature")
        finalized = bool(body.get("finalized"))
        return SettleReceipt(
            channel_id=self.channel_id,
            state="settled" if finalized else "unknown",
            settled_signature=signature,
            finalized=finalized,
        )

    async def await_settlement(
        self,
        *,
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
    ) -> SettleReceipt:
        """Wait for pay.sh's idle-close to settle the channel on-chain.

        >>> THERE IS NOTHING TO CLOSE, AND THAT IS THE DESIGN. <<<
        Payment-channel sessions settle out-of-band when pay.sh's lifecycle
        runloop idle-closes the channel; upstream exposes no close and no
        settle-now. Finishing means: stop ticking, let the channel go idle,
        and poll. A timeout here is NOT a failure of the work — the settle
        usually lands after the executor has already moved on, and the receipt
        stays pollable.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(
            timeout if timeout is not None else self.settle_timeout_s
        )
        interval = float(
            poll_interval if poll_interval is not None else self.receipt_poll_s
        )
        last = SettleReceipt(channel_id=self.channel_id or "", state="unknown")
        while True:
            try:
                last = await self.receipt()
            except MeteredChannelError as exc:
                logger.warning("Receipt poll failed: %s", exc)
            if last.settled:
                logger.info(
                    "Channel %s… settled on-chain: %s",
                    last.channel_id[:8],
                    last.settled_signature,
                )
                return last
            if loop.time() >= deadline:
                logger.warning(
                    "Channel %s… has not settled yet after %.0fs. pay.sh settles at "
                    "idle-close and cannot be hurried; poll the receipt again later.",
                    (self.channel_id or "?")[:8],
                    float(timeout if timeout is not None else self.settle_timeout_s),
                )
                return last
            await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_message(resp: httpx.Response) -> str:
    """Best-effort extraction of upstream's ``{error, message}`` envelope."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        parts = [str(body[k]) for k in ("error", "message") if body.get(k)]
        if parts:
            return " — ".join(parts)
    return str(body)[:200]


def _as_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Malformed %s=%r — using %s", name, raw, default)
        return default


def _env_decimal(name: str, default: Decimal) -> Decimal:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return Decimal(raw)
    except InvalidOperation:
        logger.warning("Malformed %s=%r — using %s", name, raw, default)
        return default
