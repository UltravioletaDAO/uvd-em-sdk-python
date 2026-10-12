# Changelog

## [0.10.0] — unreleased

0.9.2 (`uvd-x402-sdk<0.98`) was never released: its ceiling already excluded
0.98.0 and 0.99.0, published the same day. Its changes are in this release.

### Changed

- `uvd-x402-sdk[wallet]>=0.93.0,<1.0` (was `<0.95` in 0.9.1). Execution Market
  consumes this package in the same environment, and its uvd-x402-sdk pin has
  to move to the latest release within its maximum lag of 3 days. A ceiling at
  the next minor expired faster than this package could ship: 0.98.0 and
  0.99.0 were published the same day as 0.97.0. The ceiling now keeps the next
  major out (1.0.0 and its pre-releases) and no longer keeps each new 0.x minor
  out until this suite has run against it. A later 0.x minor installs without
  this suite having run against it. CI runs the suite on the newest release
  under the ceiling, and on each published minor from 0.95.0 to 0.99.0 (see
  Tests), but only when CI runs: on pull requests and by hand, with no
  schedule.
- In the published wheels, the uvd-x402-sdk modules this package uses
  (`escrow_signing`, `escrow_contracts`, `erc7702`, `networks`, `wallet`,
  `erc8128`, `erc8004`, `exceptions`) are byte-identical from 0.97.0 to 0.99.0.
  The package `__init__` they load changed in 0.98.0, and so did the `client`,
  `config`, `dx402`, `receipts` and `response` modules it imports. 0.98.0 and
  0.99.0 also change the FastAPI integration and add `bounded_json` and
  `durable`. The suite passes on 0.97.0, 0.98.0 and 0.99.0.
- From 0.94.0 to 0.99.0, `wallet`, `erc8128` and `erc8004` are byte-identical
  in the published wheels. The `escrow_signing` this package re-exports signs
  the same bytes for a config its tables agree with, and refuses more before
  signing. From 0.96.0 it refuses a config whose escrow, token collector,
  USDC, operator or fee ceiling contradicts the SDK's escrow tables. From
  0.97.0 it also refuses a chain without a row in every table, an
  unregistered network name, and tier windows or an `authorizationExpiry`
  above their ceilings.
- With uvd-x402-sdk 0.97.0 or later, nothing is signed for Execution Market's
  escrow testnets, `base-sepolia` (84532) and `ethereum-sepolia` (11155111),
  even with the SDK's own escrow addresses. The SDK has no rows for them in
  `ESCROW_OPERATORS` or `ESCROW_MAX_FEE_BPS`, and does not register those
  network names. Below 0.97.0 they are signed with a warning. This was already
  true of 0.9.2's range, which included 0.97.0.

### Added

- `scripts/vendor_em.py --check --public-log`: the same two answers, for a
  log anyone can read. It prints the vendored file names and SOURCE's commit,
  both already public, and nothing learnt from the EM clone: not the newer
  ref's commit or date, not an error message or a traceback (a `SyntaxError`
  quotes the EM line it failed on). A failure is named by its exception class.
  A git failure whose cause is the network (`TRANSPORT_CAUSES`: DNS, no
  route, a timeout, a reset or closed connection, the remote hanging up) is
  `NETWORK` and exits 75, not 1: the check did not finish, which is not drift
  (a drift found before it still exits 1). An access cause (`ACCESS_CAUSES`:
  the key refused, the repository not found, the host key) wins over it and
  stays an error, and so does any answer from the server (`remote error:`,
  such as "not our ref" for a SOURCE commit EM no longer has).
- `.github/workflows/em-drift.yml` (the repository's, not in the sdist):
  `--check --public-log` every Monday and on demand, against a fresh clone of
  EM's `main` read with a read-only deploy key (secret `EM_DEPLOY_KEY` of the
  `em-drift` environment, `main` only; every run is red until it is set). A
  failed clone is named by a fixed line, not by git's words. Red is drift, a
  stale pin, a missing or refused key, an answer from GitHub, or an unknown
  failure; a network failure, in the clone or during the check, is a
  `::warning::` and a `NETWORK —` line in the summary, and the run stays green
  unless a drift was found before it. The drift used to
  surface only when someone ran `--check` by hand. Setup and how to read a
  run: `docs/em-drift-check.md`.

### Tests

- `.github/workflows/ci.yml` gains the job `uvd-x402-sdk`, which runs the
  suite offline on each published minor from 0.95.0 to 0.99.0, one per
  matrix leg, on Python 3.11.
- `tests/test_dependencies.py` pins the new ceiling: 0.95.0 to 0.99.x and a
  later 0.x minor are in, and 1.0.0 and its pre-releases are out.
- `test_5_an_unverified_chain_is_signed_with_a_warning` checks the refusal
  (`ValueError`, nothing signed) when the installed SDK is 0.96.0 or later,
  and the warning below it.
- `test_6_em_testnets_are_refused_from_0_97` signs EM's two escrow testnets
  with the SDK's own addresses: refused with nothing signed from 0.97.0,
  signed below it.

## [0.9.1] — 2026-10-11

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
