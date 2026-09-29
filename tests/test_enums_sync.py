"""Parity test: client enums vs backend ``mcp_server/models.py`` (F3-6).

Same pattern as ``test_networks_sync.py``: runs wherever the repo checkout
includes ``mcp_server/`` (CI does); skipped for a standalone SDK install.
The backend module is parsed with ``ast`` — never imported (it pulls
env-dependent imports at import time).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
BACKEND = SDK_ROOT.parent / "mcp_server" / "models.py"
SYNC_SCRIPT = SDK_ROOT / "scripts" / "sync_enums.py"

requires_backend = pytest.mark.skipif(
    not BACKEND.exists(),
    reason="mcp_server/ not present (standalone SDK install)",
)


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_enums", SYNC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # sys.modules registration is required BEFORE exec: the script defines
    # @dataclass classes, and dataclasses resolves their module by name.
    sys.modules["sync_enums"] = module
    spec.loader.exec_module(module)
    return module


@requires_backend
def test_enums_match_backend():
    """Every synced enum must equal backend members + documented client
    extras, name for name and value for value. On failure:
    python scripts/sync_enums.py && ruff format ."""
    import em_plugin_sdk.models as models

    sync = _load_sync_module()
    backend_enums = sync.load_backend_enums(BACKEND)
    for enum_name in sync.TARGETS["plugin"].enums:
        expected = sync.expected_members(backend_enums, enum_name)
        actual = {member.name: member.value for member in getattr(models, enum_name)}
        assert actual == expected, (
            f"{enum_name} drifted from mcp_server/models.py — regenerate "
            "with: python scripts/sync_enums.py && ruff format ."
        )


@requires_backend
def test_generated_region_matches_renderer():
    """The region TEXT must be exactly what the script renders (guards
    hand-edits inside the markers, not just semantic drift)."""
    sync = _load_sync_module()
    backend_enums = sync.load_backend_enums(BACKEND)
    target = sync.TARGETS["plugin"]
    assert sync.read_region(target.path) == sync.render_region(backend_enums, target)


@requires_backend
def test_client_extras_still_absent_from_backend():
    """The day the backend enum gains an extra's value (e.g. TaskStatus
    'assigning'), the CLIENT_EXTRAS entry must be retired — the script
    itself refuses to run, and this test names the cleanup."""
    sync = _load_sync_module()
    backend_enums = sync.load_backend_enums(BACKEND)
    for enum_name, extras in sync.CLIENT_EXTRAS.items():
        backend_values = set(backend_enums[enum_name].values())
        for extra in extras:
            assert extra.value not in backend_values, (
                f"Backend {enum_name} now declares '{extra.value}' — remove "
                "the CLIENT_EXTRAS entry in scripts/sync_enums.py and resync."
            )


def test_dispute_reason_not_generated():
    """DisputeReason mirrors migration 004, not models.py — it must stay
    hand-written (9 members) and never enter the generated region."""
    from em_plugin_sdk.models import DisputeReason

    assert len(DisputeReason) == 9
    assert DisputeReason.OTHER.value == "other"
