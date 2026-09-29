---
date: 2026-07-03
tags:
  - type/report
  - domain/integrations
status: active
aliases:
  - SDK Status
related-files:
  - em-plugin-sdk/em_plugin_sdk/client.py
  - em-plugin-sdk/em_plugin_sdk/resources/
  - em-plugin-sdk/em_plugin_sdk/erc8128.py
  - em-plugin-sdk/em_plugin_sdk/escrow_signing.py
  - docs/planning/MASTER_PLAN_MOBILE_SDK_PARITY_SYNC_2026-07-03.md
---

# em-plugin-sdk — Status Report (v0.5.0)

> Python SDK for the Execution Market REST API.
> Regenerated 2026-07-03 from the actual source tree after Master Plan Phases 6-8
> (merge `344bad74`). **Every number below is counted from code, not from the plan.**
> Package: `em-plugin-sdk/` (standalone, ready for its own repo).

## Summary

| Metric | Value | Counted from |
|--------|-------|--------------|
| Version | **0.5.0** | `em_plugin_sdk/__init__.py::__version__` (single source; `pyproject.toml` derives it at build via `[tool.hatch.version]`) |
| Resource namespaces | **15** | `client.py` (`__init__`) |
| API methods | **101** | sum over resources (below) |
| Tests | **225 passing** | `pytest --collect-only` (0 failing) |
| Test files | 20 | `tests/` |
| Networks in registry | 17 (9 EVM w/ full escrow + Solana + testnets) | `networks.py::NETWORKS` |
| Escrow endpoints returning 410 Gone | 4 | `resources/escrow.py` |
| Auth paths | ERC-8128 wallet · supabase_jwt scoping · api_key fallback | `client.py` |
| PyPI | **NOT published** — install from source (D-03) | — |

---

## Version — single source of truth

`__version__ = "0.5.0"` lives ONLY in `em_plugin_sdk/__init__.py` (SDK-51). `pyproject.toml`
declares `dynamic = ["version"]` and `[tool.hatch.version]` reads it at build time; `client.py`
derives the `User-Agent` from it at runtime. `tests/test_version.py` (3 tests) enforces that
these agree — there is no second literal to drift.

---

## Auth model (Phase 6)

Three paths, resolved per request:

1. **ERC-8128 wallet signing (production).** Attach a `WalletAdapter` and pass **no** api_key
   (`EMClient(wallet=...)`). Every non-GET request is signed per ERC-8128 / RFC 9421
   (`erc8128.py::sign_request`): a fresh single-use nonce is fetched from
   `GET /auth/erc8128/nonce`, and `Signature` / `Signature-Input` / `Content-Digest` headers
   are attached. Nonces are single-use, so the request is **re-signed on every retry attempt**
   (`request_with_retry` calls `headers_factory` per attempt). GETs are unsigned by default
   (`sign=True` forces it for owner-scoped reads).
2. **`supabase_jwt` scoping.** Sent as `Authorization: Bearer <jwt>` ONLY by the `client.h2a`
   namespace (publisher-side, `verify_jwt_auth`) and the worker-scoped methods
   (`workers.my_submission` / `.geo_reference` / `.update_social_links`, `submissions.get`;
   `verify_worker_auth`). Neither api_key nor ERC-8128 signatures are accepted on those routes.
   All other namespaces keep api_key / ERC-8128 auth.
3. **api_key fallback (internal testing only).** Bearer token. Production runs
   `EM_API_KEYS_ENABLED=false`, which rejects every api_key with HTTP 403 (INC-2026-03-27), so
   this path is for local/dev only. When both a wallet and an api_key are provided, the api_key
   takes precedence (signing is disabled).

---

## API coverage — endpoint by endpoint

15 resource namespaces + 2 top-level methods on `EMClient`. Method counts are the real public
methods on each resource class.

| Namespace | Methods (n) | Method names |
|-----------|:-----------:|--------------|
| `client.tasks` | 12 | create, get, list, list_page, cancel, assign, batch_create*, apply, list_applications, get_payment, get_transactions, available |
| `client.submissions` | 6 | list, get, submit, approve, reject, request_more_info |
| `client.workers` | 6 | register, balance, payment_events, my_submission, geo_reference, update_social_links |
| `client.reputation` | 15 | get_agent, get_agent_identity, leaderboard, info, networks, em_reputation, em_identity, get_feedback, rate_worker, rate_agent, register, prepare_feedback, confirm_feedback, **prepare_relayed_rating**, **submit_relayed_rating** |
| `client.evidence` | 4 | presign_upload, presign_download, upload, verify |
| `client.payments` | 4 | balance, events, task_payment, task_transactions |
| `client.webhooks` | 8 | create, list, get, update, delete, rotate_secret, test, verify_signature† |
| `client.h2a` | 11 | publish, list, get, submissions, applications, assign, approve, reject, cancel, rate_publisher, payment_config |
| `client.agents` | 2 | directory, register_executor |
| `client.relay` | 4 | create, get, assign_leg, handoff |
| `client.identity` | 1 | register (ERC-8004, gasless via Facilitator) |
| `client.escrow` | 7 | config, payment_extension, deposit, balance, release, refund, update_task_escrow |
| `client.disputes` | 5 | list, available, get, create, resolve |
| `client.worldid` | 3 | rp_signature, verify, worker_status |
| top-level | 2 | health, config |
| **Total** | **101** | |

`*` `tasks.batch_create` is deprecated: `POST /tasks/batch` is disabled server-side (fixed 503,
hardening API-002). The method emits a `DeprecationWarning` and remains only for compatibility.
`†` `webhooks.verify_signature` is the one offline method (local HMAC-SHA256, no network call).

**Endpoint overlaps** (methods sharing a backend route — real coverage is ~95 distinct
endpoints, not 101): `payments.balance` ≡ `workers.balance` (`/payments/balance/{addr}`),
`payments.events` ≡ `workers.payment_events` (`/payments/events`), `payments.task_payment` ≡
`tasks.get_payment` (`/tasks/{id}/payment`), `payments.task_transactions` ≡
`tasks.get_transactions` (`/tasks/{id}/transactions`). These are intentional ergonomic aliases.

### New namespaces added in Phase 8

- **`client.escrow`** — x402r escrow config + lifecycle (release/refund gated by
  `verify_agent_auth_write` = ERC-8128 in prod).
- **`client.disputes`** — publisher-initiated disputes + human-arbiter resolution
  (mirrors `mcp_server/api/routers/disputes.py`; 9 typed `dispute_reason` values validated
  locally).
- **`client.worldid`** — World ID 4.0 RP signing + Cloud API verify + `world-status` lookup.
- **`client.identity`** — ERC-8004 on-chain identity registration, gasless via the Facilitator
  (resolves the wallet from the attached `WalletAdapter`).
- **Worker-side + rating** on existing namespaces: `workers.my_submission` / `geo_reference` /
  `update_social_links`, `submissions.get`, `h2a.rate_publisher`, `h2a.applications`,
  `h2a.assign`, `h2a.payment_config`.

---

## Escrow endpoints — 4 return 410 Gone

Kept in `resources/escrow.py` because the routes still exist, with live status documented per
method. All direct-contract escrow queries/writes moved to the x402 SDK + Facilitator (gasless):

| Method | Endpoint | Live status |
|--------|----------|-------------|
| `escrow.config` | `GET /escrow/config` | **LIVE** — contract addresses + network |
| `escrow.refund` | `POST /escrow/refund` | **LIVE** — gasless, ERC-8128 write, ownership-checked |
| `escrow.update_task_escrow` | `PATCH /tasks/{id}/escrow` | **LIVE** — persists release-capable `payment_info` |
| `escrow.payment_extension` | `GET /escrow/payment-extension` | **410 Gone** |
| `escrow.deposit` | `GET /escrow/deposits/{id}` | **410 Gone** (use `tasks.get().escrow_status`) |
| `escrow.balance` | `GET /escrow/balance` | **410 Gone** (check a block explorer) |
| `escrow.release` | `POST /escrow/release` | **410 Gone** (release via `submissions.approve`) |

The four **410 Gone** endpoints are `payment_extension`, `deposit`, `balance`, `release`.

---

## Escrow sign-on-assignment (ADR-002)

`escrow_signing.py` — Python mirror of the dashboard's `buildEscrowPreAuth`
(`dashboard/src/services/h2aSigning.ts`) and `uvd_x402_sdk.AdvancedEscrowClient`:

- `build_escrow_pre_auth(...)` — signs the `ReceiveWithAuthorization` EIP-712 typed data AT
  ASSIGNMENT (the EIP-3009 nonce = `AuthCaptureEscrow.getHash(paymentInfo)` **includes the
  receiver**, so the signature can only be created once the worker is known). Returns the raw
  JSON `X-Payment-Auth` value the backend relays verbatim to the Facilitator `/settle`.
- `compute_escrow_nonce(...)` — port of `AdvancedEscrowClient._compute_nonce` (payer slot
  zeroed for the payer-agnostic hash).
- **Fail-loud**: unknown/incomplete network config raises `ValueError` instead of falling back
  to a mismatched EIP-712 domain (which would sign a wallet-draining auth). On-chain limits are
  enforced client-side: bounty ≤ $100 (deposit condition) and `maxFeeBps` ≥ 1300 (operator's
  static fee). Release/refund windows extend to `deadline + 7d + 7d` so a human publisher can
  still approve after the worker delivers near the deadline (avoids `AfterAuthorizationExpiry`).
- Requires `pip install em-plugin-sdk[wallet]` (eth-account / eth-abi / eth-utils).

---

## Standalone features (no server calls)

| Feature | Module | Detail |
|---------|--------|--------|
| Fee calculator | `fees.py` | `calculate_fee`, `calculate_reverse_fee`, `get_fee_rate` — flat 13% (1300 bps, StaticFeeCalculator parity) |
| Network registry | `networks.py` | 17 networks (GENERATED from backend `NETWORK_CONFIG` by `scripts/sync_networks.py`; `test_networks_sync.py` fails on drift). Helpers: `get_network`, `get_enabled_networks`, `get_supported_tokens`, `is_valid_pair`, `get_chain_id`, `has_escrow_support`, `get_escrow_networks`. 9 EVM networks have full x402r escrow (escrow AND operator); 10 default-enabled (9 EVM + Solana) |
| Escrow signing | `escrow_signing.py` | `build_escrow_pre_auth`, `compute_escrow_nonce` |
| ERC-8128 signer | `erc8128.py` | `sign_request`, `fetch_nonce` |
| Real-time events | `realtime/` | `EMEventClient` (auto-reconnect, rooms, typed handlers) + 22 event constants |
| Mock client | `testing/mock_client.py` | `MockEMClient` — drop-in for consumer unit tests |

---

## Tests — 225 passing (per file)

`pytest --collect-only -q` → **225 collected, 0 failing**.

| Test file | Tests |
|-----------|:-----:|
| `test_client.py` | 41 |
| `test_h2a.py` | 24 |
| `test_mock_client.py` | 18 |
| `test_relay.py` | 4 |
| `test_fees.py` | 15 |
| `test_escrow_signing.py` | 14 |
| `test_reputation.py` | 23 |
| `test_networks.py` | 13 |
| `test_realtime.py` | 11 |
| `test_webhooks.py` | 9 |
| `test_erc8128.py` | 9 |
| `test_disputes.py` | 9 |
| `test_escrow.py` | 7 |
| `test_worker_side.py` | 5 |
| `test_payments.py` | 5 |
| `test_evidence.py` | 4 |
| `test_worldid.py` | 3 |
| `test_version.py` | 3 |
| `test_readme_quickstart.py` | 3 |
| `test_networks_sync.py` | 2 |
| **Total** | **225** |

CI runs these in the `sdk-tests` job of `.github/workflows/ci.yml` (path-filtered to
`em-plugin-sdk/**`).

---

## Deferred surface (decision D-04)

The SDK covers the **agent surface + what the dashboard consumes** — not all ~150 backend
routes. Per the sync skill state (`.claude/skills/sincroniza/state.json`), the following endpoint
groups are **deferred, not drift** — a future `sincroniza` run must treat them as out-of-scope
until D-04 is revisited:

> **SDK-14 / 16 / 17 / 19 / 20 / 21 / 22 / 24 / 25 / 26 / 27 / 39** — arbiter AaaS, MoonPay,
> account, cross-chain reputation, ENS, moderation, executor identity, observability, social
> evidence, ClawKey/VeryAI, taxímetro, pagination caps/headers.

---

## Distribution — install from source (NOT on PyPI)

Per decision **D-03**, v0.5.0 is **not published to PyPI** (publishing is an irreversible
external write requiring explicit OK). README and docs instruct **install from source** until
that OK lands:

```bash
pip install -e path/to/em-plugin-sdk           # + [wallet] for escrow/ERC-8128 signing
```

Dependencies: `httpx>=0.25.0`, `pydantic>=2.0`; optional `[realtime]` (`websockets`),
`[wallet]` (`uvd-x402-sdk[wallet]` + eth-account/eth-abi/eth-utils), `[dev]` (pytest, respx).

---

## Usage — the two production auth paths

### Agent (ERC-8128 wallet signing)

```python
from uvd_x402_sdk.wallet import EnvKeyAdapter
from em_plugin_sdk import EMClient

async with EMClient(wallet=EnvKeyAdapter()) as client:      # no api_key → signed writes
    await client.identity.register("my-agent")               # gasless ERC-8004
    task = await client.tasks.create(params)                 # X-Payment-Auth for escrow
```

### Human publisher (Supabase JWT) — sign-on-assignment escrow

```python
from em_plugin_sdk import EMClient, build_escrow_pre_auth

async with EMClient(supabase_jwt="eyJ...", wallet=publisher_wallet) as client:
    cfg = await client.h2a.payment_config()                  # GET /h2a/payment-config
    auth = build_escrow_pre_auth(
        payment_config=cfg, network="base",
        payer=publisher_addr, receiver=worker_addr,          # nonce commits to the worker
        amount_usd=0.10, deadline=task_deadline, wallet=publisher_wallet,
    )
    await client.h2a.assign(task_id, worker_id, payment_auth=auth)
    await client.h2a.approve(task_id, submission_id=sub_id,
                             verdict="accepted", worker_score=5)   # rating required
```
