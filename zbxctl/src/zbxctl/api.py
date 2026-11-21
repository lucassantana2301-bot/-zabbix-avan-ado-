"""Cliente mínimo da API JSON-RPC do Zabbix 7.

Autenticação pelo header `Authorization: Bearer`, que é o método documentado
a partir do 7.0 (o campo `auth` no corpo está deprecado).
"""

from __future__ import annotations

import itertools
import time
from typing import Any

import httpx

JSON = dict[str, Any]


class ZabbixError(RuntimeError):
    def __init__(self, method: str, code: int, message: str, data: str) -> None:
        super().__init__(f"{method}: {message} {data}".strip())
        self.method = method
        self.code = code
        self.data = data


class ZabbixAPI:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        # Linkar template grande (ex.: Linux by Zabbix agent) cria dezenas de itens numa só chamada.
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        verify_tls: bool = True,
    ) -> None:
        self.url = url.rstrip("/")
        if not self.url.endswith("api_jsonrpc.php"):
            self.url += "/api_jsonrpc.php"
        self._token = token
        self._ids = itertools.count(1)
        self._client = httpx.Client(timeout=timeout, transport=transport, verify=verify_tls)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ZabbixAPI:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def call(self, method: str, params: JSON | list[Any] | None = None) -> Any:
        headers = {"Content-Type": "application/json-rpc"}
        # apiinfo.version e user.login são as únicas chamadas sem autenticação.
        if self._token and method not in {"apiinfo.version", "user.login"}:
            headers["Authorization"] = f"Bearer {self._token}"
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": next(self._ids)}
        response = self._client.post(self.url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            err = body["error"]
            raise ZabbixError(
                method, int(err.get("code", 0)), str(err.get("message", "")), str(err.get("data", ""))
            )
        return body["result"]

    def login(self, username: str, password: str) -> None:
        self._token = str(self.call("user.login", {"username": username, "password": password}))

    def version(self) -> str:
        return str(self.call("apiinfo.version"))

    def wait_ready(self, timeout_s: float = 300, interval_s: float = 5) -> str:
        """Espera o frontend responder: no primeiro boot o Zabbix leva 1 a 3 minutos."""
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.version()
            except (httpx.HTTPError, ZabbixError, ValueError) as exc:
                last_error = exc
                time.sleep(interval_s)
        raise TimeoutError(f"Zabbix não respondeu em {timeout_s:.0f}s: {last_error}")
