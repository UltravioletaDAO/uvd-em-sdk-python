"""Equality test: networks.py snapshot vs backend NETWORK_CONFIG (SDK-37).

The backend dict lives in Execution Market (private), in
``mcp_server/integrations/x402/network_registry.py``; this repository compares
against the fields the SDK reads, vendored at a pinned commit in
``vendor/em/networks.json`` (``vendor/em/SOURCE``; ``tests/test_vendor_em.py``
checks the hashes). No skip: it runs in a standalone checkout, CI included.
"""

import importlib.util
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = SDK_ROOT / "scripts" / "sync_networks.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_networks", SYNC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_networks_matches_backend():
    """The generated snapshot must equal the vendored backend registry field
    by field. On failure: python scripts/sync_networks.py && ruff format ."""
    sync = _load_sync_module()
    expected = sync.build_registry(sync.load_network_config())
    actual = sync.snapshot_registry()
    assert actual == expected, (
        "networks.py drifted from NETWORK_CONFIG — regenerate with: "
        "python scripts/sync_networks.py && ruff format ."
    )


def test_escrow_rule_matches_backend_and():
    """has_escrow_support == (escrow AND operator), per network, exactly as
    the backend computes it."""
    from uvd_em_sdk.networks import NETWORKS, has_escrow_support

    sync = _load_sync_module()
    config = sync.load_network_config()
    assert set(config) == set(NETWORKS)
    for name, cfg in config.items():
        backend_rule = bool(cfg.get("escrow") and cfg.get("operator"))
        assert has_escrow_support(name) == backend_rule, name
