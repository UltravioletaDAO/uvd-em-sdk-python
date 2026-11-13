"""Regenerate the SDK's pinned FeedbackDelegate table from Execution Market's.

Single source of truth: Execution Market's deployment record
``contracts/deployments/feedback-delegate.json``. EM is private, so this
repository carries, per network, the chain id and the v4 delegate's address
and nothing else, vendored at a pinned commit in
``vendor/em/feedback-delegate.json`` by ``scripts/vendor_em.py``
(``vendor/em/SOURCE``). This script rewrites the generated region of
``uvd_em_sdk/feedback_delegate.py`` from that copy;
``tests/test_feedback_delegates_sync.py`` enforces equality on every pytest
run, CI included.

That table is what ``verify_feedback_delegate`` lets an EIP-7702
authorization point at: a row added here is a contract the SDK will let a
wallet delegate its account to. Review a change of it as such.

Usage (from the repository root)::

    python scripts/sync_feedback_delegates.py            # rewrite the region
    python scripts/sync_feedback_delegates.py --check    # exit 1 on drift

Run ``ruff format .`` after a rewrite (house rule).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
VENDORED = SDK_ROOT / "vendor" / "em" / "feedback-delegate.json"
MODULE_PY = SDK_ROOT / "uvd_em_sdk" / "feedback_delegate.py"

BEGIN_MARKER = (
    "# --- BEGIN GENERATED: FEEDBACK DELEGATES (scripts/sync_feedback_delegates.py) ---"
)
END_MARKER = "# --- END GENERATED: FEEDBACK DELEGATES ---"


def load_deployments(path: Path = VENDORED) -> dict[str, dict[str, Any]]:
    """The vendored projection: network -> ``{"chain_id", "address"}``."""
    return json.loads(path.read_text(encoding="utf-8"))


def build_table(deployments: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Vendored deployments -> the plain-data table the module must hold."""
    return {
        network: {
            "network": network,
            "chain_id": row["chain_id"],
            "address": row["address"],
        }
        for network, row in deployments.items()
    }


def snapshot_table() -> dict[str, dict[str, Any]]:
    """Current ``uvd_em_sdk.feedback_delegate.FEEDBACK_DELEGATES`` as plain
    data (same shape as :func:`build_table`), in the module's order."""
    sys.path.insert(0, str(SDK_ROOT))
    try:
        from uvd_em_sdk.feedback_delegate import FEEDBACK_DELEGATES
    finally:
        sys.path.pop(0)
    return {
        key: {"network": d.network, "chain_id": d.chain_id, "address": d.address}
        for key, d in FEEDBACK_DELEGATES.items()
    }


def render_table(table: dict[str, dict[str, Any]]) -> str:
    """Render the generated region content (marker to marker, exclusive)."""
    lines = ["FEEDBACK_DELEGATES: dict[str, FeedbackDelegate] = {"]
    for network, row in table.items():
        lines.append(f"    {network!r}: FeedbackDelegate(")
        lines.append(f"        network={row['network']!r},")
        lines.append(f"        chain_id={row['chain_id']!r},")
        lines.append(f"        address={row['address']!r},")
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines)


def rewrite_module(table: dict[str, dict[str, Any]]) -> None:
    text = MODULE_PY.read_text(encoding="utf-8")
    try:
        head, rest = text.split(BEGIN_MARKER, 1)
        _, tail = rest.split(END_MARKER, 1)
    except ValueError:
        raise SystemExit(f"Markers not found in {MODULE_PY}")
    new_text = (
        head + BEGIN_MARKER + "\n" + render_table(table) + "\n" + END_MARKER + tail
    )
    MODULE_PY.write_text(new_text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the table against vendor/em/; exit 1 on drift",
    )
    args = parser.parse_args()

    expected = build_table(load_deployments())

    if args.check:
        actual = snapshot_table()
        if list(actual.items()) == list(expected.items()):
            print(
                f"OK — feedback_delegate.py matches the deployment record "
                f"({len(expected)} networks)"
            )
            return 0
        print("DRIFT — feedback_delegate.py does not match the deployment record")
        print("Fix: python scripts/sync_feedback_delegates.py && ruff format .")
        return 1

    rewrite_module(expected)
    print(
        f"Rewrote {MODULE_PY.name} with {len(expected)} networks. "
        "Now run: ruff format ."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
