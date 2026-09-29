# Changelog

## [0.9.0] — unreleased

First release under this name. `uvd-em-sdk` replaces `em-plugin-sdk`, whose
last release was 0.8.0, published from the Execution Market monorepo.

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
