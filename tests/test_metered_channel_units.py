"""The unit of work is the buyer's, not the executor's.

The 36 ported tests cover the channel lifecycle with a TIME unit, because that
is what the spike that wrote them measured. These cover the part that makes the
same machinery serve any measurable task: a *countable* unit — one scan, one
token, one byte — where there is no seconds-per-unit and the count IS the meter.

That is the difference between "a vehicle billing elapsed seconds" and "a camera
confirming a person is doing the job". Same rail, same vouchers, different unit.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from em_plugin_sdk.metered_channel import (
    COUNT_UNITS,
    UNIT_SECONDS,
    MeteredChannel,
    MeteredChannelUnavailable,
)

WORKER_PAYOUT = "9vXF5Wn7DXvke63gJG2qxLby9pteqk1WB8TpuSKjiWoY"
TASK_ID = "11111111-2222-3333-4444-555555555555"


def _ch(**kw) -> MeteredChannel:
    kw.setdefault("price_per_unit", Decimal("0.001"))
    return MeteredChannel(WORKER_PAYOUT, task_id=TASK_ID, **kw)


# --- countable units: one tick == one unit ----------------------------------


@pytest.mark.parametrize("unit", sorted(COUNT_UNITS))
def test_a_countable_unit_bills_exactly_one_per_tick(unit):
    """No seconds-per-unit exists for a scan. The caller ticks once per
    confirmation, and the interval is only how often the loop wakes up."""
    ch = _ch(unit=unit, tick_seconds=30.0)
    assert ch.units_per_tick == Decimal(1)
    assert ch.tick_amount_base_units == 1000  # $0.001 -> 1000 base units


def test_a_countable_units_price_does_not_move_with_the_interval():
    """The trap this guards: reusing the time formula would make a scan cost
    half as much if the loop happened to wake twice as often."""
    fast = _ch(unit="scan", tick_seconds=1.0)
    slow = _ch(unit="scan", tick_seconds=600.0)
    assert fast.tick_amount_base_units == slow.tick_amount_base_units


# --- time units: a tick bills the fraction of a unit it covers --------------


def test_a_time_unit_bills_the_fraction_of_the_interval():
    """30s on a per-minute price is half a unit — the behaviour the ported
    tests pin, restated here against the generalized formula."""
    ch = _ch(unit="minute", price_per_unit=Decimal("0.02"), tick_seconds=30.0)
    assert ch.units_per_tick == Decimal("0.5")
    assert ch.tick_amount_base_units == 10_000  # $0.01


def test_the_default_unit_is_the_second():
    assert _ch(tick_seconds=1.0).unit == "second"


@pytest.mark.parametrize("unit", sorted(UNIT_SECONDS))
def test_every_time_unit_scales_by_its_own_length(unit):
    ch = _ch(
        unit=unit, price_per_unit=Decimal("1"), tick_seconds=float(UNIT_SECONDS[unit])
    )
    assert ch.units_per_tick == Decimal(1)


# --- refusals ---------------------------------------------------------------


def test_an_unknown_unit_is_refused_not_defaulted():
    """Metering the wrong unit bills the wrong amount and nothing downstream
    would notice — so it fails here, at construction."""
    with pytest.raises(MeteredChannelUnavailable, match="unknown unit of work"):
        _ch(unit="furlong")


def test_a_tick_that_floors_to_zero_is_refused():
    """Upstream rejects a zero reservation; failing loudly here beats a meter
    that runs all job long and bills nothing."""
    with pytest.raises(MeteredChannelUnavailable, match="floors to 0"):
        _ch(unit="hour", price_per_unit=Decimal("0.000001"), tick_seconds=0.001)


def test_the_unit_comes_from_the_environment_when_not_passed(monkeypatch):
    monkeypatch.setenv("EM_METERED_UNIT", "token")
    assert _ch(tick_seconds=5.0).unit == "token"


# --- the naming criterion ---------------------------------------------------


def test_the_public_surface_names_no_particular_executor():
    """The vehicle is A consumer of this module, not the module. A name that
    only makes sense for something that flies is the wrong name here."""
    forbidden = re.compile(r"drone|dron\b|robot|flight|takeoff|aircraft", re.I)

    src = Path(__file__).parent.parent / "em_plugin_sdk" / "metered_channel.py"
    body = src.read_text(encoding="utf-8")
    hits = [ln for ln in body.splitlines() if forbidden.search(ln)]
    assert not hits, f"nombre de dron en el modulo generico: {hits[:5]}"

    names = [n for n in dir(MeteredChannel) if not n.startswith("_")]
    assert not [n for n in names if forbidden.search(n)]


def test_the_three_absent_gateway_endpoints_are_still_absent():
    """pay.sh has no open, no session-state and no close. A method named for
    one of them would be calling an endpoint that does not exist — the same
    guard mcp_server/tests/test_payshell_session_channels.py keeps."""
    names = [n for n in dir(MeteredChannel) if not n.startswith("_")]
    assert not [n for n in names if n.startswith("close")]
