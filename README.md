# uvd-em-sdk

**The Python client for the [Execution Market](https://execution.market) REST
API** — publish tasks, manage workers, sign escrow authorizations, handle
payments. Async, typed (Pydantic v2), resource-namespaced (Stripe pattern).
Enums and the network registry are generated from the backend source of truth
(`scripts/sync_enums.py`, `scripts/sync_networks.py`) with parity tests.

## Replaces `em-plugin-sdk`

`uvd-em-sdk` is the package formerly published as **`em-plugin-sdk`**, moved
out of the Execution Market monorepo into its own repository and renamed.
Version 0.9.0 has the API of `em-plugin-sdk` 0.8.0 under the new names:

| | Before | Now |
|---|---|---|
| Distribution (`pip install`) | `em-plugin-sdk` | `uvd-em-sdk` |
| Import package | `em_plugin_sdk` | `uvd_em_sdk` |
| `User-Agent` | `em-plugin-sdk/<version>` | `uvd-em-sdk/<version>` |

Migrating is a rename:

```bash
pip uninstall em-plugin-sdk
pip install uvd-em-sdk
```

and `from em_plugin_sdk import ...` becomes `from uvd_em_sdk import ...`.

## Install

```bash
pip install uvd-em-sdk

# The [wallet] extra is required for production auth — every write is signed.
pip install "uvd-em-sdk[wallet]"
```

Optional extras: `[wallet]` (ERC-8128 + escrow signing), `[realtime]`
(WebSocket), `[all]`, `[dev]`.

From a checkout, for contributors:

```bash
git clone https://github.com/UltravioletaDAO/uvd-em-sdk-python
cd uvd-em-sdk-python
pip install -e ".[dev]"
```

## Quick Start

Production accepts exactly one credential for agent writes: **an ERC-8128
signature from your own wallet**. There is no API key to ask for — the server
runs with `EM_API_KEYS_ENABLED=false` and answers `403` to every bearer token
(see [Authentication](#authentication)).

```python
import asyncio

from uvd_em_sdk import CreateTaskParams, EMClient, EvidenceType, TaskCategory
from uvd_x402_sdk.wallet import EnvKeyAdapter  # pip install "uvd-em-sdk[wallet]"


async def main():
    # EnvKeyAdapter reads WALLET_PRIVATE_KEY (or PRIVATE_KEY) from the
    # environment — never hardcode a key. Every write below is signed per
    # ERC-8128 (RFC 9421) with a fresh single-use nonce.
    async with EMClient(wallet=EnvKeyAdapter()) as client:
        # List published tasks (auto-paginating async iterator) — reads are
        # open, so this one call works without a wallet too.
        async for task in client.tasks.list(status="published"):
            print(f"{task.title} — ${task.bounty_usd}")

        # Publish a task — signed
        task = await client.tasks.create(
            CreateTaskParams(
                title="Take photo of storefront",
                instructions="Go to 123 Main St and photograph the storefront during business hours",
                category=TaskCategory.PHYSICAL_PRESENCE,
                bounty_usd=0.10,
                deadline_hours=24,
                evidence_required=[EvidenceType.PHOTO_GEO],
            )
        )
        print(f"Created task {task.id}")


asyncio.run(main())
```

## Authentication

Production runs with `EM_API_KEYS_ENABLED=false` — **API keys are rejected
(403)**. Three auth modes, per surface:

| Mode | How | Used by |
|------|-----|---------|
| **ERC-8128 wallet signing** (production) | `EMClient(wallet=EnvKeyAdapter())` — every write is signed per RFC 9421 with a fresh single-use nonce | All agent writes (tasks, submissions, escrow, disputes, ...) |
| API key (internal testing only) | `EMClient(api_key="em_...")` | Nothing in production |
| Supabase JWT (human sessions) | `EMClient(supabase_jwt="eyJ...")` | `client.h2a.*` (publisher) and worker-scoped reads/writes (`workers.my_submission`, `submissions.get`, ...) |

```python
from uvd_x402_sdk.wallet import EnvKeyAdapter  # pip install "uvd-em-sdk[wallet]"

async with EMClient(wallet=EnvKeyAdapter()) as client:  # key from env, never hardcoded
    await client.identity.register("my-agent")
```

### API key — internal testing only

The constructor still takes one, and **against production it buys you nothing**:
`EM_API_KEYS_ENABLED=false` there, so every request carrying
`Authorization: Bearer em_...` comes back `403` (INC-2026-03-27). It is for a
local or staging server started with API keys on. Passing both `api_key` and
`wallet` disables signing — the key wins — so never leave one set by accident.

```python
async with EMClient(api_key="em_your_key") as client:   # 403 in production
    async for task in client.tasks.list(status="published"):
        print(task.title)
```

## Resource namespaces

| Namespace | Covers |
|-----------|--------|
| `client.tasks` | `create` `get` `list` `list_page` `available` `cancel` `assign` `apply` `list_applications` `get_payment` `get_transactions` |
| `client.submissions` | `list` `get` `submit` `approve` `reject` `request_more_info` |
| `client.workers` | `register` `balance` `payment_events` `my_submission` `geo_reference` `update_social_links` |
| `client.services` | supply side — `publish` `browse` `get` `update` `mine` `find_sellers` `order` |
| `client.h2a` | `publish` (universal `POST /publish`) `list` `get` `submissions` `applications` `assign` `approve` `reject` `cancel` `rate_publisher` `payment_config` |
| `client.agents` | `directory` `register_executor` |
| `client.escrow` | `config` `refund` `update_task_escrow` (+ legacy 410 endpoints, documented per method) |
| `client.disputes` | `list` `available` `get` `create` `resolve` |
| `client.worldid` | `rp_signature` `verify` `worker_status` (Orb gate for bounties >= $500) |
| `client.reputation` | agent reputation/identity, leaderboard, `rate_worker` `rate_agent`, prepare/confirm feedback, and the **rater-signed rail** (`prepare_relayed_rating` / `submit_relayed_rating`) |
| `client.evidence` | presigned upload/download, `upload`, AI `verify` |
| `client.payments` | `balance` `events` `task_payment` `task_transactions` |
| `client.webhooks` | CRUD, `rotate_secret`, `test`, `verify_signature` |
| `client.identity` | ERC-8004 gasless registration |
| `client.relay` | relay legs |

Top-level: `client.health()`, `client.config()`.

## Service listings (supply side)

Tasks are the demand side — you ask for work. A **service listing** is the
supply side: you advertise a capability and let buyers order it. Publishing a
listing moves no money; `order()` is the only money-moving call in the
namespace, and it locks escrow like any other assignment.

```python
# SELLER — advertise a capability (no escrow, no funds move)
listing = await client.services.publish(
    title="On-site store audit in Miami",
    description="I visit the store, photograph the shelves and report back",
    category=TaskCategory.PHYSICAL_PRESENCE,
    unit_price_usd=5.0,
    skills=["photography", "retail"],
)
await client.services.update(listing.id, availability="paused")   # off the board
await client.services.mine()                                      # paused rows included

# BUYER — vet, then buy. Rank by the seller's on-chain score, not by arrival order.
board = await client.services.browse(sort="reputation", min_reputation=70)
best = board.listings[0]
print(best.title, best.effective_reputation_score, best.seller_correlation)

payment_auth = build_escrow_pre_auth(                 # receiver = the SELLER's wallet
    payment_config=await client.h2a.payment_config(),
    network="base",
    payer="0xBuyer...",
    receiver=best.seller_wallet,
    amount_usd=best.unit_price_usd,
    deadline=deadline_epoch,
    wallet=EnvKeyAdapter(),
)
order = await client.services.order(
    best.id, payment_auth=payment_auth, payment_network="base"
)
print(order.task_id, order.escrow_status)   # 'locked', or 'assigning' while async
```

An order materializes a normal escrowed task, so it lands on the seller as a
worker assignment and finishes through the usual submit → approve → release
path. Already published a task? `client.services.find_sellers(task_id)` returns
the active listings that could fill it, ranked by effective reputation —
read-only, ordering one is still an explicit call.

## Escrow signing (ADR-002, sign-on-assignment)

Fee model: **flat 13% (1300 bps)**, split atomically on-chain at release.

> **Protocol constraint (verbatim):** The EIP-3009 nonce is
> `AuthCaptureEscrow.getHash(paymentInfo)` which **includes the receiver** —
> the escrow signature can only be created AT ASSIGNMENT, when the worker is
> known. Never design flows that sign an escrow auth before the worker is
> chosen.

```python
from uvd_em_sdk import build_escrow_pre_auth
from uvd_x402_sdk.wallet import EnvKeyAdapter

config = await client.h2a.payment_config()          # per-network escrow params
payment_auth = build_escrow_pre_auth(
    payment_config=config,
    network="base",                                  # unknown network -> ValueError (fail loud)
    payer="0xPublisher...",
    receiver="0xWorker...",                          # committed by the nonce
    amount_usd=0.10,                                 # on-chain deposit limit: $100
    deadline=task_deadline_epoch,
    wallet=EnvKeyAdapter(),
)
await client.h2a.assign(task_id, executor_id, payment_auth=payment_auth)
```

Escrow-capable networks = escrow contract AND deployed operator
(`uvd_em_sdk.networks.has_escrow_support`). The network registry is a
generated snapshot of the backend `NETWORK_CONFIG` — resync with
`python scripts/sync_networks.py && ruff format .`.

## Rater-signed reputation (EIP-7702)

The ERC-8004 ReputationRegistry records `msg.sender` as the author of a rating,
and there is no delegation path in the deployed contract. So a rating relayed by
the Facilitator — which is what sponsored gas means — is attributed **to the
Facilitator**. Measured on Base, that was **91,3%** of the network's feedback
sitting under one address that, since revoking is authorised off the same field,
could also have erased all of it.

This rail moves authorship back to the rater without changing the registry. Gas
stays sponsored: your agent signs, the Facilitator pays.

```python
prep = await client.reputation.prepare_relayed_rating(
    task_id=task_id,
    direction="executor_rates_publisher",   # or publisher_rates_executor
    score=95,
    comment="paid on time, clear spec",
)

# prep["delegated"] is False the FIRST time this wallet rates on this chain:
# an EIP-7702 authorization must be signed too, and `prep["account_nonce"]`
# carries what it needs. From the second rating on, only the digest.
signature = wallet.sign_message(prep["digest"])

result = await client.reputation.submit_relayed_rating(prep, signature)
assert result["authored_by"].lower() == my_wallet.lower()   # not the sponsor's
```

**Echo the `prepare` fields back verbatim.** The Facilitator rebuilds the
registry calldata from them and requires the signature to cover exactly that;
recompute one and the signature silently stops verifying.

**Do not fall back to the legacy path when signing fails.** It would put the
sponsor's address on your rating while reporting success — the exact confusion
this rail exists to remove. `supports_rater_authorship(network)` tells you up
front whether a chain can carry a rating you authored yourself (Avalanche's
C-Chain refuses EIP-7702, so it cannot).

## Development

```bash
pip install -e ".[dev]"      # from the repository root
pytest                       # all tests, offline (respx mocks)
ruff format . && ruff check .
```

## License

MIT
