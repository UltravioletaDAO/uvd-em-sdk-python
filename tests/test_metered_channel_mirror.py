"""The tick that pay.sh reserved, told to EM — the first link of the taxímetro.

Until this existed the executor's meter was invisible: ``MeteredChannel.tick``
reserved against the pay.sh gateway and emitted a log line, and pay.sh has no
push channel and no session-state endpoint, so NOTHING readable happened until
the channel settled — which is after the work is over. ``mpp_session_events``
stayed empty for the whole flight and the taxímetro had nothing to draw.

What these tests pin, in the order the money cares about:

1. **The mirror follows the reservation, never leads it.** EM is told about a
   unit pay.sh already accepted. A tick the gateway refused is not mirrored.
2. **The mirror can never cost a delivery.** Every failure mode of the mirror —
   no client, no evidence, an empty dict, a 422, a network error — leaves the
   reservation standing and the work running. That is the same contract
   ``try_tick`` already had, applied to the report instead of the charge.
3. **The payee travels.** EM refuses LOUDLY when the declared payee is not the
   task's assigned worker, so declaring it turns "the gateway pays one address
   and the task names another" into a first-tick failure instead of a
   settlement surprise.
4. **A failed mirror is COUNTED, not swallowed.** ``mirror_failures`` is what
   tells a flight report "the money moved and the meter did not".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal

import httpx
import pytest

from uvd_em_sdk.metered_channel import (
    ChannelClosedByServer,
    MeteredChannel,
    MeteredChannelError,
    tick_delivery_id,
)

GATEWAY = "http://payshell.test"
TASK_ID = "11111111-2222-4333-8444-555555555555"
WORKER_PAYOUT = "9vXF5Wn7DXvke63gJG2qxLby9pteqk1WB8TpuSKjiWoY"
TREASURY = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
CHANNEL = "CHNLxYvVA28MJP9PrFuDXccuoGXAx7jBacfLEkahyGsX"

PHOTO = {"photo_geo": {"url": "https://evidence.execution.market/frame-1.jpg"}}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _FakeTasks:
    """Stands in for ``EMClient.tasks`` — records what the mirror sent."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    async def tick(self, task_id, *, evidence, unit=None, payee=None, note=None):
        self.calls.append(
            {
                "task_id": task_id,
                "evidence": evidence,
                "unit": unit,
                "payee": payee,
                "note": note,
            }
        )
        if self.fail is not None:
            raise self.fail
        return {"success": True, "data": {"voucher_index": len(self.calls)}}


class _FakeEM:
    def __init__(self, **kwargs) -> None:
        self.tasks = _FakeTasks(**kwargs)


def _gateway(*, reserve_status: int = 200):
    """A pay.sh gateway that accepts (or refuses) a delivery reservation."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/__402/session/deliveries":
            if reserve_status != 200:
                return httpx.Response(reserve_status, json={"error": "nope"})
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "deliveryId": body["deliveryId"],
                    "sessionId": body["sessionId"],
                    "amount": body["amount"],
                    "currency": "USDC",
                    "sequence": len(seen),
                    "expiresAt": 1893456000,
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    return handler, seen


def _channel(handler, **kwargs) -> MeteredChannel:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=GATEWAY, timeout=5.0
    )
    kwargs.setdefault("tick_seconds", 30.0)
    kwargs.setdefault("unit", "minute")
    kwargs.setdefault("price_per_unit", Decimal("0.02"))
    channel = MeteredChannel(WORKER_PAYOUT, task_id=TASK_ID, http=client, **kwargs)
    channel.bind_channel(CHANNEL)
    return channel


# ---------------------------------------------------------------------------
# The mirror happens, and it says the right things
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reserved_tick_is_mirrored_to_em():
    handler, _ = _gateway()
    em = _FakeEM()
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    await channel.tick()

    assert len(em.tasks.calls) == 1, "the tick reserved but EM was never told"
    call = em.tasks.calls[0]
    assert call["task_id"] == TASK_ID
    assert call["evidence"] == PHOTO
    assert call["unit"] == "minute", "the unit EM meters must be the channel's"
    assert call["payee"] == WORKER_PAYOUT, (
        "the payee has to travel: EM cross-checks it against the assigned "
        "worker, which is the only free check that the gateway and the task "
        "agree on who gets paid"
    )
    assert channel.mirrored_ticks == 1
    assert channel.mirror_failures == 0


@pytest.mark.asyncio
async def test_every_tick_of_a_flight_is_mirrored():
    handler, _ = _gateway()
    em = _FakeEM()
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    for _ in range(4):
        await channel.tick()

    assert channel.sequence == 4
    assert len(em.tasks.calls) == 4, (
        "mpp_session_events stays empty unless EVERY tick is mirrored — one "
        "mirrored tick out of four is a meter that lies about the pace"
    )
    assert channel.mirrored_ticks == 4


@pytest.mark.asyncio
async def test_an_async_evidence_provider_is_awaited():
    handler, _ = _gateway()
    em = _FakeEM()

    async def camera():
        return PHOTO

    channel = _channel(handler, em_client=em, evidence_provider=camera)
    await channel.tick()

    assert em.tasks.calls[0]["evidence"] == PHOTO, (
        "the thing producing evidence is usually a camera, so an async "
        "provider must be awaited rather than mirrored as a coroutine object"
    )


# ---------------------------------------------------------------------------
# The mirror NEVER costs a delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_mirror_does_not_undo_the_reservation():
    handler, seen = _gateway()
    em = _FakeEM(fail=RuntimeError("EM unreachable"))
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    directive = await channel.tick()

    assert directive is not None, "the reservation stands — the money moved"
    assert channel.sequence == 1
    assert channel.billed_base_units > 0
    assert channel.mirror_failures == 1, (
        "a mirror that fails silently is worse than one that fails: the flight "
        "report has to be able to say the meter did not advance"
    )
    assert channel.mirrored_ticks == 0
    assert len(seen) == 1, "the gateway was called exactly once"


@pytest.mark.asyncio
async def test_the_platform_closing_the_channel_DOES_stop_the_reservations():
    """The one mirror failure that must stop the work.

    Every other failure here means "EM did not hear us" and the reservation
    rightly stands. A 409 CHANNEL_EXPIRED means something else: the platform's
    channel ceiling (EM_SOLANA_SESSION_MAX_OPEN / _MAX_AGE_S) took this
    channel's slot and EM will not count another tick on it — ever.

    Treating that like a transient mirror failure is what turns the ceiling
    into a money bug: a tick reserves against pay.sh BEFORE it reports, so the
    executor would keep booking deliveries the platform stopped counting, and
    the receipt would say less than what settles. The "espejo perdido" shape,
    caused by the ceiling itself.
    """
    handler, seen = _gateway()
    em = _FakeEM(
        fail=RuntimeError('409 {"code": "CHANNEL_EXPIRED", "closed_reason": "max_age"}')
    )
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    with pytest.raises(ChannelClosedByServer):
        await channel.tick()

    assert channel.channel_closed_by_server is True
    reserved_once = len([r for r in seen if r.url.path.endswith("/deliveries")])
    assert reserved_once == 1, "the tick that learned it was over still reserved"

    # And the NEXT tick does not reserve at all: the guard runs before the
    # reservation, so no delivery is booked that EM would then refuse.
    with pytest.raises(ChannelClosedByServer):
        await channel.tick()
    still_once = len([r for r in seen if r.url.path.endswith("/deliveries")])
    assert still_once == 1, (
        "a second delivery was booked after EM said the channel was over: "
        "money moving that the platform is no longer counting"
    )


@pytest.mark.asyncio
async def test_an_ordinary_mirror_failure_still_does_not_stop_the_work():
    """The control for the test above.

    Without it, "stop on a failed mirror" and "stop on THIS failed mirror"
    are indistinguishable — and the first one would break every flight whose
    network blipped.
    """
    handler, seen = _gateway()
    em = _FakeEM(fail=RuntimeError("500 upstream hiccup"))
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    await channel.tick()
    await channel.tick()

    assert channel.channel_closed_by_server is False
    assert channel.sequence == 2
    assert channel.mirror_failures == 2
    assert len([r for r in seen if r.url.path.endswith("/deliveries")]) == 2


@pytest.mark.asyncio
async def test_an_evidence_provider_that_raises_does_not_stop_the_tick():
    handler, _ = _gateway()
    em = _FakeEM()

    def broken_camera():
        raise OSError("the camera is not answering")

    channel = _channel(handler, em_client=em, evidence_provider=broken_camera)

    await channel.tick()

    assert channel.sequence == 1
    assert em.tasks.calls == [], "no evidence, no mirror — and no crash either"
    assert channel.mirror_failures == 1


@pytest.mark.asyncio
async def test_empty_evidence_is_never_mirrored():
    handler, _ = _gateway()
    em = _FakeEM()
    channel = _channel(handler, em_client=em, evidence_provider=dict)

    await channel.tick()

    assert em.tasks.calls == [], (
        "an empty dict is an absence, and the server refuses it for the same "
        "reason — a tick with no evidence is a claim, not a receipt"
    )
    assert channel.mirror_failures == 1
    assert channel.sequence == 1


@pytest.mark.asyncio
async def test_without_a_client_the_channel_still_bills():
    handler, _ = _gateway()
    channel = _channel(handler, evidence_provider=lambda: PHOTO)

    await channel.tick()

    assert channel.sequence == 1, "no EM client is not an error, it is a mode"
    assert channel.mirrored_ticks == 0
    assert channel.mirror_failures == 0, (
        "not configuring the mirror is not a failure of the mirror — counting "
        "it as one would drown the real failures in the flight report"
    )


@pytest.mark.asyncio
async def test_a_refused_reservation_is_not_mirrored():
    handler, _ = _gateway(reserve_status=500)
    em = _FakeEM()
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    with pytest.raises(MeteredChannelError):
        await channel.tick()

    assert em.tasks.calls == [], (
        "the mirror REPORTS a reservation that happened; reporting one the "
        "gateway refused would put a unit in the meter that nobody is paying"
    )
    assert channel.mirrored_ticks == 0


@pytest.mark.asyncio
async def test_try_tick_still_swallows_everything():
    """The outer contract is unchanged: metering never stops the work."""
    handler, _ = _gateway(reserve_status=500)
    em = _FakeEM()
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    assert await channel.try_tick() is None
    assert em.tasks.calls == []


# ---------------------------------------------------------------------------
# 5. Closing the meter must not cut a tick in half
#
# `billing()` used to end with a bare `meter.cancel()`, and a tick is not one
# await: it reserves against pay.sh, raises the tally, and only then reports to
# EM. A cancellation landing in that window left the delivery reserved and EM
# never told — the meter reading LESS than the money.
#
# Found by the CI job for a simulated drone flight on its FIRST run
# (`assert 15 == 16`: 16 reserved, 15 mirrored) and not reproducible locally.
# That detector depends on the scheduler; these do not — each one injects the
# interruption at the exact point, so they fail every run without the fix.
#
# Three windows, and only the first two are visible from inside the SDK:
#   * the report   — cancelled between the tally and the mirror;
#   * the closing  — `billing()` exiting while a tick is under way;
#   * the RESERVATION — cancelled inside the POST, where the SDK's own counters
#     never learn anything happened. Only the gateway can witness that one.
# ---------------------------------------------------------------------------


def _slow_gateway(delay: float):
    """A gateway that BOOKS the delivery and only then answers, slowly.

    The delay is the whole point: it holds the reservation POST open so a
    cancellation can land inside it, which is the window where pay.sh keeps a
    delivery the executor never finds out about.
    """
    booked: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        booked.append(body["deliveryId"])  # pay.sh has it on its books HERE
        await asyncio.sleep(delay)
        return httpx.Response(
            200,
            json={
                "deliveryId": body["deliveryId"],
                "sessionId": body["sessionId"],
                "amount": body["amount"],
                "currency": "USDC",
                "sequence": len(booked),
                "expiresAt": 1893456000,
            },
        )

    return handler, booked


@pytest.mark.asyncio
async def test_a_cancellation_between_the_tally_and_the_mirror_cannot_split_them():
    """Cancel EXACTLY in the window. Deterministic, not timing-dependent."""
    handler, _ = _gateway()
    em = _FakeEM()
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    in_the_window = asyncio.Event()
    may_finish = asyncio.Event()
    real_mirror = channel._mirror_tick_to_em

    async def mirror_that_parks_in_the_window(**kwargs):
        # Reached only AFTER the local tally has been bumped: this is the exact
        # window the race lives in.
        in_the_window.set()
        await may_finish.wait()
        return await real_mirror(**kwargs)

    channel._mirror_tick_to_em = mirror_that_parks_in_the_window

    ticking = asyncio.create_task(channel.tick())
    await in_the_window.wait()

    assert channel.sequence == 1, "the tally must already be bumped here"
    assert em.tasks.calls == [], "the mirror must not have run yet"

    ticking.cancel()
    may_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await ticking

    assert len(em.tasks.calls) == 1, (
        "the reservation was counted and the mirror was dropped by the "
        "cancellation: EM shows less money than pay.sh reserved, which is the "
        "taxímetro running slower than the meter"
    )
    assert channel.mirrored_ticks == channel.sequence, (
        "reserved and mirrored must come out equal even when the work is "
        "cancelled mid-tick"
    )


@pytest.mark.asyncio
async def test_the_inverse_never_happens_either_a_mirror_with_no_reservation():
    """The other direction: the meter may never show MORE than was charged.

    Moving the mirror ahead of the tally would fix the defect above and open
    this one. Whatever the fix, `mirrored_ticks` may never exceed `sequence` —
    and a mirror that FAILS must still leave the reservation counted, because
    the money moved either way.
    """
    handler, _ = _gateway()
    em = _FakeEM(fail=RuntimeError("EM unreachable"))
    channel = _channel(handler, em_client=em, evidence_provider=lambda: PHOTO)

    await channel.tick()

    assert channel.sequence == 1, "the reservation happened; it must be counted"
    assert channel.mirrored_ticks == 0
    assert channel.mirror_failures == 1
    assert channel.mirrored_ticks <= channel.sequence, (
        "a mirror without a reservation behind it would draw a meter running "
        "FASTER than the money"
    )


@pytest.mark.asyncio
async def test_closing_the_meter_never_cuts_a_tick_in_half():
    """A tick under way when billing() exits still reaches EM."""
    handler, _ = _gateway()

    class _SlowTasks(_FakeTasks):
        """EM answers slowly — the window a cancellation lands in."""

        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()

        async def tick(self, task_id, **kwargs):
            self.entered.set()
            await asyncio.sleep(0.05)
            return await super().tick(task_id, **kwargs)

    em = _FakeEM.__new__(_FakeEM)
    em.tasks = _SlowTasks()

    channel = _channel(
        handler,
        em_client=em,
        evidence_provider=lambda: PHOTO,
        tick_seconds=0.02,
    )

    async with channel.billing():
        # Leave the block while a mirror is under way — exactly what the end of
        # the work does when it finishes between two ticks.
        await asyncio.wait_for(em.tasks.entered.wait(), timeout=2.0)

    assert channel.sequence > 0, "nothing was billed — the race is not reproduced"
    assert channel.mirrored_ticks == channel.sequence, (
        f"{channel.sequence} tick(s) reserved against pay.sh and only "
        f"{channel.mirrored_ticks} reached EM: closing the meter cut a tick in "
        f"half, so the taxímetro draws less than what was charged"
    )
    assert len(em.tasks.calls) == channel.sequence
    assert channel.unmirrored_ticks == 0


@pytest.mark.asyncio
async def test_a_reservation_the_sdk_never_learned_about_cannot_be_lost():
    """The third window — and the SDK's own counters are blind to it.

    Cancel inside the reservation POST and pay.sh keeps a delivery whose
    existence never reaches `sequence`. Every assertion of the form
    `mirrored == sequence` PASSES here while the money moved, because both
    sides are zero. So the witness has to be the GATEWAY: what it booked, not
    what the executor thinks it booked.
    """
    handler, booked = _slow_gateway(delay=0.3)
    em = _FakeEM()
    channel = _channel(
        handler, em_client=em, evidence_provider=lambda: PHOTO, tick_seconds=0.02
    )

    async with channel.billing():
        # Exit while the POST is still open — the reservation is on pay.sh's
        # books and its answer has not come back yet.
        await asyncio.sleep(0.08)

    assert booked, "the reservation never reached the gateway — not reproduced"
    assert len(em.tasks.calls) == len(booked), (
        f"pay.sh booked {len(booked)} delivery(ies) and EM was told about "
        f"{len(em.tasks.calls)}: a reservation cut off mid-POST is money the "
        f"executor never even counted, which no counter of its own can reveal"
    )
    assert channel.sequence == len(booked), (
        "the local tally has to end up agreeing with the gateway, or the "
        "flight report is describing a different flight"
    )


@pytest.mark.asyncio
async def test_a_mirror_lost_to_the_network_is_resent_when_the_meter_closes():
    """The second road to the same state, and a shield does not cover it.

    Nothing is cancelled here: EM simply refuses while the work runs. Without
    the debt ledger those ticks were a warning in a log and nothing else.
    """
    handler, _ = _gateway()
    em = _FakeEM(fail=RuntimeError("EM unreachable"))
    channel = _channel(
        handler, em_client=em, evidence_provider=lambda: PHOTO, tick_seconds=0.02
    )

    async with channel.billing():
        await asyncio.sleep(0.05)
        assert channel.mirror_failures > 0, "EM was supposed to be refusing"
        # EM comes back before the work ends.
        em.tasks.fail = None

    assert channel.sequence > 0
    # Behaviour first, new counters second: this assertion has to be able to go
    # red on the OLD module, where a mirror lost to the network was a warning
    # and nothing more.
    assert channel.mirrored_ticks == channel.sequence, (
        "every reservation pay.sh accepted has to end up in EM, whether it got "
        "there on the first attempt or on the resend"
    )
    # NOT `len(em.tasks.calls)`: the double records the ATTEMPT before it
    # raises, so a tick that failed once and was resent once shows up twice
    # there. What must come out equal is what EM ACCEPTED.
    assert channel.recovered_mirrors > 0, "the owed ticks were never resent"
    assert channel.unmirrored_ticks == 0, (
        "EM answered again before the meter closed, so nothing should still be owed"
    )


@pytest.mark.asyncio
async def test_what_cannot_be_resent_is_named_not_merely_missing(caplog):
    """A discrepancy that survives has to be recoverable BY NAME.

    `mirror_failures` counts attempts; it cannot say WHICH deliveries are
    missing, and a number that does not add up is not something anyone can act
    on. The deliveryId is deterministic and the reservation behind it is
    idempotent, so naming it is the difference between a lost tick and a
    replayable one.
    """
    handler, _ = _gateway()
    em = _FakeEM(fail=RuntimeError("EM is down for good"))
    channel = _channel(
        handler, em_client=em, evidence_provider=lambda: PHOTO, tick_seconds=0.02
    )

    with caplog.at_level(logging.ERROR, logger="em.metered.channel"):
        async with channel.billing():
            await asyncio.sleep(0.05)

    assert channel.sequence > 0
    assert channel.unmirrored_ticks == channel.sequence, (
        "EM never came back, so every reserved tick is still owed — and the "
        "debt is what makes it visible; mirror_failures alone counts attempts"
    )
    named = [
        r.getMessage() for r in caplog.records if "never recorded" in r.getMessage()
    ]
    assert named, "the surviving discrepancy was never reported at all"
    assert tick_delivery_id(TASK_ID, 0) in named[0], (
        "the leftovers have to be named by deliveryId: that id is what makes "
        "them replayable, and replaying it is safe because it is idempotent"
    )


@pytest.mark.asyncio
async def test_a_clean_cycle_mirrors_one_to_one_and_never_twice():
    """The negative. A fix that mirrors the same reservation twice is worse.

    The ledger resends what EM has not taken; if it ever resent something EM
    already took, the meter would run FASTER than the money — the defect the
    inverse test guards against, arriving through the repair instead of
    through the order of operations.
    """
    handler, seen = _gateway()
    em = _FakeEM()
    channel = _channel(
        handler, em_client=em, evidence_provider=lambda: PHOTO, tick_seconds=0.02
    )

    async with channel.billing():
        await asyncio.sleep(0.09)

    reservations = [r for r in seen if r.url.path.endswith("/session/deliveries")]
    assert len(reservations) >= 2, "the meter barely ran — nothing is being pinned"
    assert len(em.tasks.calls) == len(reservations), (
        "one mirror per reservation, no more and no less"
    )
    assert channel.mirrored_ticks == channel.sequence
    assert channel.unmirrored_ticks == 0
    assert channel.recovered_mirrors == 0, (
        "nothing failed, so the drain had nothing to do — a resend here would "
        "mean a tick got mirrored twice"
    )


@pytest.mark.asyncio
async def test_the_meter_never_reserves_faster_than_its_interval():
    """The interval is the money, so it may not be built out of a timeout.

    Making the meter interruptible is one `wait_for(stop.wait(), tick_seconds)`
    away, and on Windows that spelling is a much worse money bug than the
    missing mirror: measured on py3.11.4 / ProactorEventLoop, a 10 ms TIMEOUT
    fires in ~0.05 ms while `sleep(0.01)` takes its ~11 ms, so the meter
    reserves hundreds of deliveries a second against the buyer's cap.

    The interval here is deliberately BELOW the Windows clock resolution, which
    is the only place that spelling misbehaves — above ~15 ms a timeout is
    accurate and this would pass with the broken code in place (measured: at 50
    ms it does). So on Linux this passes for the honest reason and on Windows it
    is a real guard. The bound is loose on purpose: a slow runner may tick
    LESS, never 100x more.
    """
    interval = 0.01
    handler, booked = _gateway()
    channel = _channel(handler, tick_seconds=interval)

    started = time.perf_counter()
    async with channel.billing():
        await asyncio.sleep(0.2)
    elapsed = time.perf_counter() - started

    reservations = [r for r in booked if r.url.path.endswith("/session/deliveries")]
    ceiling = int(elapsed / interval) + 3
    assert len(reservations) <= ceiling, (
        f"{len(reservations)} deliveries reserved in {elapsed:.2f}s at a "
        f"{interval * 1000:.0f} ms interval (at most {ceiling} are possible): the "
        f"meter is billing off "
        f"something other than the clock, and it is the buyer's cap it burns"
    )
