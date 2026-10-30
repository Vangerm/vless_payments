import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from yookassa import Configuration, Payment

from app.nats_client.publisher import publish_payment_success
from app.services.idempotency import (
    ACQUIRED,
    DUPLICATE,
    IN_PROGRESS,
    PaymentIdempotencyStore,
)
from app.services.receipt_service import create_nalog_receipt

logger = logging.getLogger(__name__)

# Событие YooKassa, которое означает успешную оплату
_PAYMENT_SUCCEEDED = "payment.succeeded"


def create_app(
    nc,
    nats_subject: str,
    nalog_inn: str,
    nalog_password: str,
    processed_payments_path: str | None = None,
):
    """Фабрика FastAPI-приложения с замыканием зависимостей."""

    app = FastAPI(title="YooKassa Webhook")

    # Платежи, которые уже обработаны (защита от повторных уведомлений)
    processed_payments = PaymentIdempotencyStore(processed_payments_path)

    # Результаты уже созданных чеков: если публикация в NATS не удалась
    # и YooKassa повторит уведомление, повторно чек в ФНС не создаётся.
    created_receipts: dict[str, tuple[bool, str | None]] = {}

    def _find_status(obj):
        """Рекурсивно ищет поле 'status' в любом уровне вложенности."""
        if isinstance(obj, dict):
            if "status" in obj:
                return obj["status"]
            for v in obj.values():
                s = _find_status(v)
                if s is not None:
                    return s
        elif isinstance(obj, list):
            for item in obj:
                s = _find_status(item)
                if s is not None:
                    return s
        return None

    def _extract_object(obj) -> dict | None:
        """Извлекает объект с ключом 'object' (YooKassa v3 webhook)."""
        if isinstance(obj, dict) and "object" in obj:
            return obj["object"]
        return obj

    @app.post("/yookassa/webhook")
    async def yookassa_webhook(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "invalid json"})

        event = payload.get("event") if isinstance(payload, dict) else None
        status = _find_status(payload)
        logger.info(
            "YooKassa webhook received: event=%s, status=%s", event, status,
        )

        if status != "succeeded":
            # ЮKassa считает уведомление доставленным только при ответе
            # HTTP 200. Любой другой код заставляет её повторять
            # отправку 24 часа, поэтому не-succeeded события (pending,
            # canceled, waiting_for_capture) подтверждаем кодом 200 —
            # действий по ним не требуется.
            return JSONResponse(
                status_code=200,
                content={"status": status or "unknown"},
            )

        # Обрабатываем ТОЛЬКО успешную оплату. Уведомления других типов
        # (например, refund.succeeded) тоже содержат status="succeeded",
        # и без этой проверки по ним начислялись бы деньги.
        if event is not None and event != _PAYMENT_SUCCEEDED:
            logger.info(
                "YooKassa webhook: событие %s не является оплатой — "
                "пропускаем (начисление не выполняется)",
                event,
            )
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "event": event},
            )

        # Извлекаем объект платежа (для YooKassa v3/v2)
        payment_data = _extract_object(payload)
        if not payment_data:
            logger.warning("No payment object found in payload")
            # 5xx — ЮKassa повторит уведомление позже
            return JSONResponse(
                status_code=500, content={"detail": "no payment object"}
            )

        payment_id = payment_data.get("id")
        if not payment_id:
            logger.warning("No payment id in payload")
            return JSONResponse(
                status_code=500, content={"detail": "no payment id"}
            )

        # --- Защита от повторного зачисления ---
        # YooKassa повторяет уведомление, пока не получит ответ 2xx.
        # Повторная обработка того же платежа приводит к лишнему чеку в
        # ЛК НПД и повторному начислению денег пользователю.
        reservation = await processed_payments.acquire(payment_id)
        if reservation == DUPLICATE:
            logger.warning(
                "Payment %s: повторный вебхук — пропускаем "
                "(чек и начисление уже выполнены)",
                payment_id,
            )
            return JSONResponse(
                status_code=200, content={"status": "duplicate"},
            )
        if reservation == IN_PROGRESS:
            logger.warning(
                "Payment %s: первое уведомление ещё обрабатывается — "
                "просим YooKassa повторить позже",
                payment_id,
            )
            # 5xx — YooKassa повторит уведомление позже
            return JSONResponse(
                status_code=500,
                content={"detail": "payment is being processed"},
            )

        # Получаем детали платежа через API YooKassa
        try:
            payment = Payment.find_one(payment_id)
        except Exception as e:
            logger.error("Failed to find payment %s: %s", payment_id, e)
            # Снимаем резерв: YooKassa повторит уведомление, и мы попробуем снова
            await processed_payments.release(payment_id)
            return JSONResponse(status_code=500, content={"detail": "payment lookup failed"})

        if payment.status != "succeeded":
            logger.info("Payment %s status is %s, skipping", payment_id, payment.status)
            await processed_payments.release(payment_id)
            # Данные в API могли ещё не обновиться — пусть ЮKassa повторит
            return JSONResponse(status_code=500, content={"detail": "payment is not succeeded yet"})

        # Извлекаем метаданные
        metadata = payment.metadata or {}
        telegram_id = metadata.get("telegram_id")
        if not telegram_id:
            logger.warning("No telegram_id in payment %s metadata", payment_id)
            await processed_payments.release(payment_id)
            return JSONResponse(status_code=500, content={"detail": "no telegram_id"})

        amount_value = float(payment.amount.value) if payment.amount else 0

        message_id = metadata.get("message_id")
        chat_id = metadata.get("chat_id")

        # Всегда создаём чек в ЛК НПД (повторно — только если чек ещё
        # не создавался: иначе в ФНС появится второй чек за один платёж)
        if payment_id in created_receipts:
            receipt_status, receipt_link = created_receipts[payment_id]
            logger.info(
                "Payment %s: используем ранее созданный чек (link=%s)",
                payment_id, receipt_link,
            )
        else:
            receipt_status, receipt_link = await create_nalog_receipt(
                inn=nalog_inn,
                password=nalog_password,
                amount=amount_value,
            )
            created_receipts[payment_id] = (receipt_status, receipt_link)

        # Публикуем NATS-сообщение для бота
        try:
            await publish_payment_success(
                nc=nc,
                subject=nats_subject,
                telegram_id=int(telegram_id),
                amount=int(amount_value),
                payment_id=payment_id,
                receipt_status=receipt_status,
                receipt_link=receipt_link,
                message_id=int(message_id) if message_id else None,
                chat_id=int(chat_id) if chat_id else None,
            )
        except Exception as e:
            # Сообщение не доставлено — снимаем резерв и отдаём 5xx,
            # чтобы YooKassa повторила уведомление.
            logger.error(
                "Failed to publish payment %s to NATS: %s", payment_id, e,
            )
            await processed_payments.release(payment_id)
            return JSONResponse(status_code=500, content={"detail": "nats publish failed"})

        # Платёж обработан — повторные вебхуки больше не начисляют деньги
        await processed_payments.mark_done(payment_id)

        return JSONResponse(status_code=200, content={"status": "succeeded"})

    return app
