"""MeteredChannel — an executor billing a task on a Solana payment channel.

These are the 36 tests that landed with the executor spike, ported unchanged in
substance to the generic module. What they pin has nothing to do with what does
the work: who signs, that the minted splits are RE-READ rather than trusted,
that three gateway endpoints do not exist and are never called, and that a
broken gateway degrades to "billed nothing" and never to "did not deliver".
"""

from __future__ import annotations

import asyncio
import base64
import json
from decimal import Decimal

import httpx
import pytest

from uvd_em_sdk.metered_channel import (
    WORKER_QUERY_PARAM,
    MeteredChannel,
    MeteredChannelError,
    MeteredChannelUnavailable,
    SessionNotFound,
    parse_session_challenges,
    streaming_requested,
    tick_delivery_id,
    usdc_to_base_units,
    verify_session_splits,
)

GATEWAY = "http://payshell.test"
TASK_ID = "11111111-2222-4333-8444-555555555555"
# base58 pubkeys — never a key, only addresses.
WORKER_PAYOUT = "9vXF5Wn7DXvke63gJG2qxLby9pteqk1WB8TpuSKjiWoY"
TREASURY = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
CHANNEL = "CHNLxYvVA28MJP9PrFuDXccuoGXAx7jBacfLEkahyGsX"

WORKER_87 = [{"recipient": WORKER_PAYOUT, "shareBps": 8700}]


# ---------------------------------------------------------------------------
# Fixtures that build a REAL-shaped 402 session offer
# ---------------------------------------------------------------------------


def _request_blob(splits=None, channel_id: str | None = None) -> str:
    request = {
        "amount": "1000",
        "currency": "USDC",
        "recipient": TREASURY,
        "minimumDeposit": "100000",
        "suggestedDeposit": "20000000",
        "unitType": "request",
        "methodDetails": {
            "network": "solana",
            "channelProgram": CHANNEL,
            "decimals": 6,
            "voucherSigner": "client",
            "idleTimeoutSeconds": 15,
            "distributionSplits": splits if splits is not None else [],
            **({"channelId": channel_id} if channel_id else {}),
        },
    }
    return base64.urlsafe_b64encode(json.dumps(request).encode()).decode().rstrip("=")


def _session_header(
    splits=None, *, channel_id: str | None = None, cid: str = "ch_1"
) -> str:
    return (
        f'Payment id="{cid}", realm="Execution Market", method="solana", '
        f'intent="session", request="{_request_blob(splits, channel_id)}"'
    )


def _charge_header() -> str:
    blob = (
        base64.urlsafe_b64encode(
            json.dumps({"amount": "10000", "recipient": TREASURY}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f'Payment id="ch_c", method="solana", intent="charge", request="{blob}"'


def _recorder(responses):
    """Replay ``responses`` in order (last one repeats), recording requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    return handler, seen


def _channel(handler, **kwargs) -> MeteredChannel:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=GATEWAY, timeout=5.0
    )
    kwargs.setdefault("tick_seconds", 30.0)
    kwargs.setdefault("unit", "minute")
    kwargs.setdefault("price_per_unit", Decimal("0.02"))
    return MeteredChannel(WORKER_PAYOUT, task_id=TASK_ID, http=client, **kwargs)


# ---------------------------------------------------------------------------
# 5.1 -> 5.2: which tasks bill by channel at all
# ---------------------------------------------------------------------------


def test_streaming_is_off_for_an_ordinary_task(monkeypatch):
    monkeypatch.delenv("EM_METERED_BILLING", raising=False)
    assert streaming_requested({"id": TASK_ID, "payment_network": "base"}) is False


def test_streaming_reads_the_metadata_the_publisher_wrote(monkeypatch):
    monkeypatch.delenv("EM_METERED_BILLING", raising=False)
    task = {
        "id": TASK_ID,
        "payment_network": "solana",
        "metadata": {"payment_streaming": True},
    }
    assert streaming_requested(task) is True
    # The executor polls RAW rows, where metadata can arrive as a JSON string.
    assert (
        streaming_requested({**task, "metadata": json.dumps(task["metadata"])}) is True
    )
    # And the flat spelling an API response model echoes.
    assert (
        streaming_requested(
            {"id": TASK_ID, "payment_network": "solana", "payment_streaming": True}
        )
        is True
    )


def test_streaming_refuses_a_non_solana_task(monkeypatch):
    """Channels exist on the Solana rail only — never guess on another chain."""
    monkeypatch.delenv("EM_METERED_BILLING", raising=False)
    task = {
        "id": TASK_ID,
        "payment_network": "base",
        "metadata": {"payment_streaming": True},
    }
    assert streaming_requested(task) is False


@pytest.mark.parametrize("value,expected", [("1", True), ("0", False), ("off", False)])
def test_the_env_override_wins_in_both_directions(monkeypatch, value, expected):
    monkeypatch.setenv("EM_METERED_BILLING", value)
    assert streaming_requested({"id": TASK_ID, "payment_network": "base"}) is expected


def test_for_task_returns_none_when_the_task_does_not_stream(monkeypatch):
    monkeypatch.delenv("EM_METERED_BILLING", raising=False)
    assert MeteredChannel.for_task({"id": TASK_ID, "payment_network": "solana"}) is None


def test_for_task_refuses_to_bill_without_a_payout_address(monkeypatch):
    """Billing nothing must not look like a successful job."""
    monkeypatch.delenv("EM_METERED_BILLING", raising=False)
    monkeypatch.delenv("EM_METERED_PAYOUT_ADDRESS", raising=False)
    with pytest.raises(MeteredChannelUnavailable):
        MeteredChannel.for_task(
            {
                "id": TASK_ID,
                "payment_network": "solana",
                "metadata": {"payment_streaming": True},
            }
        )


def test_for_task_carries_the_bounty_as_the_meter_cap(monkeypatch):
    monkeypatch.setenv("EM_METERED_PAYOUT_ADDRESS", WORKER_PAYOUT)
    channel = MeteredChannel.for_task(
        {
            "id": TASK_ID,
            "payment_network": "solana",
            "bounty_usd": 0.10,
            "metadata": {"payment_streaming": True},
        },
        tick_seconds=30.0,
        unit="minute",
        price_per_unit=Decimal("0.02"),
    )
    assert channel is not None
    assert channel.cap_base_units == 100_000  # $0.10 in USDC base units


def test_a_non_base58_payout_address_is_refused():
    with pytest.raises(MeteredChannelUnavailable):
        MeteredChannel("0x" + "11" * 20, task_id=TASK_ID)


def test_a_tick_that_floors_to_zero_is_refused():
    """Upstream rejects a zero reservation — say so instead of ticking dust."""
    with pytest.raises(MeteredChannelUnavailable) as exc:
        MeteredChannel(
            WORKER_PAYOUT,
            task_id=TASK_ID,
            tick_seconds=0.01,
            unit="minute",
            price_per_unit=Decimal("0.000001"),
        )
    assert "0 USDC base units" in str(exc.value)


# ---------------------------------------------------------------------------
# 5.2: open the session — the offer names THIS executor
# ---------------------------------------------------------------------------


async def test_open_session_names_the_executor_in_the_query_param():
    handler, seen = _recorder(
        [
            httpx.Response(
                402, headers=[("www-authenticate", _session_header(WORKER_87))]
            )
        ]
    )
    channel = _channel(handler)

    offer = await channel.open_session()

    assert offer.challenge_id == "ch_1"
    assert offer.recipient == TREASURY  # the 13% remainder rides on the primary
    assert [(s.recipient, s.share_bps) for s in offer.splits] == [(WORKER_PAYOUT, 8700)]
    assert offer.network == "solana"
    # The payee travels as a query parameter on the METERED path.
    request = seen[0]
    assert request.url.params[WORKER_QUERY_PARAM] == WORKER_PAYOUT
    assert request.url.path == "/hello"
    assert request.method == "GET"


async def test_open_session_never_touches_the_control_plane():
    """There is no /__402 open route — probing one would be inventing an API."""
    handler, seen = _recorder(
        [
            httpx.Response(
                402, headers=[("www-authenticate", _session_header(WORKER_87))]
            )
        ]
    )

    await _channel(handler, path="api/v1/tasks").open_session()

    assert [r.url.path for r in seen] == ["/api/v1/tasks"]
    assert not any("__402" in r.url.path for r in seen)


async def test_open_session_picks_the_session_offer_out_of_many():
    handler, _ = _recorder(
        [
            httpx.Response(
                402,
                headers=[
                    ("www-authenticate", _charge_header()),
                    ("www-authenticate", _session_header(WORKER_87)),
                ],
            )
        ]
    )
    assert (await _channel(handler).open_session()).challenge_id == "ch_1"


async def test_open_session_reads_newline_joined_challenges():
    handler, _ = _recorder(
        [
            httpx.Response(
                402,
                headers=[
                    (
                        "www-authenticate",
                        _charge_header() + "\n" + _session_header(WORKER_87),
                    )
                ],
            )
        ]
    )
    assert (await _channel(handler).open_session()).splits[0].share_bps == 8700


async def test_open_session_binds_the_channel_id_when_the_offer_carries_one():
    handler, _ = _recorder(
        [
            httpx.Response(
                402,
                headers=[
                    ("www-authenticate", _session_header(WORKER_87, channel_id=CHANNEL))
                ],
            )
        ]
    )
    channel = _channel(handler)

    await channel.open_session()

    assert channel.channel_id == CHANNEL


async def test_open_session_rejects_an_offer_that_pays_someone_else():
    """The silent failure mode: the gate ignored the parameter and fell back."""
    handler, _ = _recorder(
        [
            httpx.Response(
                402,
                headers=[
                    (
                        "www-authenticate",
                        _session_header([{"recipient": TREASURY, "shareBps": 8700}]),
                    )
                ],
            )
        ]
    )
    with pytest.raises(MeteredChannelError) as exc:
        await _channel(handler).open_session()
    assert WORKER_QUERY_PARAM in str(exc.value)


async def test_open_session_rejects_a_shortchanged_worker():
    handler, _ = _recorder(
        [
            httpx.Response(
                402,
                headers=[
                    (
                        "www-authenticate",
                        _session_header(
                            [{"recipient": WORKER_PAYOUT, "shareBps": 5000}]
                        ),
                    )
                ],
            )
        ]
    )
    with pytest.raises(MeteredChannelError) as exc:
        await _channel(handler).open_session()
    assert "5000bps" in str(exc.value)


async def test_open_session_rejects_a_200():
    """200 means the path is not metered: no channel can be opened on it."""
    handler, _ = _recorder([httpx.Response(200, text="ok")])
    with pytest.raises(MeteredChannelError) as exc:
        await _channel(handler).open_session()
    assert "402" in str(exc.value)


async def test_open_session_rejects_a_charge_only_gateway():
    handler, _ = _recorder(
        [httpx.Response(402, headers=[("www-authenticate", _charge_header())])]
    )
    with pytest.raises(MeteredChannelError) as exc:
        await _channel(handler).open_session()
    assert "session" in str(exc.value)


async def test_open_session_ignores_an_undecodable_blob():
    handler, _ = _recorder(
        [
            httpx.Response(
                402,
                headers=[
                    (
                        "www-authenticate",
                        'Payment id="x", method="solana", intent="session", '
                        'request="!!!not-base64!!!"',
                    )
                ],
            )
        ]
    )
    with pytest.raises(MeteredChannelError):
        await _channel(handler).open_session()


def test_parse_session_challenges_tolerates_junk():
    assert parse_session_challenges([]) == []
    assert parse_session_challenges(["Basic realm=x"]) == []
    assert parse_session_challenges([_charge_header()]) == []


def test_verify_session_splits_rejects_splits_that_leave_no_fee():
    """100% in splits = zero remainder, and the PAYER refuses to sign that."""
    offer = parse_session_challenges(
        [
            _session_header(
                [
                    {"recipient": WORKER_PAYOUT, "shareBps": 8700},
                    {"recipient": TREASURY, "shareBps": 1300},
                ]
            )
        ]
    )[0]
    with pytest.raises(MeteredChannelError) as exc:
        verify_session_splits(offer, worker_address=WORKER_PAYOUT)
    assert "0bps" in str(exc.value)


def test_verify_session_splits_rejects_a_duplicated_recipient():
    offer = parse_session_challenges(
        [
            _session_header(
                [
                    {"recipient": WORKER_PAYOUT, "shareBps": 4350},
                    {"recipient": WORKER_PAYOUT, "shareBps": 4350},
                ]
            )
        ]
    )[0]
    with pytest.raises(MeteredChannelError) as exc:
        verify_session_splits(offer, worker_address=WORKER_PAYOUT)
    assert "more than once" in str(exc.value)


# ---------------------------------------------------------------------------
# 5.3: one tick per interval of job
# ---------------------------------------------------------------------------


def _directive(delivery_id: str = "em-tick-x-0", sequence: int = 1) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "deliveryId": delivery_id,
            "sessionId": CHANNEL,
            "amount": "10000",
            "currency": "USDC",
            "sequence": sequence,
            "expiresAt": 1893456000,
        },
    )


async def test_tick_posts_to_the_real_deliveries_path():
    handler, seen = _recorder([_directive()])
    channel = _channel(handler)
    channel.bind_channel(CHANNEL)

    await channel.tick()

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/__402/session/deliveries"
    assert body["sessionId"] == CHANNEL
    # base-unit INTEGER STRING: 30s of a $0.02/minute job = $0.01.
    assert body["amount"] == "10000"
    assert body["deliveryId"] == f"em-tick-{TASK_ID}-0"


async def test_ticks_carry_a_stable_sequence_so_a_retry_cannot_double_bill():
    handler, seen = _recorder([_directive()])
    channel = _channel(handler)
    channel.bind_channel(CHANNEL)

    await channel.tick()
    await channel.tick()

    ids = [json.loads(r.content)["deliveryId"] for r in seen]
    assert ids == [f"em-tick-{TASK_ID}-0", f"em-tick-{TASK_ID}-1"]
    assert tick_delivery_id(TASK_ID, 0) == ids[0]


async def test_tick_without_a_channel_refuses_before_any_request():
    handler, seen = _recorder([_directive()])
    with pytest.raises(MeteredChannelError):
        await _channel(handler).tick()
    assert seen == []


async def test_tick_surfaces_session_not_found():
    handler, _ = _recorder(
        [
            httpx.Response(
                404, json={"error": "session_not_found", "message": "no session"}
            )
        ]
    )
    channel = _channel(handler)
    channel.bind_channel(CHANNEL)

    with pytest.raises(SessionNotFound):
        await channel.tick()


async def test_try_tick_swallows_a_failure_so_the_job_goes_on():
    handler, _ = _recorder([httpx.Response(500, text="boom")])
    channel = _channel(handler)
    channel.bind_channel(CHANNEL)

    assert await channel.try_tick() is None
    assert channel.billed_base_units == 0


def test_bind_channel_rejects_a_hostile_channel_id():
    """The id is interpolated into a control-plane URL — validate it first."""
    handler, _ = _recorder([_directive()])
    with pytest.raises(MeteredChannelError):
        _channel(handler).bind_channel("../__402/verify")


async def test_the_meter_never_bills_past_the_bounty():
    """The cap is what the buyer agreed to: the last tick is trimmed, not skipped."""
    handler, seen = _recorder([_directive()])
    channel = _channel(handler, bounty_usd=Decimal("0.025"))  # 25_000 base units
    channel.bind_channel(CHANNEL)

    for _ in range(4):
        await channel.try_tick()

    amounts = [int(json.loads(r.content)["amount"]) for r in seen]
    assert amounts == [10000, 10000, 5000]  # third tick trimmed to the remainder
    assert channel.billed_base_units == 25_000
    assert channel.billed_usdc == Decimal("0.025")


async def test_billing_ticks_while_the_block_runs_and_stops_at_the_end():
    handler, seen = _recorder([_directive()])
    channel = _channel(handler, tick_seconds=0.01, price_per_unit=Decimal("60"))
    channel.bind_channel(CHANNEL)

    async with channel.billing():
        await asyncio.sleep(0.06)
    ticks_at_landing = channel.sequence

    assert ticks_at_landing >= 2, "the meter never ticked while the executor was flying"
    await asyncio.sleep(0.05)
    # Nothing keeps ticking after landing: the context manager owns the meter.
    assert channel.sequence == ticks_at_landing
    assert len(seen) == ticks_at_landing


async def test_billing_stops_on_its_own_once_the_bounty_is_exhausted():
    handler, _ = _recorder([_directive()])
    channel = _channel(
        handler,
        tick_seconds=0.01,
        price_per_unit=Decimal("60"),  # $0.01 per 10 ms tick
        bounty_usd=Decimal("0.02"),
    )
    channel.bind_channel(CHANNEL)

    async with channel.billing():
        await asyncio.sleep(0.15)

    assert channel.billed_base_units == 20_000
    assert channel.sequence == 2


# ---------------------------------------------------------------------------
# 5.4: landing — idle-close, then poll. There is nothing to close.
# ---------------------------------------------------------------------------


async def test_await_settlement_returns_the_signature_once_it_lands():
    handler, seen = _recorder(
        [
            httpx.Response(200, json={"settledSignature": None, "finalized": False}),
            httpx.Response(200, json={"settledSignature": "5xSig", "finalized": True}),
        ]
    )
    channel = _channel(handler)
    channel.bind_channel(CHANNEL)

    receipt = await channel.await_settlement(timeout=5.0, poll_interval=0.01)

    assert receipt.settled is True
    assert receipt.settled_signature == "5xSig"
    assert all(r.url.path == f"/__402/payment-channels/receipt/{CHANNEL}" for r in seen)


async def test_an_unsettled_receipt_is_never_reported_as_open():
    """200-with-nulls is also what a channel that never existed answers."""
    handler, _ = _recorder(
        [httpx.Response(200, json={"settledSignature": None, "finalized": False})]
    )
    channel = _channel(handler)
    channel.bind_channel(CHANNEL)

    receipt = await channel.await_settlement(timeout=0.03, poll_interval=0.01)

    assert receipt.state == "unknown"
    assert receipt.settled is False


def test_the_executor_never_grew_a_close_or_state_call():
    """pay.sh settles at idle-close and exposes no close in ANY release.

    Adding one here would revive the 2026-08-05 bug where the whole Solana
    money path called endpoints that never existed — the backend's client is
    pinned the same way (test_payshell_session_channels.py).
    """
    handler, _ = _recorder([_directive()])
    channel = _channel(handler)
    for dead in (
        "close_session",
        "close_channel",
        "session_close",
        "close_now",
        "settle_session",
        "settle_now",
        "get_session",
        "session_state",
    ):
        assert not hasattr(channel, dead), (
            f"MeteredChannel.{dead}() would call an endpoint absent from the "
            "pay-v0.27.0 router (start.rs:1478-1517) — read the module docstring "
            "before re-adding it."
        )


# ---------------------------------------------------------------------------
# Money arithmetic
# ---------------------------------------------------------------------------


def test_base_units_truncate_instead_of_rounding_up():
    """Never bill a payer more than the rate agreed."""
    assert usdc_to_base_units(Decimal("0.0000019")) == 1
    assert usdc_to_base_units(Decimal("0.1")) == 100_000


@pytest.mark.parametrize("bad", [("", 0), ("t", -1), ("   ", 2)])
def test_tick_delivery_id_validates_its_inputs(bad):
    with pytest.raises(MeteredChannelError):
        tick_delivery_id(*bad)
