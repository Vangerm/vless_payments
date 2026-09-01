import logging

import nats

logger = logging.getLogger(__name__)


async def create_nats_connection(server_url: str):
    """Подключается к NATS."""
    nc = await nats.connect(server_url)
    logger.info("NATS connected to %s", server_url)
    return nc
