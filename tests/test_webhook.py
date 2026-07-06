"""Tests for the YooKassa webhook server logic."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.webhook_server import create_app


def _make_payload(status: str = "succeeded", payment_id: str = "pay_123"):
    """Создаёт тестовый пэйлоад YooKassa webhook."""
    return {
        "type": "notification",
        "event": f"payment.{status}",
        "object": {
            "id": payment_id,
            "status": status,
            "amount": {"value": "100.00", "currency": "RUB"},
            "metadata": {
                "telegram_id": "12345",
                "message_id": "555",
                "chat_id": "12345",
                "reciept_control": "True",
            },
        },
    }


class TestWebhook:
    @pytest.fixture
    def client(self):
        nc = AsyncMock()
        app = create_app(
            nc=nc,
            nats_subject="payment.yookassa.succeeded",
            nalog_inn="test_inn",
            nalog_password="test_pass",
        )
        return TestClient(app)

    def test_invalid_json(self, client):
        """Невалидный JSON — 400."""
        response = client.post(
            "/yookassa/webhook",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_non_succeeded_status(self, client):
        """Статус не succeeded — 202."""
        payload = _make_payload(status="pending")
        response = client.post(
            "/yookassa/webhook",
            json=payload,
        )
        assert response.status_code == 202
        assert response.json()["status"] == "pending"

    def test_no_object(self, client):
        """Нет объекта платежа."""
        payload = {"event": "payment.succeeded", "status": "succeeded"}
        response = client.post(
            "/yookassa/webhook",
            json=payload,
        )
        assert response.status_code == 202

    @patch("app.webhook_server.Payment.find_one")
    @patch("app.webhook_server.create_nalog_receipt")
    def test_successful_payment(
        self, mock_receipt, mock_find_one, client
    ):
        """Успешный платёж — 200, публикуется NATS."""
        mock_find_one.return_value.status = "succeeded"
        mock_find_one.return_value.amount.value = "100.00"
        mock_find_one.return_value.metadata = {
            "telegram_id": "12345",
            "message_id": "555",
            "chat_id": "12345",
            "reciept_control": "True",
        }
        mock_receipt.return_value = (True, "https://receipt.url/print")

        payload = _make_payload()
        response = client.post("/yookassa/webhook", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "succeeded"
        mock_find_one.assert_called_once_with("pay_123")
        mock_receipt.assert_awaited_once()

    @patch("app.webhook_server.Payment.find_one")
    @patch("app.webhook_server.create_nalog_receipt")
    def test_payment_no_receipt(
        self, mock_receipt, mock_find_one, client
    ):
        """Без чека (reciept_control=False)."""
        mock_find_one.return_value.status = "succeeded"
        mock_find_one.return_value.amount.value = "100.00"
        mock_find_one.return_value.metadata = {
            "telegram_id": "12345",
            "reciept_control": "False",
        }
        mock_receipt.return_value = (False, None)

        payload = _make_payload()
        response = client.post("/yookassa/webhook", json=payload)

        assert response.status_code == 200
        mock_receipt.assert_not_awaited()

    @patch("app.webhook_server.Payment.find_one")
    def test_payment_status_not_succeeded(self, mock_find_one, client):
        """Платёж ещё не succeeded."""
        mock_find_one.return_value.status = "pending"
        mock_find_one.return_value.amount.value = "100.00"
        mock_find_one.return_value.metadata = {"telegram_id": "12345"}

        payload = _make_payload(status="pending")
        response = client.post("/yookassa/webhook", json=payload)

        assert response.status_code == 202
