"""Тесты идемпотентности вебхуков YooKassa."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.server.webhook import create_app
from app.services.idempotency import (
    ACQUIRED,
    DUPLICATE,
    IN_PROGRESS,
    PaymentIdempotencyStore,
)


def _make_payload(
    payment_id: str = "pay_dup_1",
    event: str = "payment.succeeded",
):
    return {
        "type": "notification",
        "event": event,
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "100.00", "currency": "RUB"},
            "metadata": {
                "telegram_id": "12345",
                "message_id": "555",
                "chat_id": "12345",
            },
        },
    }


@pytest.fixture
def nc():
    return AsyncMock()


@pytest.fixture
def client(nc):
    app = create_app(
        nc=nc,
        nats_subject="payment.yookassa.succeeded",
        nalog_inn="test_inn",
        nalog_password="test_pass",
    )
    return TestClient(app)


def _mock_successful_payment(payment_id: str):
    """Мок Payment.find_one с успешным платежом."""
    payment = AsyncMock()
    payment.status = "succeeded"
    payment.amount.value = "100.00"
    payment.metadata = {
        "telegram_id": "12345",
        "message_id": "555",
        "chat_id": "12345",
    }
    return payment


class TestWebhookIdempotency:
    """Повторные вебхуки не должны начислять деньги дважды."""

    @patch("app.server.webhook.create_nalog_receipt")
    @patch("app.server.webhook.Payment.find_one")
    def test_duplicate_webhook_is_skipped(
        self, mock_find_one, mock_receipt, client, nc
    ):
        mock_find_one.return_value = _mock_successful_payment("pay_dup_1")
        mock_receipt.return_value = (True, "https://receipt.url/print")

        payload = _make_payload()

        first = client.post("/yookassa/webhook", json=payload)
        second = client.post("/yookassa/webhook", json=payload)

        assert first.status_code == 200
        assert first.json()["status"] == "succeeded"

        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"

        # Чек создан один раз, в NATS опубликовано один раз
        assert mock_receipt.await_count == 1
        assert nc.publish.await_count == 1

    @patch("app.server.webhook.create_nalog_receipt")
    @patch("app.server.webhook.Payment.find_one")
    def test_different_payments_are_processed(
        self, mock_find_one, mock_receipt, client
    ):
        mock_find_one.return_value = _mock_successful_payment("pay_1")
        mock_receipt.return_value = (True, "https://receipt.url/print")

        first = client.post("/yookassa/webhook", json=_make_payload("pay_1"))
        second = client.post("/yookassa/webhook", json=_make_payload("pay_2"))

        assert first.json()["status"] == "succeeded"
        assert second.json()["status"] == "succeeded"
        assert mock_receipt.await_count == 2

    @patch("app.server.webhook.publish_payment_success")
    @patch("app.server.webhook.create_nalog_receipt")
    @patch("app.server.webhook.Payment.find_one")
    def test_retry_after_publish_failure_succeeds(
        self, mock_find_one, mock_receipt, mock_publish, client
    ):
        """Если публикация упала — YooKassa повторит, чек не дублируется."""
        mock_find_one.return_value = _mock_successful_payment("pay_retry")
        mock_receipt.return_value = (True, "https://receipt.url/print")
        mock_publish.side_effect = [RuntimeError("nats is down"), None]

        payload = _make_payload("pay_retry")

        failed = client.post("/yookassa/webhook", json=payload)
        assert failed.status_code == 500

        retried = client.post("/yookassa/webhook", json=payload)
        assert retried.status_code == 200
        assert retried.json()["status"] == "succeeded"

        # Чек создан один раз — повторно в ФНС не отправляем
        assert mock_receipt.await_count == 1
        assert mock_publish.await_count == 2

    @patch("app.server.webhook.create_nalog_receipt")
    @patch("app.server.webhook.Payment.find_one")
    def test_failed_lookup_does_not_block_retry(
        self, mock_find_one, mock_receipt, client, nc
    ):
        """Ошибка поиска платежа отдаёт 5xx и снимает резерв."""
        mock_find_one.side_effect = [
            RuntimeError("502 Bad Gateway"),
            _mock_successful_payment("pay_lookup"),
        ]
        mock_receipt.return_value = (True, "https://receipt.url/print")

        payload = _make_payload("pay_lookup")

        failed = client.post("/yookassa/webhook", json=payload)
        assert failed.status_code == 500

        retried = client.post("/yookassa/webhook", json=payload)
        assert retried.status_code == 200
        assert retried.json()["status"] == "succeeded"
        assert nc.publish.await_count == 1


class TestWebhookEventFilter:
    """Начислять деньги можно только по событию payment.succeeded."""

    @patch("app.server.webhook.create_nalog_receipt")
    @patch("app.server.webhook.Payment.find_one")
    def test_refund_event_is_ignored(
        self, mock_find_one, mock_receipt, client, nc
    ):
        payload = _make_payload(event="refund.succeeded")

        response = client.post("/yookassa/webhook", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        mock_find_one.assert_not_called()
        mock_receipt.assert_not_called()
        nc.publish.assert_not_called()

    @patch("app.server.webhook.create_nalog_receipt")
    @patch("app.server.webhook.Payment.find_one")
    def test_canceled_event_is_ignored(
        self, mock_find_one, mock_receipt, client, nc
    ):
        payload = {
            "type": "notification",
            "event": "payment.canceled",
            "object": {"id": "pay_cancel", "status": "canceled"},
        }

        response = client.post("/yookassa/webhook", json=payload)

        assert response.status_code == 200
        mock_receipt.assert_not_called()
        nc.publish.assert_not_called()


class TestPaymentIdempotencyStore:
    """Юнит-тесты хранилища обработанных платежей."""

    @pytest.mark.asyncio
    async def test_first_acquire_is_new(self):
        store = PaymentIdempotencyStore()
        assert await store.acquire("pay_1") == ACQUIRED

    @pytest.mark.asyncio
    async def test_second_acquire_while_processing(self):
        store = PaymentIdempotencyStore()
        await store.acquire("pay_1")
        assert await store.acquire("pay_1") == IN_PROGRESS

    @pytest.mark.asyncio
    async def test_done_payment_is_duplicate(self):
        store = PaymentIdempotencyStore()
        await store.acquire("pay_1")
        await store.mark_done("pay_1")
        assert await store.acquire("pay_1") == DUPLICATE

    @pytest.mark.asyncio
    async def test_released_payment_can_be_processed_again(self):
        store = PaymentIdempotencyStore()
        await store.acquire("pay_1")
        await store.release("pay_1")
        assert await store.acquire("pay_1") == ACQUIRED

    @pytest.mark.asyncio
    async def test_release_keeps_done_payments(self):
        store = PaymentIdempotencyStore()
        await store.acquire("pay_1")
        await store.mark_done("pay_1")
        await store.release("pay_1")
        assert await store.acquire("pay_1") == DUPLICATE

    @pytest.mark.asyncio
    async def test_state_survives_restart(self, tmp_path):
        path = tmp_path / "processed.json"

        store = PaymentIdempotencyStore(path)
        await store.acquire("pay_1")
        await store.mark_done("pay_1")

        restored = PaymentIdempotencyStore(path)
        assert await restored.acquire("pay_1") == DUPLICATE

    @pytest.mark.asyncio
    async def test_write_error_does_not_break_processing(self, tmp_path):
        # Родитель пути — файл, поэтому запись невозможна
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        store = PaymentIdempotencyStore(blocker / "processed.json")

        assert await store.acquire("pay_1") == ACQUIRED
        await store.mark_done("pay_1")
        assert await store.acquire("pay_1") == DUPLICATE
