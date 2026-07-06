"""Tests for NATS client."""

import json
from unittest.mock import AsyncMock

import pytest

from app.nats_client import publish_payment_success


class TestNatsClient:
    @pytest.mark.asyncio
    async def test_publish_payment_success(self):
        nc = AsyncMock()
        nc.publish = AsyncMock()

        await publish_payment_success(
            nc=nc,
            subject="payment.yookassa.succeeded",
            telegram_id=12345,
            amount=100,
            payment_id="pay_abc123",
            receipt_status=True,
            receipt_link="https://receipt.url/print",
            message_id=555,
            chat_id=12345,
        )

        nc.publish.assert_awaited_once()
        call_args = nc.publish.call_args[0]
        assert call_args[0] == "payment.yookassa.succeeded"

        payload = json.loads(call_args[1])
        assert payload["payload"]["telegram_id"] == "12345"
        assert payload["payload"]["amount"] == "100"
        assert payload["payload"]["payment_id"] == "pay_abc123"
        assert payload["payload"]["receipt_status"] is True
        assert payload["payload"]["receipt_link"] == "https://receipt.url/print"
        assert payload["payload"]["message_id"] == "555"
        assert payload["payload"]["chat_id"] == "12345"

    @pytest.mark.asyncio
    async def test_publish_minimal(self):
        nc = AsyncMock()
        nc.publish = AsyncMock()

        await publish_payment_success(
            nc=nc,
            subject="payment.yookassa.succeeded",
            telegram_id=999,
            amount=50,
            payment_id="pay_min",
        )

        nc.publish.assert_awaited_once()
        payload = json.loads(nc.publish.call_args[0][1])
        assert payload["payload"]["telegram_id"] == "999"
        assert payload["payload"]["amount"] == "50"
        assert payload["payload"]["receipt_status"] is False
        assert payload["payload"].get("receipt_link") is None
        assert payload["payload"].get("message_id") is None
        assert payload["payload"].get("chat_id") is None
