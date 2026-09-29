"""Tests for the escrow resource (SDK-12)."""

import json

import pytest
import httpx
import respx

from uvd_em_sdk import EMClient

BASE = "https://api.execution.market/api/v1"

DEPOSIT_ID = "0x" + "cd" * 32
TASK_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with EMClient(api_key="em_test") as c:
        yield c


class TestEscrow:
    async def test_config(self, mock_router, client):
        mock_router.get("/escrow/config").mock(
            return_value=httpx.Response(
                200,
                json={
                    "available": True,
                    "network": "base",
                    "chain_id": 8453,
                    "factory_address": "0x" + "41" * 20,
                    "escrow_address": "0x" + "c4" * 20,
                    "usdc_address": "0x" + "83" * 20,
                },
            )
        )
        result = await client.escrow.config()
        assert result["chain_id"] == 8453

    async def test_config_with_network(self, mock_router, client):
        route = mock_router.get("/escrow/config").mock(
            return_value=httpx.Response(
                200,
                json={
                    "available": True,
                    "network": "polygon",
                    "chain_id": 137,
                    "factory_address": "0xb3" + "3d" * 19,
                    "escrow_address": "0x" + "32" * 20,
                    "operator_address": "0x" + "b8" * 20,
                    "usdc_address": "0x" + "3c" * 20,
                },
            )
        )
        result = await client.escrow.config(network="polygon")
        assert result["network"] == "polygon"
        assert route.calls.last.request.url.params["network"] == "polygon"
        assert result["available"] is True

    async def test_payment_extension(self, mock_router, client):
        mock_router.get("/escrow/payment-extension").mock(
            return_value=httpx.Response(200, json={"refund": {"scheme": "x402r"}})
        )
        result = await client.escrow.payment_extension()
        assert result["refund"]["scheme"] == "x402r"

    async def test_deposit(self, mock_router, client):
        mock_router.get(f"/escrow/deposits/{DEPOSIT_ID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "deposit_id": DEPOSIT_ID,
                    "payer": "0x" + "aa" * 20,
                    "merchant": "0x" + "bb" * 20,
                    "amount": "10.00",
                    "token": "0x" + "83" * 20,
                    "state": "IN_ESCROW",
                    "created_at": "2026-07-01T00:00:00Z",
                },
            )
        )
        result = await client.escrow.deposit(DEPOSIT_ID)
        assert result["state"] == "IN_ESCROW"

    async def test_balance(self, mock_router, client):
        route = mock_router.get("/escrow/balance").mock(
            return_value=httpx.Response(
                200,
                json={
                    "merchant": "0x" + "bb" * 20,
                    "balance_usdc": "42.00",
                    "network": "base",
                },
            )
        )
        result = await client.escrow.balance(merchant="0x" + "bb" * 20)
        assert result["balance_usdc"] == "42.00"
        assert route.calls.last.request.url.params["merchant"] == "0x" + "bb" * 20

    async def test_release(self, mock_router, client):
        route = mock_router.post("/escrow/release").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "deposit_id": DEPOSIT_ID,
                    "recipient": "0x" + "cc" * 20,
                    "amount": "10.00",
                },
            )
        )
        result = await client.escrow.release(
            DEPOSIT_ID, worker_address="0x" + "cc" * 20, amount="10.00"
        )
        assert result["success"] is True
        body = json.loads(route.calls.last.request.content)
        assert body == {
            "deposit_id": DEPOSIT_ID,
            "worker_address": "0x" + "cc" * 20,
            "amount": "10.00",
        }

    async def test_refund(self, mock_router, client):
        route = mock_router.post("/escrow/refund").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "tx_hash": "0xrefund",
                    "deposit_id": DEPOSIT_ID,
                    "payer": "0x" + "aa" * 20,
                    "amount": "10.00",
                },
            )
        )
        result = await client.escrow.refund(DEPOSIT_ID)
        assert result["tx_hash"] == "0xrefund"
        body = json.loads(route.calls.last.request.content)
        assert body == {"deposit_id": DEPOSIT_ID}

    async def test_update_task_escrow(self, mock_router, client):
        route = mock_router.patch(f"/tasks/{TASK_ID}/escrow").mock(
            return_value=httpx.Response(
                200, json={"success": True, "message": "Escrow metadata updated"}
            )
        )
        payment_info = {"operator": "0x" + "27" * 20, "salt": "0x1234"}
        result = await client.escrow.update_task_escrow(
            TASK_ID, payment_info=payment_info, escrow_tx="0xlock"
        )
        assert result["success"] is True
        body = json.loads(route.calls.last.request.content)
        assert body == {"payment_info": payment_info, "escrow_tx": "0xlock"}
