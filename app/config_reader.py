from functools import lru_cache
from os import getenv
from typing import TypeVar, Type

from pydantic import BaseModel, SecretStr
from yaml import load

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader


ConfigType = TypeVar("ConfigType", bound=BaseModel)


class PaymentConfig(BaseModel):
    yookassa_shop_id: int
    yookassa_secret_key: SecretStr


class NalogConfig(BaseModel):
    inn: SecretStr
    password: SecretStr


class NatsConfig(BaseModel):
    nats_server: str
    nats_yookassa_payment_subject: str


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8443
    ssl_keyfile: str | None = None
    ssl_certfile: str | None = None


@lru_cache(maxsize=1)
def parse_config_file() -> dict:
    file_path = getenv("PAYMENTS_CONFIG")
    if file_path is None:
        raise ValueError("PAYMENTS_CONFIG environment variable not set")
    with open(file_path, "rb") as file:
        config_data = load(file, Loader=SafeLoader)
    return config_data


@lru_cache
def get_config(model: Type[ConfigType], root_key: str) -> ConfigType:
    config_dict = parse_config_file()
    if root_key not in config_dict:
        raise ValueError(f"Key {root_key} not found")
    return model.model_validate(config_dict[root_key])
