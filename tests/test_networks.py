"""Tests for the static network/token registry."""

from em_plugin_sdk.networks import (
    NETWORKS,
    get_network,
    get_enabled_networks,
    get_supported_tokens,
    is_valid_pair,
    get_chain_id,
    get_escrow_networks,
    has_escrow_support,
    DEFAULT_NETWORK,
    DEFAULT_TOKEN,
)


class TestNetworkRegistry:
    def test_all_11_enabled_networks(self):
        # 10 EVM (arc since EM-2a) + Solana — mirrors the backend's
        # EM_ENABLED_NETWORKS code default.
        enabled = get_enabled_networks()
        names = {n.name for n in enabled}
        assert "base" in names
        assert "ethereum" in names
        assert "arc" in names
        assert "solana" in names
        assert len(enabled) == 11

    def test_base_has_usdc_and_eurc(self):
        net = get_network("base")
        assert net is not None
        assert "USDC" in net.token_symbols
        assert "EURC" in net.token_symbols

    def test_solana_is_svm(self):
        net = get_network("solana")
        assert net is not None
        assert net.network_type == "svm"
        assert net.chain_id is None
        assert not net.has_escrow

    def test_get_chain_id(self):
        assert get_chain_id("base") == 8453
        assert get_chain_id("ethereum") == 1
        assert get_chain_id("polygon") == 137
        assert get_chain_id("nonexistent") is None

    def test_is_valid_pair(self):
        assert is_valid_pair("base", "USDC") is True
        assert is_valid_pair("base", "EURC") is True
        assert is_valid_pair("base", "PYUSD") is False
        assert is_valid_pair("ethereum", "PYUSD") is True
        assert is_valid_pair("nonexistent", "USDC") is False

    def test_get_supported_tokens(self):
        tokens = get_supported_tokens("ethereum")
        assert "USDC" in tokens
        assert "EURC" in tokens
        assert "PYUSD" in tokens
        assert "AUSD" in tokens
        assert get_supported_tokens("nonexistent") == []

    def test_escrow_networks(self):
        escrow = get_escrow_networks()
        assert "base" in escrow
        assert "ethereum" in escrow
        assert "solana" not in escrow

    def test_18_networks_total(self):
        """SDK-37: full registry — 13 EVM mainnets + solana + 4 testnets.

        Arc made it 13 EVM mainnets (R5 of the Arc plan, 2026-09-23). EM-1
        gave it the generation-D escrow singleton and EM-2a EM's operator, so
        it runs the full escrow lifecycle.
        """
        assert len(NETWORKS) == 18
        for name in ("hyperevm", "unichain", "scroll"):
            assert name in NETWORKS
            assert not NETWORKS[name].has_escrow
        assert NETWORKS["arc"].has_escrow is True
        assert NETWORKS["arc"].has_operator is True
        assert has_escrow_support("arc") is True

    def test_testnet_flags(self):
        testnets = {n for n, net in NETWORKS.items() if net.is_testnet}
        assert testnets == {
            "base-sepolia",
            "ethereum-sepolia",
            "polygon-amoy",
            "arbitrum-sepolia",
        }

    def test_escrow_support_requires_escrow_and_operator(self):
        """SDK-37: has_escrow_support = escrow AND operator (backend rule).
        base-sepolia carries the escrow singleton but no operator — it can
        NOT run the full Fase 2 lifecycle."""
        sepolia = NETWORKS["base-sepolia"]
        assert sepolia.has_escrow is True
        assert sepolia.has_operator is False
        assert has_escrow_support("base-sepolia") is False
        assert has_escrow_support("base") is True
        assert has_escrow_support("solana") is False
        assert has_escrow_support("nonexistent") is False
        # 12 networks carry the escrow singleton (arc since EM-1); only the 10
        # mainnets with a deployed operator (arc since EM-2a) support the full
        # lifecycle.
        assert sum(1 for n in NETWORKS.values() if n.has_escrow) == 12
        assert len(get_escrow_networks()) == 10
        assert "arc" in get_escrow_networks()
        assert set(get_escrow_networks()) == {
            n for n in NETWORKS if has_escrow_support(n)
        }

    def test_defaults(self):
        assert DEFAULT_NETWORK == "base"
        assert DEFAULT_TOKEN == "USDC"

    def test_operator_flag(self):
        net = get_network("base")
        assert net is not None
        assert net.has_operator is True
        sol = get_network("solana")
        assert sol is not None
        assert sol.has_operator is False

    def test_token_address_format(self):
        """EVM tokens start with 0x, Solana tokens are base58."""
        base_usdc = get_network("base").get_token("USDC")
        assert base_usdc.address.startswith("0x")
        sol_usdc = get_network("solana").get_token("USDC")
        assert not sol_usdc.address.startswith("0x")
