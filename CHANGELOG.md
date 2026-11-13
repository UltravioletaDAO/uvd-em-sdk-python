# Changelog

## [0.11.0] — unreleased

An EIP-7702 authorization gives the contract it names the power to act as the
signing account. `submit_relayed_rating` carries one the first time a wallet
rates on a chain, and the contract comes from Execution Market's `prepare`
answer. This release checks that answer against a table the SDK ships, and
lets the caller say the account's owner accepted the delegation, which
Execution Market's backend is set to require with an authorization.

### Added

- `submit_relayed_rating(..., delegation_acknowledged=False)`, keyword-only.
  It is sent as `"delegation_acknowledged": true` only together with an
  `authorization` and only when it is exactly `True`; without an
  authorization it is never sent. Set it only when the account's owner was
  shown `prepared["delegation_notice"]` (which contract, which chain, and that
  the account stays delegated after the rating) and accepted. A backend that
  requires it refuses an authorization without it: 422
  `relay_delegation_not_acknowledged`, raised as `EMValidationError` with the
  code in `details["detail"]["code"]`. A backend that does not require it
  ignores the field, so this release works with both.
- `uvd_em_sdk.feedback_delegate`, exported from `uvd_em_sdk`:
  `FEEDBACK_DELEGATES` (network -> `FeedbackDelegate(network, chain_id,
  address)`), `get_feedback_delegate(chain_id)` and
  `verify_feedback_delegate(prepared, authorization=None)`. The table is
  Execution Market's deployment record of the v4 FeedbackDelegate, per
  network its chain id and address and nothing else: base (8453), ethereum
  (1), polygon (137), arbitrum (42161), optimism (10), celo (42220), bsc (56),
  monad (143), arc (5042) and base-sepolia (84532). Call
  `verify_feedback_delegate(prepared)` before signing the authorization and
  sign over the `chain_id` and `address` it returns.
- `EMDelegateNotPinnedError` (an `EMError`): `code` names the check that
  failed (`prepared_invalid`, `chain_id_invalid`, `chain_not_pinned`,
  `network_mismatch`, `delegate_not_pinned`, `authorization_invalid`,
  `authorization_wrong_chain`, `authorization_wrong_delegate`,
  `authorization_wrong_signer`), `details` what was compared, and
  `status_code` is `None`.
- `vendor/em/feedback-delegate.json` and `scripts/sync_feedback_delegates.py`,
  which generates the table from it. `scripts/vendor_em.py` vendors the file
  from EM's `contracts/deployments/feedback-delegate.json` and refuses a record
  of another version than v4, a row that is not a positive integer chain id
  and an address written `0x` and 40 hex digits, and two rows on one chain.
  `--check` compares it like the other vendored files.

### Changed

- `submit_relayed_rating` with an `authorization` checks, before sending
  anything: `prepared["chain_id"]` is a positive `int` (absent, `None` and 0
  are refused: 0 is EIP-7702's wildcard, valid on every chain); the SDK pins a
  delegate on that chain; `prepared["network"]`, when present, is that chain's
  network; `prepared["delegate"]` is the pinned delegate; the authorization's
  `chainId` is exactly that chain id as an `int`; its `address` is the pinned
  delegate; and its signature (`yParity`, `r`, `s`) recovers, over exactly
  that chain id, that address and its `nonce`, to `prepared["rater_wallet"]`.
  The last one catches a wallet that signed other values than the dict
  carries: chainId 0 under a dict that says this chain would hand whoever
  reads the body a delegation valid on every chain. Addresses are compared
  without case and must be written `0x` and 40 hex digits; `chain_id` and
  `y_parity` are read when `chainId` and `yParity` are absent, as Execution
  Market reads them. A mismatch raises `EMDelegateNotPinnedError` and no
  request is made. What is sent is the authorization as JSON writes it, the
  same plain copy that was checked. This refuses a submit that 0.10.0 sent:
  an authorization with a hand-built `prepared` that lacks `chain_id`,
  `delegate` or `rater_wallet`, for a delegate the table does not name, or
  whose signature is not the rater's over those values. A delegate Execution
  Market deploys later is refused until a release carries it. Without an
  `authorization` nothing is checked. The signature is recovered with
  `eth-keys`, `rlp` and `eth-utils`, which `eth-account` (required through
  `uvd-x402-sdk[wallet]`) depends on: no new requirement.
- `prepare_relayed_rating`'s docstring points at `verify_feedback_delegate`
  instead of a block-explorer check, which it gave because no list of
  deployments was public.
- `vendor/em/SOURCE` keeps its pin (the same commit and date): `networks.json`,
  `enums.json` and `retry.json` are byte-identical, and it gains
  `feedback-delegate.json` and the sha256 of EM's record at that commit.

### Fixed

- README: the rater-signed reputation example called
  `submit_relayed_rating(prep, signature)` without `task_id`, `direction` and
  `score`, which are required (a `TypeError`), and signed with
  `wallet.sign_message(prep["digest"])`, an EIP-191 `personal_sign` over the
  digest's text: the double envelope `prepare_relayed_rating`'s docstring
  warns against, which fails as `relay_bad_signature`. It now passes the
  three fields, says how to sign the digest, and shows the first-rating flow.

### Tests

- `tests/test_reputation.py`: the exact body with and without an
  authorization; `delegation_acknowledged` never sent without an authorization
  nor for a truthy value that is not `True`; a foreign delegate, another
  chain, chain id 0, absent or a bool, a malformed address, and a signature
  that is not the rater's over the values the dict carries (signed for chain
  0, another key, nonce or address, malformed `r`/`s`/`yParity`) refused
  with no request; EM's 422 raised with its code. The authorization fixture
  is a known answer from eth-account's `Account.sign_authorization` with a
  throwaway key.
- `tests/test_feedback_delegates_sync.py` holds the table to
  `vendor/em/feedback-delegate.json`, and `tests/test_parity_runs.py` proves
  it runs in a standalone checkout and fails on a drift on either side.
- `tests/test_vendor_em.py`: the extractor copies only network, chain id and
  address, refuses the records above, and `--check` flags a redeployed
  delegate (not a changed note). The public-log guard reads a commit as a
  whole word: "feedback" holds the hex run "feedbac".

## [0.10.0] — 2026-10-12

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
