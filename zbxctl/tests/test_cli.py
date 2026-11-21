from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zbxctl import cli
from zbxctl.api import ZabbixAPI

from .fake_zabbix import FakeZabbix

CONFIG = Path(__file__).resolve().parents[2] / "monitoring" / "zabbix.yaml"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeZabbix:
    fake = FakeZabbix()

    class Patched(ZabbixAPI):
        def __init__(self, url: str, **kwargs: Any) -> None:
            super().__init__(url, transport=fake.transport(), **kwargs)

    monkeypatch.setattr(cli, "ZabbixAPI", Patched)
    monkeypatch.setenv("ZABBIX_PASSWORD", "zabbix")
    monkeypatch.delenv("ZABBIX_API_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    return fake


def test_validate(capsys: pytest.CaptureFixture[str], fake: FakeZabbix) -> None:
    assert cli.main(["validate", "-f", str(CONFIG)]) == cli.EXIT_OK
    assert "2 host(s), 2 web check(s), sem alertas" in capsys.readouterr().out


def test_plan_apply_cycle_with_detailed_exitcode(
    capsys: pytest.CaptureFixture[str], fake: FakeZabbix
) -> None:
    assert cli.main(["plan", "-f", str(CONFIG), "--detailed-exitcode"]) == cli.EXIT_CHANGES
    assert "para criar" in capsys.readouterr().out

    assert cli.main(["apply", "-f", str(CONFIG)]) == cli.EXIT_OK
    assert "Aplicado." in capsys.readouterr().out

    assert cli.main(["plan", "-f", str(CONFIG), "--detailed-exitcode"]) == cli.EXIT_OK
    assert "Nenhuma mudança" in capsys.readouterr().out


def test_wrong_password_is_reported(
    capsys: pytest.CaptureFixture[str], fake: FakeZabbix, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZABBIX_PASSWORD", "errada")
    assert cli.main(["plan", "-f", str(CONFIG)]) == cli.EXIT_ERROR
    assert "erro da API do Zabbix" in capsys.readouterr().err


def test_missing_credentials(
    capsys: pytest.CaptureFixture[str], fake: FakeZabbix, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ZABBIX_PASSWORD")
    assert cli.main(["plan", "-f", str(CONFIG)]) == cli.EXIT_ERROR
    assert "ZABBIX_PASSWORD" in capsys.readouterr().err
