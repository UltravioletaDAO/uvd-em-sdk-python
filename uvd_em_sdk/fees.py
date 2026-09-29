"""Fee calculator for Execution Market — pure computation, no server calls.

Mirrors the PRODUCTION fee model: a **flat 13% (1300 bps)** deducted
on-chain at release by the ``StaticFeeCalculator(1300bps)`` — the
authoritative source of truth (see ``payment_dispatcher.FASE5_FEE_BPS``).
Credit-card convention: the bounty IS the lock amount; the fee is deducted
from it, not added on top. Agent pays $0.10 → worker gets $0.087, treasury
gets $0.013.

The rate is the same for every task category. ``category`` parameters are
kept for backward compatibility and echoed in the breakdown, but they do
not affect the rate.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

from pydantic import BaseModel

from .models import TaskCategory

# Flat platform fee: 1300 basis points (13%) — StaticFeeCalculator on-chain.
FEE_BPS = 1300

# Headroom for the signed ``maxFeeBps`` in escrow authorizations: the x402r
# protocol fee (up to 5%, BackTrack controlled) is absorbed from the treasury
# share, so signatures allow up to 1800 bps while the operator charges 1300.
MAX_FEE_BPS = 1800

FEE_RATE = Decimal(FEE_BPS) / Decimal(10000)

# USDC has 6 decimals — all on-chain amounts are quantized to this precision.
_USDC = Decimal("0.000001")


class FeeBreakdown(BaseModel):
    """Result of a fee calculation."""

    gross_amount: float
    fee_rate: float
    fee_rate_percent: float
    fee_amount: float
    worker_amount: float
    category: str


def calculate_fee(
    bounty_usd: float, category: str | TaskCategory = "simple_action"
) -> FeeBreakdown:
    """Calculate the flat 13% fee breakdown for a given bounty.

    Mirrors the on-chain split exactly: the fee is floored at USDC
    precision (6 decimals), matching the StaticFeeCalculator's integer
    division ``amount * 1300 / 10000``.

    Args:
        bounty_usd: Gross bounty amount the agent posts (= lock amount).
        category: Task category — echoed in the breakdown only; the rate
            is flat 13% for every category.

    Returns:
        FeeBreakdown with exact amounts.

    Example::

        >>> fee = calculate_fee(0.10)
        >>> fee.worker_amount  # 0.087
        >>> fee.fee_amount     # 0.013
    """
    if bounty_usd <= 0:
        raise ValueError("Bounty must be positive")

    cat_str = category.value if isinstance(category, TaskCategory) else category

    bounty = Decimal(str(bounty_usd))
    fee = (bounty * FEE_RATE).quantize(_USDC, rounding=ROUND_DOWN)
    worker = bounty - fee

    return FeeBreakdown(
        gross_amount=float(bounty),
        fee_rate=float(FEE_RATE),
        fee_rate_percent=float(FEE_RATE * 100),
        fee_amount=float(fee),
        worker_amount=float(worker),
        category=cat_str,
    )


def calculate_reverse_fee(
    desired_worker_amount: float, category: str | TaskCategory = "simple_action"
) -> FeeBreakdown:
    """Calculate the bounty needed for a worker to receive a specific amount.

    Uses the same ceiling formula as the backend's ``compute_lock_amount``
    (``lock = ceil(bounty * 10000 / (10000 - fee_bps))``) so the worker is
    guaranteed to net at least ``desired_worker_amount`` after the on-chain
    split.

    Args:
        desired_worker_amount: What the worker should receive after fees.
        category: Task category — echoed in the breakdown only.

    Example::

        >>> fee = calculate_reverse_fee(10.00)
        >>> fee.gross_amount   # 11.494253 (the bounty to post)
        >>> fee.worker_amount  # >= 10.00
    """
    if desired_worker_amount <= 0:
        raise ValueError("Desired amount must be positive")

    desired = Decimal(str(desired_worker_amount))
    bounty = (desired * Decimal(10000) / Decimal(10000 - FEE_BPS)).quantize(
        _USDC, rounding=ROUND_CEILING
    )

    return calculate_fee(float(bounty), category)


def get_fee_rate(category: str | TaskCategory = "simple_action") -> float:
    """Get the fee rate (flat 0.13 for every category)."""
    return float(FEE_RATE)
