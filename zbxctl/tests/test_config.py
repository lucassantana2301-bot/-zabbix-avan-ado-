from __future__ import annotations

from pathlib import Path

import pytest

from zbxctl.config import ConfigError, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "zabbix.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = """
host_groups: [Aplicações]
hosts:
  - name: demo-app
    groups: [Aplicações]
    web_checks:
      - name: Health
        url: http://demo-app:8000/health
alerting:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    chat_id: ${TELEGRAM_CHAT_ID}
"""


def test_expands_secrets_from_environment(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, VALID), env={"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-1"})
    assert cfg.alerting.telegram is not None
    assert cfg.alerting.telegram.bot_token == "123:abc"


def test_drops_telegram_when_secrets_are_absent(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, VALID), env={})
    assert cfg.alerting.telegram is None
    assert cfg.hosts[0].web_checks[0].down is not None  # trigger de queda é o padrão


def test_missing_env_outside_telegram_is_an_error(tmp_path: Path) -> None:
    text = VALID.replace("http://demo-app:8000/health", "${APP_URL}/health")
    with pytest.raises(ConfigError, match="APP_URL"):
        load_config(write(tmp_path, text), env={})


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("groups: [Aplicações]", "extra"),  # campo desconhecido no topo
        ("hosts:\n  - name: x\n    groups: [Nao declarado]", "não declarados"),
        ("hosts:\n  - name: com espaco\n    groups: [Aplicações]", "pattern"),
        (
            "hosts:\n  - name: x\n    groups: [Aplicações]\n    web_checks:\n      - {name: A, url: ftp://x}",
            "pattern",
        ),
        (
            "hosts:\n  - name: x\n    groups: [Aplicações]\n    web_checks:\n      - {name: A, url: 'http://x', interval: 10}",
            "string",
        ),
    ],
)
def test_rejects_invalid_configs(tmp_path: Path, bad: str, message: str) -> None:
    text = "host_groups: [Aplicações]\n" + bad + "\n"
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text), env={})


def test_unreadable_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="não foi possível ler"):
        load_config(write(tmp_path, "hosts: [unclosed"), env={})
