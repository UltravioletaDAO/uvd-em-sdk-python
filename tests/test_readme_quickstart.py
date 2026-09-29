"""The README quick-start must run as written (SDK-54).

Mirrors the "Quick Start" snippet of ``README.md`` against
respx — if the snippet references a method that does not exist, this file
stops compiling/passing. Also greps the README for the flat client methods
removed in the Phase 2 Stripe refactor.
"""

import re
from pathlib import Path

import pytest
import httpx
import respx
from eth_account import Account
from uvd_x402_sdk.wallet import EnvKeyAdapter

from uvd_em_sdk import CreateTaskParams, EMClient, EvidenceType, TaskCategory

from tests.removed_methods import REMOVED_FLAT_METHODS

BASE = "https://api.execution.market/api/v1"
README = Path(__file__).resolve().parents[1] / "README.md"

TASK_JSON = {
    "id": "abc-123",
    "title": "Take photo of storefront",
    "status": "published",
    "category": "physical_presence",
    "bounty_usd": 0.10,
    "deadline": "2026-04-01T00:00:00Z",
    "created_at": "2026-03-20T00:00:00Z",
    "agent_id": "0xABC",
}


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
def env_wallet_key(monkeypatch):
    """A THROWAWAY key in the env var the README snippet relies on.

    The snippet constructs ``EnvKeyAdapter()`` with no argument, so mirroring
    it call-for-call means the key has to arrive the way the README says it
    does -- from the environment. Generated in-test, never printed, never a
    real wallet.
    """
    monkeypatch.setenv("WALLET_PRIVATE_KEY", Account.create().key.hex())


async def test_readme_quickstart_flow(mock_router, env_wallet_key):
    """Exact call sequence of the README Quick Start."""
    nonce_route = mock_router.get("/auth/erc8128/nonce").mock(
        return_value=httpx.Response(200, json={"nonce": "nonce-1", "ttl_seconds": 300})
    )
    mock_router.get("/tasks").mock(
        return_value=httpx.Response(
            200,
            json={
                "tasks": [TASK_JSON],
                "total": 1,
                "count": 1,
                "offset": 0,
                "has_more": False,
            },
        )
    )
    create_route = mock_router.post("/tasks").mock(
        return_value=httpx.Response(201, json=TASK_JSON)
    )

    async with EMClient(wallet=EnvKeyAdapter()) as client:
        titles = []
        async for task in client.tasks.list(status="published"):
            titles.append(f"{task.title} — ${task.bounty_usd}")
        assert titles == ["Take photo of storefront — $0.1"]

        task = await client.tasks.create(
            CreateTaskParams(
                title="Take photo of storefront",
                instructions=(
                    "Go to 123 Main St and photograph the storefront "
                    "during business hours"
                ),
                category=TaskCategory.PHYSICAL_PRESENCE,
                bounty_usd=0.10,
                deadline_hours=24,
                evidence_required=[EvidenceType.PHOTO_GEO],
            )
        )
        assert task.id == "abc-123"

    # The point of the snippet: the write really is signed, and the read was
    # not. A wallet that silently sent nothing would pass the asserts above.
    assert nonce_route.call_count == 1
    signed = create_route.calls[0].request
    assert "Signature" in signed.headers
    assert 'nonce="nonce-1"' in signed.headers["signature-input"]
    assert "Authorization" not in signed.headers


def test_quickstart_snippet_does_not_offer_an_api_key():
    """D-03: the Quick Start is the first thing the PyPI page shows.

    API keys are rejected in production (``EM_API_KEYS_ENABLED=false`` ->
    403), so a first example built on one sends every new reader straight into
    an error that reads like the package is broken. A published version cannot
    be replaced, so this is guarded, not remembered.
    """
    blocks = re.findall(
        r"^```python\s*$(.*?)^```\s*$", README.read_text("utf-8"), re.M | re.S
    )
    assert blocks, "README has no python code blocks"
    first = blocks[0]
    assert "api_key" not in first, (
        "the first python block authenticates with an API key"
    )
    assert "EMClient(wallet=EnvKeyAdapter())" in first


def test_readme_mentions_no_removed_flat_methods():
    """SDK-54: the pre-refactor flat methods must not reappear in docs."""
    text = README.read_text(encoding="utf-8")
    for method in REMOVED_FLAT_METHODS:
        assert method not in text, f"README references removed method: {method}"


def test_readme_gives_the_install_that_actually_works():
    """D-03, decided the other way on 2026-09-21: the package IS on PyPI.

    This test used to assert the opposite (`"pip install uvd-em-sdk" not in
    text`) and it was right to: until the release existed, that command 404'd,
    and a README is the first thing a consumer runs. Now the release is the
    supported path, so the same test guards the same property from the other
    side -- the README must name an install that resolves.

    This file is the PyPI long_description. Whatever it says here is what the
    project page says, and a published version cannot be replaced.
    """
    text = README.read_text(encoding="utf-8")
    assert "pip install uvd-em-sdk" in text
    # 0.9.0: uvd-x402-sdk[wallet] is a hard dependency, so the bare install
    # signs, and the [wallet] extra is gone. Advising it now would only earn
    # the reader a pip warning ("does not provide the extra 'wallet'").
    assert "uvd-em-sdk[wallet]" not in text
    # The package left the private monorepo for its own repository, so the
    # "the source is private" warning this test used to require is gone.
    # What a reader of the old name needs instead is the way over.
    assert "em-plugin-sdk" in text
    assert "pip uninstall em-plugin-sdk" in text
