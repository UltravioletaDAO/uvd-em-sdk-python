# Changelog

## [Unreleased]

`__version__` is 0.9.1: what a git install of this tree reports, so it is not
taken for the 0.9.0 on PyPI. Not released.

### Fixed

- `workers.register()` sends the client's Supabase JWT when it does not sign:
  `POST /workers/register` requires a credential (ERC-8128 or a Supabase JWT,
  as the API reference says), and a JWT-only client sent none and got 401. A
  signing client still sends the ERC-8128 signature alone. Ported from
  `em-plugin-sdk`, where it landed after this repository was split off.

### Changed

- `uvd-x402-sdk[wallet]>=0.93.0,<0.95` (was `<0.94`): Execution Market pins
  0.94.0 and consumes this package in the same environment. From 0.93.0 to
  0.94.0, `escrow_signing`, `wallet` and `erc8128` are byte-identical and
  `erc8004.supports_relayed_feedback` is identical by AST; the suite passes
  on both.
- `scripts/vendor_em.py --check` also compares `vendor/em/` with what EM's
  newer `--upstream` ref (default `origin/main`) yields, and exits 1 when a
  re-vendor would change it. It checked only SOURCE's own commit, so it stayed
  green however far EM moved. A newer commit that yields the same vendored
  bytes is green; a ref that does not contain SOURCE's commit (a clone that
  was not fetched) is reported `STALE` and exits 1 instead of proposing an
  older commit. The output names files and commits, never EM data.

- `scripts/sync_networks.py` and `scripts/sync_enums.py` read `vendor/em/`
  instead of `../mcp_server/`. Their `--backend` option is removed, and
  `sync_enums.py` no longer writes the monorepo's `sdk/python` (absent here:
  the script would crash on it).

### Added

- `scripts/upstream-check.plist.template`: the periodic `--check` as a macOS
  LaunchAgent (bring this checkout to main, refusing local edits; fetch the
  authorised EM clone's main; then check). Not installed, no secret: it uses
  the clones' own git credentials.
- `vendor/em/`: the Execution Market data the parity tests compare the SDK
  against — the `NETWORK_CONFIG` fields `networks.py` mirrors and the four
  synced enums, not the backend modules — written by `scripts/vendor_em.py`
  from a pinned EM commit. `vendor/em/SOURCE` records the commit and the
  sha256 of each upstream module and each vendored file;
  `tests/test_vendor_em.py` checks them. `tests/test_networks_sync.py` and
  `tests/test_enums_sync.py` used to skip outside the monorepo and now always
  run.

## [0.9.0] — 2026-09-29

First PyPI release. `uvd-em-sdk` replaces `em-plugin-sdk`, formerly
distributed from the Execution Market monorepo (never released on PyPI; its
last version there was 0.8.0).

### Changed

- **Renamed.** Distribution `em-plugin-sdk` -> `uvd-em-sdk`; import package
  `em_plugin_sdk` -> `uvd_em_sdk`; `User-Agent` `uvd-em-sdk/<version>`. The
  API is 0.8.0's. Migrate with `pip uninstall em-plugin-sdk`,
  `pip install uvd-em-sdk`, and `from em_plugin_sdk ...` -> `from uvd_em_sdk ...`.
- **`uvd-x402-sdk[wallet]>=0.93.0,<0.94` is a hard dependency.** It was the
  optional `[wallet]` extra, pinned `==0.91.2`. The `[wallet]` extra is gone:
  the base install signs. `import uvd_em_sdk` raises `ImportError` over an
  `uvd-x402-sdk` older than 0.93.0.
- **`uvd_em_sdk.escrow_signing` re-exports `uvd_x402_sdk.escrow_signing`**
  instead of carrying its own copy of the escrow EIP-3009 builder. Every name
  of the SDK's stable API is re-exported as the same object; the names the
  0.8.0 copy defined still import. Behaviour that comes with the SDK's
  implementation:
  1. a USDC EIP-712 domain other than the on-chain-verified one
     (`VERIFIED_USDC_DOMAINS`) is refused;
  2. the amount is converted with `to_base_units`: `0.3 - 0.1` -> `200000`
     (the copy truncated it to `199999`), and a digit below one base unit
     raises;
  3. a `payment_info_typehash` other than `AuthCaptureEscrow`'s is refused;
  4. the typed data passed to the wallet carries `primaryType`;
  5. a chain not in `VERIFIED_USDC_DOMAINS` is signed with a logged warning.

  `build_escrow_pre_auth` gains an optional ninth parameter,
  `delegation_resolver` (EIP-7702); the eight positional parameters are
  unchanged. The clock and salt are read in `uvd_x402_sdk.escrow_signing`:
  tests that freeze them patch that module.

### Added

- `tests/fixtures/`: byte-identical copies of Execution Market's
  `shared/test-vectors/escrow-preauth.json` and `erc8128.json`, so the whole
  suite runs outside the monorepo.
- `tests/test_escrow_signing_reexport.py` and `tests/test_dependencies.py`.
