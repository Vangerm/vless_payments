"""Tests for vless_payments config reader."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from app.config_reader import (
    parse_config_file,
    get_config,
    PaymentConfig,
    NalogConfig,
    NatsConfig,
    ServerConfig,
)


SAMPLE_CONFIG = {
    "payment": {
        "yookassa_shop_id": 1144921,
        "yookassa_secret_key": "test_secret",
    },
    "nalog": {
        "inn": "1234567890",
        "password": "nalog_pass",
    },
    "nats": {
        "nats_server": "nats://localhost:4222",
        "nats_yookassa_payment_subject": "payment.yookassa.succeeded",
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8443,
        "ssl_keyfile": "key.pem",
        "ssl_certfile": "cert.pem",
    },
}


@pytest.fixture
def config_file():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(SAMPLE_CONFIG, f)
        config_path = f.name

    old_env = os.environ.get("PAYMENTS_CONFIG")
    os.environ["PAYMENTS_CONFIG"] = config_path

    # Clear lru_cache so get_config re-parses
    parse_config_file.cache_clear()

    yield Path(config_path)

    os.environ.pop("PAYMENTS_CONFIG", None)
    if old_env is not None:
        os.environ["PAYMENTS_CONFIG"] = old_env
    os.unlink(config_path)


class TestConfigReader:
    def test_parse_config_file(self, config_file):
        data = parse_config_file()
        assert data["payment"]["yookassa_shop_id"] == 1144921
        assert data["nats"]["nats_server"] == "nats://localhost:4222"

    def test_get_payment_config(self, config_file):
        cfg = get_config(PaymentConfig, "payment")
        assert cfg.yookassa_shop_id == 1144921
        assert cfg.yookassa_secret_key.get_secret_value() == "test_secret"

    def test_get_nalog_config(self, config_file):
        cfg = get_config(NalogConfig, "nalog")
        assert cfg.inn.get_secret_value() == "1234567890"

    def test_get_nats_config(self, config_file):
        cfg = get_config(NatsConfig, "nats")
        assert cfg.nats_server == "nats://localhost:4222"
        assert cfg.nats_yookassa_payment_subject == "payment.yookassa.succeeded"

    def test_get_server_config(self, config_file):
        cfg = get_config(ServerConfig, "server")
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8443
        assert cfg.ssl_keyfile == "key.pem"

    def test_missing_env_var(self):
        old = os.environ.pop("PAYMENTS_CONFIG", None)
        parse_config_file.cache_clear()
        with pytest.raises(ValueError, match="PAYMENTS_CONFIG"):
            parse_config_file()
        if old is not None:
            os.environ["PAYMENTS_CONFIG"] = old

    def test_missing_key(self, config_file):
        with pytest.raises(ValueError, match="Key nonexistent"):
            get_config(PaymentConfig, "nonexistent")
