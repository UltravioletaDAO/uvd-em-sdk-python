# Signer comparison: Execution Market's `OwsEM8128Client` vs `uvd-x402-sdk`

What Execution Market's OWS ERC-8128 signer does that `uvd_x402_sdk.OWSWalletAdapter`
plus `uvd_x402_sdk.erc8128` do not, and what an `OwsEM8128Client` in `uvd_em_sdk`
would need in order to delegate to the SDK instead of carrying its own signer. The
document changes no code.

## Sources

| Side | Revision | Files |
|---|---|---|
| **EM**: Execution Market's Python signer | `UltravioletaDAO/execution-market` @ `b657976de` | `sdk/python/execution_market/_signer.py` (360 lines), `sdk/python/tests/test_signer.py` |
| **SDK**: `uvd-x402-sdk` | 0.93.0 (tag `v0.93.0`; the published wheel is byte-identical to `src/` for every file cited) | `src/uvd_x402_sdk/wallet.py`, `src/uvd_x402_sdk/erc8128/signer.py`, `src/uvd_x402_sdk/erc8128/core.py`, `pyproject.toml` |
| **This repo** | this branch | `uvd_em_sdk/client.py`, `uvd_em_sdk/erc8128.py`, `uvd_em_sdk/retry.py` |

A reference `_signer.py:123` means EM's file at that line. `wallet.py:767` and
`erc8128/signer.py:105` mean the SDK's files under `src/uvd_x402_sdk/`.

## What `_signer.py` is

`OwsEM8128Client(wallet_name, wallet_address, chain_id=8453, api_url=...)` is a
small async HTTP client with `get()` and `post()`. It signs every request per
ERC-8128 by shelling out to the OWS **CLI** (`ows sign message`), so the key stays in
the OWS vault (`_signer.py:1-34`, `:70-117`). Next to it are two helpers,
`task_fingerprint` (`:279-315`) and `with_backoff` (`:323-360`), and an exception,
`OwsSignError` (`:66-67`). EM re-exports all four from `execution_market`
(`sdk/python/execution_market/__init__.py:50-67`).

## Side by side

| # | Concern | EM `_signer.py` | SDK 0.93.0 | Difference |
|---|---|---|---|---|
| 1 | How OWS is reached | Subprocess to the npm CLI: `ows sign message --chain base --wallet <name> --message <hex> --encoding hex --json` (`:129-143`). Binary from `$OWS_BIN`, default `~/.npm-global/bin/ows` (`:52`). | The OWS **Python binding** (module `ows`, `open-wallet-standard` 1.4.2), imported when the adapter is built (`wallet.py:736-744`); `sign_message` calls `ows.sign_message(...)` in-process (`wallet.py:767-776`). | The SDK has no CLI transport, and EM has no binding transport. |
| 2 | Whether the OWS package is installed | Nothing to install in Python. The CLI comes from npm (`:146-147`). | Not installed by `uvd-x402-sdk[wallet]`. The only declaration is in the SDK's `dev` extra: `open-wallet-standard==1.4.2`, limited to Python < 3.14 on Linux x86_64/aarch64 and macOS x86_64/arm64 (`pyproject.toml:149`). | A `uvd-em-sdk` install cannot build an `OWSWalletAdapter` without a separate `pip install open-wallet-standard`. |
| 3 | Chain handed to OWS | Always `base` (`:134-135`), whatever the `chain_id`. | `eip155:<chain id of network>` (`wallet.py:751-756`); typed data is signed on its own `domain.chainId` (`wallet.py:792`). | The EIP-191 bytes do not depend on it. An OWS policy that decides by chain does. |
| 4 | Passphrase and vault | None; the CLI resolves its own. | `passphrase=` or `$OWS_PASSPHRASE`, and `vault_path=` (`wallet.py:711-757`). | SDK only. |
| 5 | Wallet address | Passed in, format-checked (`0x` + 42 chars) and never compared with the vault (`:105-108`). | Read from the vault on every `get_address()` (`wallet.py:759-765`); `sign_request` calls it once per request (`erc8128/signer.py:167`). | EM can put an address in the keyid that is not the one signing. The server rejects that request, but the client does not say why. |
| 6 | 65-byte signature check | `len(sig) != 65` raises `OwsSignError`, with a hint to upgrade the CLI to >= 1.2.4 (missing-`v` bug) (`:158-164`). | `_ows_signature` raises `ValueError` on any length other than 65, and turns a `v` of 0/1 into 27/28 (`wallet.py:667-679`). | Same check; different error type and hint. Only the SDK normalizes `v`. |
| 7 | Malformed OWS output | JSON decode error or missing key raises `OwsSignError` (`:154-157`). | `result["signature"]` is read directly (`wallet.py:675`), so a `KeyError` propagates. | Different error surface. |
| 8 | Error types | `OwsSignError(RuntimeError)` for a missing CLI (`:144-148`), a non-zero exit, with stderr (`:149-152`), bad output and bad length. | `ImportError` when the adapter is built without `ows` (`wallet.py:740-744`), `ValueError` on length, and the binding's own exceptions otherwise. | Callers that write `except OwsSignError` would need a mapping. |
| 9 | Recovery check after signing | None (EIP-191 only). | Typed data must recover to the wallet over the digest `eth-account` computes (`wallet.py:634`, called at `:801`). EIP-191 `sign_message` has no recovery check. | SDK only, and not on the path ERC-8128 uses. |
| 10 | Nonce | `GET {api_url}/api/v1/auth/erc8128/nonce` before every call, `httpx` default timeout, `raise_for_status` (`:188-191`). | `fetch_nonce(api_base, timeout=10.0)` builds the same URL (`erc8128/signer.py:226-243`); `fetch_nonce_sync` also exists (`:246-288`). | Equivalent. |
| 11 | Covered components | `@method @authority @path`, `@query` when there is a query string, `content-digest` when the body is **truthy** (`:195-204`). | `select_covered` uses the same list (`erc8128/core.py:154-169`). The default `content_digest="body-present"` also digests an empty-string body; `"body-truthy"` reproduces EM (`erc8128/signer.py:143-146`, `:184`). | Differs only for `body == ""`, which `post()` never sends (`json.dumps` output is never empty). |
| 12 | `@authority` | Verbatim `netloc` (`:193`, `:223`). | Normalized per RFC 9421: lowercased, default port dropped (`erc8128/signer.py:170-177`, `erc8128/core.py:118`). | Same for every live URL. Differs for an explicit `:443` or an upper-case host. |
| 13 | `@path` | `parsed.path` (`:225`). | `parsed.path or "/"` (`erc8128/signer.py:178`). | Differs only for an empty path. |
| 14 | Parameters and keyid | `created`, `expires = created + 300`, `nonce`, `keyid` lowercased, `alg="eip191"`, in that order (`:167-179`, `:206-216`). | `canonical_params` (`erc8128/core.py:172`) and `canonical_keyid` (`:256-260`); validity capped at 300 s (`erc8128/signer.py:182`). | Same bytes. Both are pinned to the F3-1 golden vectors: EM in `sdk/python/tests/test_signer.py:170-231`, the SDK with `erc8128.f3-1.json`, which it ships. |
| 15 | Which requests are signed | Every `get()` and `post()` (`:246-269`). | `sign_request` signs whatever it is given. In this repo, `EMClient` signs non-GET requests by default and a GET with `sign=True` (`uvd_em_sdk/client.py:191-193`). | A wrapper has to choose. |
| 16 | HTTP surface | `post()` and `get()` return `r.json()` with **no status check** (`:259-261`, `:267-269`); timeouts are 180 s and 30 s. `extra_headers` are merged **after** the auth headers (`:258`), so an extra `Signature` would replace the signature. | The SDK has no client; it returns headers only. `uvd_em_sdk.EMClient` maps statuses to typed errors (`client.py:251`) and re-signs on each retry (`client.py:194-210`, `retry.py:19-87`). | `EMClient` already does more. |
| 17 | `api_url` | Strips a trailing `/` and `/api/v1` (`:110-117`). | `fetch_nonce` appends `/api/v1/...` to an origin (`erc8128/signer.py:237`). | A wrapper passes the origin. |
| 18 | **Idempotency key** | `task_fingerprint(body)` (`:279-315`): SHA-256 hex of the sorted-keys JSON of 9 identity fields (title, instructions, location hint/lat/lng, bounty, deadline hours, evidence, payment network), with strings stripped and lowercased. It is the `X-Idempotency-Key` for `POST /api/v1/tasks`. | Nothing. `uvd_em_sdk` re-sends a static `X-Idempotency-Key` on every retry (`retry.py:39-41`) but has no helper that computes one. | **Missing on the SDK side and in `uvd_em_sdk`.** |
| 19 | **Backoff** | `with_backoff(fn, tries=4, base=0.5)` (`:323-360`): exponential sleep plus `U(0, base)` jitter. It retries **any exception** except an `httpx.HTTPStatusError` 4xx other than 429. `post()` and `get()` never raise on status (row 16), so in practice it retries network errors, the nonce fetch, `OwsSignError` (a missing CLI too) and a non-JSON response body. It does **not** retry a JSON 5xx. | Nothing in the SDK's ERC-8128 path. `uvd_em_sdk.retry.request_with_retry` (`retry.py:19-87`) retries per HTTP attempt on 429/5xx and connection or timeout errors, honours `Retry-After`, adds ±25 % jitter, refuses to retry a response that carries a tx hash, and re-signs every attempt. | **Different semantics**: `with_backoff` retries a whole call on exceptions, `request_with_retry` retries HTTP attempts on status. |
| 20 | Blocking | `subprocess.run` runs inside the async `_sign_headers` (`:131`, called at `:232`), so it blocks the event loop while the CLI runs. | The binding call is synchronous too, in-process (`wallet.py:769`). | Neither side hands the call to a thread. |

The SDK's side also covers what `_signer.py` never needed: typed-data and
transaction signing through OWS (`wallet.py:778-889`), EIP-3009
(`wallet.py:891-985`), and an ERC-8128 **verifier**
(`erc8128/__init__.py`, `verify_request`).

## What an `OwsEM8128Client` in `uvd_em_sdk` needs to delegate to the SDK

The target is a class with EM's constructor and methods that owns no crypto: it
would build an `OWSWalletAdapter` and call `uvd_x402_sdk.erc8128.fetch_nonce` and
`sign_request`. What is missing:

1. **OWS as an installable dependency.** Neither `uvd-x402-sdk[wallet]` nor this
   package installs `open-wallet-standard` (row 2). There are two options: an `ows`
   extra here, pinned and with markers like the SDK's `dev` line
   (`pyproject.toml:149` there), or an `ows` extra in the SDK that this package
   requests. The platform markers matter because no wheel exists outside those
   platforms.
2. **The CLI → binding move.** Every current user of `_signer.py` has the npm CLI,
   and the adapter needs the Python binding. **To verify (not measured here):**
   whether the binding and the CLI read the same vault (default location and format),
   so that a `wallet_name` that works with `ows sign message` resolves in
   `ows.get_wallet(...)` with no migration. If they do not, one of two things is
   needed: a CLI-backed `WalletAdapter` (EIP-191 `sign_message` over the subprocess,
   as in `_signer.py:123-165`), in this package or in the SDK, or documented
   migration steps.
3. **Constructor mapping.** `wallet_name` becomes `OWSWalletAdapter(wallet_name,
   network=<network of chain_id>)`, and `chain_id` goes to `sign_request(chain_id=...)`.
   `wallet_address` should be checked once against `get_address()`
   (case-insensitive). That check is new; EM trusts the argument (row 5).
4. **Byte-identical signing.** Call `sign_request(adapter, method, url, body,
   nonce, chain_id=chain_id, content_digest="body-truthy")`, which gives EM's bytes
   on every live request (rows 11-14). The proof is EM's golden-vector test
   (`sdk/python/tests/test_signer.py:170-231`) run against the wrapper with a fake
   `ows` module, plus `tests/fixtures/erc8128.json` here.
5. **Errors.** The wrapper maps `ImportError`, `ValueError` and the binding's
   exceptions to an `OwsSignError` with the same name and base class, so that
   `except OwsSignError` keeps working (rows 6-8).
6. **`task_fingerprint` and `with_backoff` have no home in the SDK.** The
   fingerprint is Execution Market domain logic (its task fields), so it belongs in
   this package, ported with EM's tests (`test_signer.py:31-72`). `with_backoff`
   can be ported as-is (`test_signer.py:292-331`) or replaced by
   `request_with_retry`. The semantics differ (row 19), so this is a product
   decision, not a refactor.
7. **HTTP surface.** Either keep EM's raw `get()`/`post()` returning JSON with no
   status check, or build the class on `EMClient` (typed errors, per-attempt
   re-signing, tx-hash guard). If it is kept raw, merge the auth headers **last** so
   that `extra_headers` cannot replace `Signature`/`Signature-Input` (row 16).
8. **Optional:** run the OWS call in `asyncio.to_thread` so signing does not block
   the loop (row 20).

A related step inside this package: `uvd_em_sdk.erc8128` still keeps its own
signer twin, because until 0.8.0 the SDK was an optional extra
(`uvd_em_sdk/erc8128.py:39-48`). With the SDK a hard dependency since 0.9.0,
`uvd_em_sdk.erc8128.sign_request` can delegate to `uvd_x402_sdk.erc8128.sign_request`.
Byte equality is already pinned by `tests/test_erc8128_canonical_parity.py`. Two
things differ:

- this twin signs the verbatim `netloc` (`uvd_em_sdk/erc8128.py:121`) where the SDK
  normalizes it (row 12);
- the twin reads `time` from its own module, a contract its tests rely on
  (`tests/test_erc8128.py`). The SDK honours the same contract through
  `uvd_x402_sdk.erc8128.time` (`erc8128/signer.py:80-102`) or `now=`.
