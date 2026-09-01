import logging

from nalogovich.lknpd import NpdClient
from nalogovich.enums import PaymentType

logger = logging.getLogger(__name__)


async def create_nalog_receipt(
    inn: str,
    password: str,
    amount: float,
) -> tuple[bool, str | None]:
    """Создаёт чек в ЛК НПД.

    Returns:
        (успех, ссылка на чек или None)
    """
    async with NpdClient(inn=inn, password=password, enable_logging=True) as client:
        try:
            await client.auth()
            income = await client.create_check(
                name=(
                    "Пополнение баланса для продления подписки "
                    "на услуги настройки пк и смартфонов."
                ),
                amount=amount,
                payment_type=PaymentType.WIRE,
            )
            receipt_link = (
                f"https://lknpd.nalog.ru/api/v1/receipt/"
                f"{inn}/{income.approved_receipt_uuid}/print"
            )
            logger.info(
                "Nalog receipt created: uuid=%s, link=%s",
                income.approved_receipt_uuid,
                receipt_link,
            )
            return True, receipt_link
        except Exception as e:
            logger.exception(
                "Failed to create nalog receipt: %s", e,
            )
            return False, None
