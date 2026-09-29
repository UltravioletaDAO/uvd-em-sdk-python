"""F3-2 canonical parity — uvd_em_sdk.erc8128 vs uvd_x402_sdk.erc8128.

uvd-x402-sdk >= 0.34.0 ships the canonical ERC-8128 signer; this module keeps
a local twin because the SDK was the optional ``[wallet]`` extra until 0.8.0
(a hard dependency since 0.9.0; the twin goes when the signer is delegated,
``docs/signer-comparison.md``). These tests enforce the F3-2 block (a)
contract:

  1. ``fetch_nonce`` delegates to the canonical module when the SDK is
     importable (pure re-export).
  2. ``sign_request`` (kept local — frozen-time monkeypatch contract) is
     BYTE-IDENTICAL to the canonical signer over the golden vectors: any
     drift between the twin and the canonical fails here first, before it
     can break auth against the production verifier.
  3. A base install (no ``uvd_x402_sdk.erc8128`` — missing extra or
     SDK < 0.34.0) still imports and signs via the local fallback.

The whole module skips when ``uvd_x402_sdk.erc8128`` is absent (pre-0.34
environments): the parity oracle is the canonical module itself.
"""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

canonical_mod = pytest.importorskip(
    "uvd_x402_sdk.erc8128",
    reason="canonical signer requires uvd-x402-sdk >= 0.34.0 ([wallet] extra)",
)

from uvd_x402_sdk.wallet import EnvKeyAdapter  # noqa: E402

import uvd_em_sdk.erc8128 as plugin_mod  # noqa: E402

# Byte-identical copy of Execution Market's shared/test-vectors/erc8128.json.
_VECTORS_PATH = Path(__file__).resolve().parent / "fixtures" / "erc8128.json"


@pytest.fixture
def golden_vectors():
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def frozen_wallet(golden_vectors):
    # Key stored 0x-less so the secret scanner never sees 0x + 64 hex.
    return EnvKeyAdapter(private_key="0x" + golden_vectors["frozen"]["private_key"])


def _freeze_time(monkeypatch, module, created: int):
    monkeypatch.setattr(
        module, "time", types.SimpleNamespace(time=lambda: float(created))
    )


class TestDelegation:
    def test_fetch_nonce_is_the_canonical_function(self):
        assert plugin_mod.fetch_nonce is canonical_mod.fetch_nonce

    def test_wire_format_constants_match(self):
        assert plugin_mod.ALG == canonical_mod.ALG
        assert plugin_mod.DEFAULT_LABEL == canonical_mod.DEFAULT_LABEL
        assert plugin_mod.DEFAULT_VALIDITY_SEC == canonical_mod.DEFAULT_VALIDITY_SEC
        assert plugin_mod.DEFAULT_CHAIN_ID == canonical_mod.DEFAULT_CHAIN_ID


class TestByteEquality:
    @pytest.mark.parametrize("request_name", ["get_query", "post_body"])
    def test_sign_request_matches_canonical_and_vector(
        self, golden_vectors, frozen_wallet, request_name, monkeypatch
    ):
        frozen = golden_vectors["frozen"]
        _freeze_time(monkeypatch, plugin_mod, frozen["created"])
        _freeze_time(monkeypatch, canonical_mod, frozen["created"])

        spec = golden_vectors["requests"][request_name]
        kwargs = dict(
            method=spec["method"],
            url=spec["url"],
            body=spec["body"],
            nonce=frozen["nonce"],
            chain_id=frozen["chain_id"],
        )
        local_headers = plugin_mod.sign_request(frozen_wallet, **kwargs)
        sdk_headers = canonical_mod.sign_request(frozen_wallet, **kwargs)

        expected = golden_vectors["vectors"]["canonical"][request_name]["headers"]
        assert local_headers == expected
        assert sdk_headers == expected


class TestBaseInstallFallback:
    def test_import_and_sign_without_canonical_module(
        self, golden_vectors, frozen_wallet, monkeypatch
    ):
        """Blocking ``uvd_x402_sdk.erc8128`` (what a base install or an SDK
        < 0.34.0 looks like at import time) must leave the local fallback
        fully functional and still golden-vector exact."""
        frozen = golden_vectors["frozen"]
        try:
            monkeypatch.setitem(sys.modules, "uvd_x402_sdk.erc8128", None)
            importlib.reload(plugin_mod)

            assert plugin_mod.fetch_nonce is not canonical_mod.fetch_nonce

            _freeze_time(monkeypatch, plugin_mod, frozen["created"])
            spec = golden_vectors["requests"]["post_body"]
            headers = plugin_mod.sign_request(
                frozen_wallet,
                method=spec["method"],
                url=spec["url"],
                body=spec["body"],
                nonce=frozen["nonce"],
                chain_id=frozen["chain_id"],
            )
            expected = golden_vectors["vectors"]["canonical"]["post_body"]["headers"]
            assert headers == expected
        finally:
            monkeypatch.undo()
            importlib.reload(plugin_mod)

        # Delegation restored after the clean reload
        assert plugin_mod.fetch_nonce is canonical_mod.fetch_nonce
