"""Teste ponta a ponta contra a stack real (usado no CI).

1. Espera o web check do demo-app ficar saudável e o agente reportar CPU.
2. Derruba o app (modo caos) e espera o Zabbix abrir o problema.
3. Religa o app e espera o problema ser resolvido.
4. Confere se o Grafana conecta no Zabbix pelo data source provisionado.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx
from zbxctl.api import ZabbixAPI

ZABBIX_URL = os.getenv("ZABBIX_URL", "http://localhost:8080")
APP_URL = os.getenv("APP_URL", "http://localhost:8000")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
PROBLEM = "Health fora do ar em demo-app"


def wait_for(label: str, check: Callable[[], Any], timeout: float = 240, every: float = 5) -> Any:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        result = check()
        if result:
            print(f"ok   {label} ({time.monotonic() - start:.0f}s)")
            return result
        time.sleep(every)
    print(f"FALHA {label}: não aconteceu em {timeout:.0f}s", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    api = ZabbixAPI(ZABBIX_URL)
    api.login(os.getenv("ZABBIX_USER", "Admin"), os.getenv("ZABBIX_PASSWORD", "zabbix"))

    hosts = {h["host"]: h["hostid"] for h in api.call("host.get", {"output": ["hostid", "host"]})}
    demo, agent = hosts["demo-app"], hosts["zabbix-agent"]

    def item_value(hostid: str, key: str) -> str | None:
        items = api.call(
            "item.get", {"output": ["lastvalue", "lastclock"], "hostids": [hostid], "filter": {"key_": key}}
        )
        if items and int(items[0]["lastclock"]) > 0:
            return str(items[0]["lastvalue"])
        return None

    def problems() -> list[str]:
        return [p["name"] for p in api.call("problem.get", {"output": ["name"], "hostids": [demo]})]

    wait_for("web check do demo-app saudável", lambda: item_value(demo, "web.test.fail[Health]") == "0")
    wait_for("agente reportando CPU", lambda: item_value(agent, "system.cpu.util") is not None)

    httpx.post(f"{APP_URL}/chaos/down", timeout=5).raise_for_status()
    wait_for("Zabbix abriu o problema de queda", lambda: PROBLEM in problems(), timeout=180)

    httpx.post(f"{APP_URL}/chaos/normal", timeout=5).raise_for_status()
    wait_for("problema resolvido após religar", lambda: PROBLEM not in problems(), timeout=180)

    auth = ("admin", os.getenv("GRAFANA_ADMIN_PASSWORD", "admin"))

    def grafana_ok() -> bool:
        try:
            r = httpx.get(f"{GRAFANA_URL}/api/datasources/uid/zabbix/health", auth=auth, timeout=10)
        except httpx.HTTPError:
            return False
        return r.status_code == 200 and r.json().get("status") == "OK"

    wait_for("Grafana conectado ao Zabbix", grafana_ok, timeout=120)
    print("E2E concluído: monitoramento, alerta e dashboard funcionando.")


if __name__ == "__main__":
    main()
