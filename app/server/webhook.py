import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from yookassa import Configuration, Payment

from app.nats_client.publisher import publish_payment_success
from app.services.receipt_service import create_nalog_receipt

logger = logging.getLogger(__name__)


def create_app(nc, nats_subject: str, nalog_inn: str, nalog_password: str):
    """Фабрика FastAPI-приложения с замыканием зависимостей."""

    app = FastAPI(title="YooKassa Webhook")

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

        status = _find_status(payload)
        logger.info("YooKassa webhook received: status=%s", status)

        if status != "succeeded":
            # Для не-succeeded событий просто подтверждаем получение
            return JSONResponse(status_code=202, content={"status": status or "unknown"})

        # Извлекаем объект платежа (для YooKassa v3/v2)
        payment_data = _extract_object(payload)
        if not payment_data:
            logger.warning("No payment object found in payload")
            return JSONResponse(status_code=202, content={"status": "no_object"})

        payment_id = payment_data.get("id")
        if not payment_id:
            logger.warning("No payment id in payload")
            return JSONResponse(status_code=202, content={"status": "no_id"})

        # Получаем детали платежа через API YooKassa
        try:
            payment = Payment.find_one(payment_id)
        except Exception as e:
            logger.error("Failed to find payment %s: %s", payment_id, e)
            return JSONResponse(status_code=500, content={"detail": "payment lookup failed"})

        if payment.status != "succeeded":
            logger.info("Payment %s status is %s, skipping", payment_id, payment.status)
            return JSONResponse(status_code=202, content={"status": payment.status})

        # Извлекаем метаданные
        metadata = payment.metadata or {}
        telegram_id = metadata.get("telegram_id")
        if not telegram_id:
            logger.warning("No telegram_id in payment %s metadata", payment_id)
            return JSONResponse(status_code=202, content={"status": "no_telegram_id"})

        amount_value = float(payment.amount.value) if payment.amount else 0

        message_id = metadata.get("message_id")
        chat_id = metadata.get("chat_id")

        # Всегда создаём чек в ЛК НПД
        receipt_status, receipt_link = await create_nalog_receipt(
            inn=nalog_inn,
            password=nalog_password,
            amount=amount_value,
        )

        # Публикуем NATS-сообщение для бота
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

        return JSONResponse(status_code=200, content={"status": "succeeded"})

    return app
