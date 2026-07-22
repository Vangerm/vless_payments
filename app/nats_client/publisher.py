import json
import logging

logger = logging.getLogger(__name__)


async def publish_payment_success(
    nc,
    subject: str,
    telegram_id: int,
    amount: int,
    payment_id: str,
    receipt_status: bool = False,
    receipt_link: str | None = None,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> None:
    """Публикует сообщение об успешном платеже в NATS."""
    payload = {
        "telegram_id": str(telegram_id),
        "amount": str(amount),
        "payment_id": payment_id,
        "receipt_status": receipt_status,
        "receipt_link": receipt_link,
    }
    if message_id is not None:
        payload["message_id"] = str(message_id)
    if chat_id is not None:
        payload["chat_id"] = str(chat_id)

    message = {
        "correlation_id": payment_id,
        "action": "payment.yookassa.succeeded",
        "payload": payload,
    }

    await nc.publish(subject, json.dumps(message).encode("utf-8"))
    logger.info(
        "Published payment success: user=%s, amount=%s, subject=%s",
        telegram_id, amount, subject,
    )
