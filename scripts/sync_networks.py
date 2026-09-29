"""Regenerate the SDK network snapshot from the backend registry.

Single source of truth: ``NETWORK_CONFIG``, whose literal lives in
``mcp_server/integrations/x402/network_registry.py`` (F6-4 moved the data out
of ``sdk_client.py``, which re-exports it). This script rewrites the region of
``em_plugin_sdk/networks.py`` from it, so the snapshot can never drift
silently again (SDK-37) — the same pattern the CI uses for skill.md
(``skill-sync-check``). ``tests/test_networks_sync.py`` enforces equality
on every pytest run.

Semantics mirrored from the backend:
  - ``has_escrow``   = the network has the AuthCaptureEscrow singleton
                       (``escrow`` key present).
  - ``has_operator`` = an EM PaymentOperator is deployed (``operator`` key).
  - Full escrow lifecycle support = ``escrow AND operator``
    (``sdk_client.py::has_escrow_support``) — exposed in the SDK as
    ``em_plugin_sdk.networks.has_escrow_support()``.
  - ``is_testnet``   = name carries a testnet suffix (the backend groups
    them under a ``# --- Testnets ---`` comment, without a flag).

Usage (from ``em-plugin-sdk/``)::

    python scripts/sync_networks.py            # rewrite the region
    python scripts/sync_networks.py --check    # exit 1 on drift (CI)
    python scripts/sync_networks.py --backend path/to/network_registry.py

Run ``ruff format .`` after a rewrite (house rule).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
DEFAULT_BACKEND = (
    SDK_ROOT.parent / "mcp_server" / "integrations" / "x402" / "network_registry.py"
)
NETWORKS_PY = SDK_ROOT / "em_plugin_sdk" / "networks.py"

BEGIN_MARKER = "# --- BEGIN GENERATED: NETWORKS (scripts/sync_networks.py) ---"
END_MARKER = "# --- END GENERATED: NETWORKS ---"

TESTNET_SUFFIXES = ("-sepolia", "-amoy", "-testnet", "-devnet")


def load_network_config(backend_path: Path) -> dict[str, Any]:
    """Parse ``NETWORK_CONFIG`` out of the backend module WITHOUT importing
    it (importing pulls the x402 package, which reads env vars at import
    time)."""
    tree = ast.parse(backend_path.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "NETWORK_CONFIG" and node.value is not None:
            return ast.literal_eval(node.value)
    raise SystemExit(f"NETWORK_CONFIG not found in {backend_path}")


def is_testnet(name: str) -> bool:
    return name.endswith(TESTNET_SUFFIXES)


def build_registry(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Backend NETWORK_CONFIG -> plain-data snapshot registry."""
    registry: dict[str, dict[str, Any]] = {}
    for name, cfg in config.items():
        registry[name] = {
            "chain_id": cfg.get("chain_id"),
            "network_type": cfg["network_type"],
            "tokens": [
                {
                    "symbol": symbol,
                    "address": token["address"],
                    "name": token["name"],
                    "decimals": token.get("decimals", 6),
                }
                for symbol, token in cfg["tokens"].items()
            ],
            "has_escrow": bool(cfg.get("escrow")),
            "has_operator": bool(cfg.get("operator")),
            "is_testnet": is_testnet(name),
        }
    return registry


def snapshot_registry() -> dict[str, dict[str, Any]]:
    """Current ``em_plugin_sdk.networks.NETWORKS`` as plain data (same shape
    as :func:`build_registry`) — used by ``--check`` and the equality test."""
    sys.path.insert(0, str(SDK_ROOT))
    try:
        from em_plugin_sdk.networks import NETWORKS
    finally:
        sys.path.pop(0)
    return {
        name: {
            "chain_id": net.chain_id,
            "network_type": net.network_type,
            "tokens": [
                {
                    "symbol": t.symbol,
                    "address": t.address,
                    "name": t.name,
                    "decimals": t.decimals,
                }
                for t in net.tokens
            ],
            "has_escrow": net.has_escrow,
            "has_operator": net.has_operator,
            "is_testnet": net.is_testnet,
        }
        for name, net in NETWORKS.items()
    }


def render_networks(registry: dict[str, dict[str, Any]]) -> str:
    """Render the generated region content (marker to marker, exclusive)."""
    lines = ["NETWORKS: dict[str, NetworkInfo] = {"]
    for name, net in registry.items():
        lines.append(f"    {name!r}: NetworkInfo(")
        lines.append(f"        name={name!r},")
        lines.append(f"        chain_id={net['chain_id']!r},")
        lines.append(f"        network_type={net['network_type']!r},")
        lines.append("        tokens=(")
        for token in net["tokens"]:
            args = [repr(token["symbol"]), repr(token["address"]), repr(token["name"])]
            if token["decimals"] != 6:
                args.append(f"decimals={token['decimals']}")
            lines.append(f"            TokenInfo({', '.join(args)}),")
        lines.append("        ),")
        for flag in ("has_escrow", "has_operator", "is_testnet"):
            if net[flag]:
                lines.append(f"        {flag}=True,")
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines)


def rewrite_networks_py(registry: dict[str, dict[str, Any]]) -> None:
    text = NETWORKS_PY.read_text(encoding="utf-8")
    try:
        head, rest = text.split(BEGIN_MARKER, 1)
        _, tail = rest.split(END_MARKER, 1)
    except ValueError:
        raise SystemExit(f"Markers not found in {NETWORKS_PY}")
    new_text = (
        head
        + BEGIN_MARKER
        + "\n"
        + render_networks(registry)
        + "\n"
        + END_MARKER
        + tail
    )
    NETWORKS_PY.write_text(new_text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the snapshot against the backend; exit 1 on drift",
    )
    args = parser.parse_args()

    if not args.backend.exists():
        raise SystemExit(f"Backend registry not found: {args.backend}")

    expected = build_registry(load_network_config(args.backend))

    if args.check:
        actual = snapshot_registry()
        if actual == expected:
            print(f"OK — networks.py matches NETWORK_CONFIG ({len(expected)} networks)")
            return 0
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            n for n in set(actual) & set(expected) if actual[n] != expected[n]
        )
        print("DRIFT — networks.py does not match NETWORK_CONFIG:")
        if missing:
            print(f"  missing in SDK: {missing}")
        if extra:
            print(f"  extra in SDK:   {extra}")
        if changed:
            print(f"  changed:        {changed}")
        print("Fix: python scripts/sync_networks.py && ruff format .")
        return 1

    rewrite_networks_py(expected)
    print(
        f"Rewrote {NETWORKS_PY.name} with {len(expected)} networks. "
        "Now run: ruff format ."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
