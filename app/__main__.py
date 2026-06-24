import asyncio
import logging.config

import uvicorn

from yookassa import Configuration

from app.config_reader import (
    get_config,
    PaymentConfig,
    NalogConfig,
    NatsConfig,
    ServerConfig,
)
from app.nats_client import create_nats_connection
from app.webhook_server import create_app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Starting vless_payments service")

    # Конфигурация
    payment_config = get_config(PaymentConfig, "payment")
    nalog_config = get_config(NalogConfig, "nalog")
    nats_config = get_config(NatsConfig, "nats")
    server_config = get_config(ServerConfig, "server")

    # Инициализация YooKassa SDK
    Configuration.account_id = payment_config.yookassa_shop_id
    Configuration.secret_key = payment_config.yookassa_secret_key.get_secret_value()
    logger.info(
        "YooKassa configured: shop_id=%s",
        payment_config.yookassa_shop_id,
    )

    # Подключение к NATS
    nc = await create_nats_connection(nats_config.nats_server)

    # Создание FastAPI приложения
    app = create_app(
        nc=nc,
        nats_subject=nats_config.nats_yookassa_payment_subject,
        nalog_inn=nalog_config.inn.get_secret_value(),
        nalog_password=nalog_config.password.get_secret_value(),
    )

    # Запуск веб-сервера
    ssl_kwargs = {}
    if server_config.ssl_keyfile and server_config.ssl_certfile:
        ssl_kwargs["ssl_keyfile"] = server_config.ssl_keyfile
        ssl_kwargs["ssl_certfile"] = server_config.ssl_certfile

    config = uvicorn.Config(
        app,
        host=server_config.host,
        port=server_config.port,
        log_level="info",
        **ssl_kwargs,
    )
    server = uvicorn.Server(config)

    logger.info(
        "Starting webhook server on %s:%s",
        server_config.host,
        server_config.port,
    )

    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await nc.close()
        logger.info("NATS connection closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
