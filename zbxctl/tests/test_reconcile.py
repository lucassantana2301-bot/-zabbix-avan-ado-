from __future__ import annotations

from pathlib import Path

import pytest

from zbxctl.api import ZabbixAPI, ZabbixError
from zbxctl.config import MonitoringConfig, Severity
from zbxctl.reconcile import ACTION_NAME, MEDIA_TYPE_NAME, PlanError, Reconciler, key_param, severity_mask

from .fake_zabbix import TOKEN, FakeZabbix

BASE = {
    "host_groups": ["Aplicações", "Infraestrutura"],
    "hosts": [
        {
            "name": "zabbix-agent",
            "groups": ["Infraestrutura"],
            "templates": ["Linux by Zabbix agent"],
            "agent": {"dns": "zabbix-agent"},
            "tags": {"env": "demo"},
        },
        {
            "name": "demo-app",
            "groups": ["Aplicações"],
            "tags": {"env": "demo", "service": "loja"},
            "web_checks": [
                {
                    "name": "Health",
                    "url": "http://demo-app:8000/health",
                    "required": '"status":"ok"',
                    "interval": "15s",
                    "down": {"severity": "high"},
                    "slow": {"threshold_ms": 1500, "window": "1m"},
                }
            ],
        },
    ],
    "alerting": {"telegram": {"bot_token": "123:abc", "chat_id": "-100200", "min_severity": "warning"}},
}


def cfg(**overrides: object) -> MonitoringConfig:
    return MonitoringConfig.model_validate({**BASE, **overrides})


@pytest.fixture
def fake() -> FakeZabbix:
    return FakeZabbix()


@pytest.fixture
def api(fake: FakeZabbix) -> ZabbixAPI:
    return ZabbixAPI("http://zabbix.test", token=TOKEN, transport=fake.transport())


def apply(api: ZabbixAPI, config: MonitoringConfig) -> list[str]:
    r = Reconciler(api, config)
    changes = r.plan()
    r.apply(changes)
    return [c.render() for c in changes]


def test_first_apply_creates_everything_and_second_plan_is_empty(api: ZabbixAPI, fake: FakeZabbix) -> None:
    rendered = apply(api, cfg())
    assert "  + host group 'Aplicações'" in rendered
    assert any("+ web check 'demo-app/Health'" in line for line in rendered)
    assert any("+ trigger 'Health fora do ar em demo-app'" in line for line in rendered)
    assert any("+ action" in line for line in rendered)

    host = next(h for h in fake.hosts.values() if h["host"] == "demo-app")
    trigger_expr = {t["description"]: t["expression"] for t in fake.triggers.values()}
    assert trigger_expr["Health fora do ar em demo-app"] == "last(/demo-app/web.test.fail[Health])<>0"
    assert (
        trigger_expr["Health lento em demo-app"] == "avg(/demo-app/web.test.time[Health,Health,resp],1m)>1.5"
    )
    assert host["tags"] == [{"tag": "env", "value": "demo"}, {"tag": "service", "value": "loja"}]

    agent = next(h for h in fake.hosts.values() if h["host"] == "zabbix-agent")
    assert agent["templates"] == ["20001"]
    assert agent["interfaces"][0]["dns"] == "zabbix-agent"

    media = fake.users["1"]["medias"]
    assert media[0]["sendto"] == "-100200"
    assert int(media[0]["severity"]) == severity_mask(Severity.warning)
    action = next(iter(fake.actions.values()))
    assert action["name"] == ACTION_NAME
    assert action["filter"]["conditions"][0]["value"] == "2"
    assert next(iter(fake.media_types.values()))["name"] == MEDIA_TYPE_NAME

    # Idempotência: o segundo plano não encontra nada a fazer.
    assert Reconciler(api, cfg()).plan() == []


def test_changes_are_detected_as_updates(api: ZabbixAPI, fake: FakeZabbix) -> None:
    apply(api, cfg())
    hosts = list(BASE["hosts"])  # type: ignore[call-overload]
    demo = {
        **hosts[1],
        "web_checks": [{**hosts[1]["web_checks"][0], "interval": "30s", "slow": {"threshold_ms": 800}}],
    }
    changed = cfg(
        hosts=[hosts[0], demo],
        alerting={"telegram": {"bot_token": "123:abc", "chat_id": "-100200", "min_severity": "high"}},
    )

    rendered = apply(api, changed)
    assert any("~ web check 'demo-app/Health' (intervalo/tentativas)" in line for line in rendered)
    assert any("~ trigger 'Health lento em demo-app'" in line for line in rendered)
    assert any("~ action" in line for line in rendered)
    assert any("~ user media" in line for line in rendered)
    assert next(iter(fake.tests.values()))["delay"] == "30s"
    assert Reconciler(api, changed).plan() == []


def test_removed_check_deletes_only_managed_objects(api: ZabbixAPI, fake: FakeZabbix) -> None:
    apply(api, cfg())
    host_id = next(h["hostid"] for h in fake.hosts.values() if h["host"] == "demo-app")
    # Trigger criado à mão no Zabbix, sem a tag managed-by.
    fake.trigger_create({"description": "Manual", "expression": "last(/demo-app/x)>0", "priority": 1})

    hosts = list(BASE["hosts"])  # type: ignore[call-overload]
    without_checks = cfg(hosts=[hosts[0], {**hosts[1], "web_checks": []}])
    rendered = apply(api, without_checks)
    assert "  - web check 'demo-app/Health'" in rendered
    assert sum(line.startswith("  - trigger") for line in rendered) == 2
    remaining = [t["description"] for t in fake.triggers.values() if t["hostid"] == host_id]
    assert remaining == ["Manual"]


def test_missing_template_fails_before_any_change(api: ZabbixAPI, fake: FakeZabbix) -> None:
    hosts = list(BASE["hosts"])  # type: ignore[call-overload]
    bad = cfg(hosts=[{**hosts[0], "templates": ["Template que não existe"]}])
    with pytest.raises(PlanError, match="não encontrados"):
        Reconciler(api, bad).plan()
    assert not any(c.endswith(".create") for c in fake.calls)


def test_without_telegram_no_alerting_objects(api: ZabbixAPI, fake: FakeZabbix) -> None:
    apply(api, cfg(alerting={}))
    assert fake.media_types == {}
    assert fake.actions == {}


def test_api_requires_auth_and_reports_errors(fake: FakeZabbix) -> None:
    anonymous = ZabbixAPI("http://zabbix.test", transport=fake.transport())
    assert anonymous.version() == "7.0.6"
    with pytest.raises(ZabbixError, match="Not authorized"):
        anonymous.call("host.get", {"filter": {"host": []}})
    anonymous.login("Admin", "zabbix")
    assert anonymous.call("hostgroup.get", {"output": [], "filter": {"name": []}}) == []
    with pytest.raises(ZabbixError, match="Incorrect"):
        anonymous.login("Admin", "errada")


def test_url_normalization() -> None:
    assert ZabbixAPI("http://z:8080/").url == "http://z:8080/api_jsonrpc.php"
    assert ZabbixAPI("http://z/zabbix/api_jsonrpc.php").url == "http://z/zabbix/api_jsonrpc.php"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Health", "Health"),
        ("API Pedidos", "API Pedidos"),
        ("a,b", '"a,b"'),
        ('x"y', '"x\\"y"'),
        (" pad", '" pad"'),
    ],
)
def test_key_param_quoting(raw: str, expected: str) -> None:
    assert key_param(raw) == expected


def test_example_config_in_repo_is_valid() -> None:
    from zbxctl.config import load_config

    path = Path(__file__).resolve().parents[2] / "monitoring" / "zabbix.yaml"
    config = load_config(path, env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"})
    assert config.alerting.telegram is not None
    assert {h.name for h in config.hosts} >= {"demo-app"}
