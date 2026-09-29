"""Equality test: networks.py snapshot vs backend NETWORK_CONFIG (SDK-37).

Runs wherever the repo checkout includes ``mcp_server/`` (CI does); skipped
for a standalone SDK install. The backend dict is parsed with ``ast`` from the
module that holds its literal (``x402/network_registry.py`` since F6-4;
``sdk_client.py`` re-exports it) — the backend is never imported, since
importing pulls the x402 package, which reads env vars at import time.
"""

import importlib.util
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
BACKEND = (
    SDK_ROOT.parent / "mcp_server" / "integrations" / "x402" / "network_registry.py"
)
SYNC_SCRIPT = SDK_ROOT / "scripts" / "sync_networks.py"

requires_backend = pytest.mark.skipif(
    not BACKEND.exists(),
    reason="mcp_server/ not present (standalone SDK install)",
)


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_networks", SYNC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_backend
def test_networks_matches_backend():
    """The generated snapshot must equal the backend registry field by
    field. On failure: python scripts/sync_networks.py && ruff format ."""
    sync = _load_sync_module()
    expected = sync.build_registry(sync.load_network_config(BACKEND))
    actual = sync.snapshot_registry()
    assert actual == expected, (
        "networks.py drifted from NETWORK_CONFIG — regenerate with: "
        "python scripts/sync_networks.py && ruff format ."
    )


@requires_backend
def test_escrow_rule_matches_backend_and():
    """has_escrow_support == (escrow AND operator), per network, exactly as
    the backend computes it."""
    from em_plugin_sdk.networks import NETWORKS, has_escrow_support

    sync = _load_sync_module()
    config = sync.load_network_config(BACKEND)
    assert set(config) == set(NETWORKS)
    for name, cfg in config.items():
        backend_rule = bool(cfg.get("escrow") and cfg.get("operator"))
        assert has_escrow_support(name) == backend_rule, name
