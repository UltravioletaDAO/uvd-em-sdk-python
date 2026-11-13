"""Equality test: the pinned FeedbackDelegate table vs Execution Market's record.

The record lives in Execution Market (private), in
``contracts/deployments/feedback-delegate.json``; this repository compares
against the fields the SDK pins (per network, chain id and v4 delegate),
vendored at a pinned commit in ``vendor/em/feedback-delegate.json``
(``vendor/em/SOURCE``; ``tests/test_vendor_em.py`` checks the hashes). No
skip: it runs in a standalone checkout, CI included, and
``tests/test_parity_runs.py`` fails if it skips or stops catching a drift.
"""

import importlib.util
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = SDK_ROOT / "scripts" / "sync_feedback_delegates.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_feedback_delegates", SYNC_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feedback_delegates_match_the_deployment_record():
    """Row by row and in order. This table is what the SDK lets an EIP-7702
    authorization point at: a row here that EM does not pin, or an address
    that differs, is a contract the SDK would let a wallet delegate to. On
    failure: python scripts/sync_feedback_delegates.py && ruff format ."""
    sync = _load_sync_module()
    expected = sync.build_table(sync.load_deployments())
    actual = sync.snapshot_table()
    assert list(actual.items()) == list(expected.items()), (
        "feedback_delegate.py drifted from the deployment record — regenerate "
        "with: python scripts/sync_feedback_delegates.py && ruff format ."
    )


def test_each_pinned_chain_resolves_to_its_row():
    """The lookup the check uses is by chain id: one row per chain, each
    reachable, keyed by its own network."""
    from uvd_em_sdk.feedback_delegate import FEEDBACK_DELEGATES, get_feedback_delegate

    sync = _load_sync_module()
    deployments = sync.load_deployments()
    assert set(FEEDBACK_DELEGATES) == set(deployments)
    for network, row in deployments.items():
        assert get_feedback_delegate(row["chain_id"]) == FEEDBACK_DELEGATES[network]
        assert FEEDBACK_DELEGATES[network].network == network
