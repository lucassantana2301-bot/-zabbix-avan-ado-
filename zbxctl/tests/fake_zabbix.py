"""Zabbix falso em memória, falando JSON-RPC via httpx.MockTransport.

Implementa só os métodos usados pelo zbxctl, com as mesmas formas de
parâmetro e resposta da API 7.0, e exige o header Bearer como o real.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import httpx

TOKEN = "token-de-teste"


class FakeZabbix:
    def __init__(self) -> None:
        self._ids = itertools.count(10_000)
        self.groups: dict[str, dict[str, Any]] = {}
        self.templates = {"20001": {"templateid": "20001", "host": "Linux by Zabbix agent"}}
        self.hosts: dict[str, dict[str, Any]] = {}
        self.tests: dict[str, dict[str, Any]] = {}
        self.triggers: dict[str, dict[str, Any]] = {}
        self.users = {"1": {"userid": "1", "username": "Admin", "medias": []}}
        self.media_types: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def nid(self) -> str:
        return str(next(self._ids))

    # ------------------------------------------------------------ transporte

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        self.calls.append(method)
        if (
            method not in {"apiinfo.version", "user.login"}
            and request.headers.get("authorization") != f"Bearer {TOKEN}"
        ):
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Not authorized."},
                    "id": body["id"],
                },
            )
        handler = getattr(self, method.replace(".", "_"), None)
        if handler is None:
            raise AssertionError(f"método não implementado no fake: {method}")
        try:
            result = handler(body["params"])
        except ValueError as exc:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Invalid params.", "data": str(exc)},
                    "id": body["id"],
                },
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body["id"]})

    # ------------------------------------------------------------ métodos

    def apiinfo_version(self, _: Any) -> str:
        return "7.0.6"

    def user_login(self, p: dict[str, Any]) -> str:
        if p == {"username": "Admin", "password": "zabbix"}:
            return TOKEN
        raise ValueError("Incorrect user name or password")

    def hostgroup_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        names = set(p["filter"]["name"])
        return [g for g in self.groups.values() if g["name"] in names]

    def hostgroup_create(self, p: dict[str, Any]) -> dict[str, Any]:
        if any(g["name"] == p["name"] for g in self.groups.values()):
            raise ValueError("Host group already exists.")
        gid = self.nid()
        self.groups[gid] = {"groupid": gid, "name": p["name"]}
        return {"groupids": [gid]}

    def template_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        names = set(p["filter"]["host"])
        return [t for t in self.templates.values() if t["host"] in names]

    def host_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        names = set(p["filter"]["host"])
        out = []
        for h in self.hosts.values():
            if h["host"] in names:
                out.append(
                    {
                        "hostid": h["hostid"],
                        "host": h["host"],
                        "hostgroups": [{"groupid": g} for g in h["groups"]],
                        "parentTemplates": [{"templateid": t} for t in h["templates"]],
                        "interfaces": h["interfaces"],
                        "tags": h["tags"],
                    }
                )
        return out

    def host_create(self, p: dict[str, Any]) -> dict[str, Any]:
        if not p["groups"]:
            raise ValueError("Host must have at least one group.")
        hid = self.nid()
        self.hosts[hid] = {
            "hostid": hid,
            "host": p["host"],
            "groups": [g["groupid"] for g in p["groups"]],
            "templates": [t["templateid"] for t in p.get("templates", [])],
            "tags": p.get("tags", []),
            "interfaces": [
                {**i, "interfaceid": self.nid(), "type": str(i["type"]), "main": str(i["main"])}
                for i in p.get("interfaces", [])
            ],
        }
        return {"hostids": [hid]}

    def host_update(self, p: dict[str, Any]) -> dict[str, Any]:
        h = self.hosts[p["hostid"]]
        h["groups"] = [g["groupid"] for g in p["groups"]]
        h["templates"] = [t["templateid"] for t in p["templates"]]
        h["tags"] = p["tags"]
        return {"hostids": [p["hostid"]]}

    def hostinterface_update(self, p: dict[str, Any]) -> dict[str, Any]:
        for h in self.hosts.values():
            for i in h["interfaces"]:
                if i["interfaceid"] == p["interfaceid"]:
                    i.update({"dns": p["dns"], "port": p["port"]})
        return {"interfaceids": [p["interfaceid"]]}

    def hostinterface_create(self, p: dict[str, Any]) -> dict[str, Any]:
        iid = self.nid()
        self.hosts[p["hostid"]]["interfaces"].append(
            {**p, "interfaceid": iid, "type": str(p["type"]), "main": str(p["main"])}
        )
        return {"interfaceids": [iid]}

    def httptest_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        return [t for t in self.tests.values() if t["hostid"] in p["hostids"]]

    def httptest_create(self, p: dict[str, Any]) -> dict[str, Any]:
        tid = self.nid()
        steps = [{**s, "httpstepid": self.nid(), "no": str(s["no"])} for s in p["steps"]]
        self.tests[tid] = {
            "httptestid": tid,
            "hostid": p["hostid"],
            "name": p["name"],
            "delay": p["delay"],
            "retries": str(p["retries"]),
            "steps": steps,
            "tags": p.get("tags", []),
        }
        return {"httptestids": [tid]}

    def httptest_update(self, p: dict[str, Any]) -> dict[str, Any]:
        t = self.tests[p["httptestid"]]
        t["delay"] = p["delay"]
        t["retries"] = str(p["retries"])
        t["steps"] = [{**s, "httpstepid": s.get("httpstepid", self.nid())} for s in p["steps"]]
        return {"httptestids": [p["httptestid"]]}

    def httptest_delete(self, ids: list[str]) -> dict[str, Any]:
        for i in ids:
            self.tests.pop(i)
        return {"httptestids": ids}

    def _host_of_expression(self, expression: str) -> str:
        host = expression.split("/")[1]
        for h in self.hosts.values():
            if h["host"] == host:
                return str(h["hostid"])
        raise ValueError(f"host inexistente na expressão: {host}")

    def trigger_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        assert p.get("expandExpression") is True
        return [t for t in self.triggers.values() if t["hostid"] in p["hostids"]]

    def trigger_create(self, p: dict[str, Any]) -> dict[str, Any]:
        tid = self.nid()
        self.triggers[tid] = {
            "triggerid": tid,
            "hostid": self._host_of_expression(p["expression"]),
            "description": p["description"],
            "expression": p["expression"],
            "priority": str(p["priority"]),
            "tags": p.get("tags", []),
        }
        return {"triggerids": [tid]}

    def trigger_update(self, p: dict[str, Any]) -> dict[str, Any]:
        self.triggers[p["triggerid"]].update({"expression": p["expression"], "priority": str(p["priority"])})
        return {"triggerids": [p["triggerid"]]}

    def trigger_delete(self, ids: list[str]) -> dict[str, Any]:
        for i in ids:
            self.triggers.pop(i)
        return {"triggerids": ids}

    def user_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        names = set(p["filter"]["username"])
        return [u for u in self.users.values() if u["username"] in names]

    def user_update(self, p: dict[str, Any]) -> dict[str, Any]:
        for m in p["medias"]:
            allowed = {"mediatypeid", "sendto", "active", "severity", "period"}
            if set(m) - allowed:
                raise ValueError(f"campos inesperados em medias: {set(m) - allowed}")
        self.users[p["userid"]]["medias"] = [
            {**m, "mediaid": self.nid(), "severity": str(m["severity"])} for m in p["medias"]
        ]
        return {"userids": [p["userid"]]}

    def mediatype_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        names = set(p["filter"]["name"])
        return [m for m in self.media_types.values() if m["name"] in names]

    def mediatype_create(self, p: dict[str, Any]) -> dict[str, Any]:
        mid = self.nid()
        self.media_types[mid] = {**p, "mediatypeid": mid, "type": str(p["type"])}
        return {"mediatypeids": [mid]}

    def mediatype_update(self, p: dict[str, Any]) -> dict[str, Any]:
        self.media_types[p["mediatypeid"]].update(p)
        return {"mediatypeids": [p["mediatypeid"]]}

    def action_get(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        names = set(p["filter"]["name"])
        return [a for a in self.actions.values() if a["name"] in names]

    def action_create(self, p: dict[str, Any]) -> dict[str, Any]:
        aid = self.nid()
        conditions = [{**c, "conditiontype": str(c["conditiontype"])} for c in p["filter"]["conditions"]]
        self.actions[aid] = {**p, "actionid": aid, "filter": {**p["filter"], "conditions": conditions}}
        return {"actionids": [aid]}

    def action_update(self, p: dict[str, Any]) -> dict[str, Any]:
        a = self.actions[p["actionid"]]
        conditions = [{**c, "conditiontype": str(c["conditiontype"])} for c in p["filter"]["conditions"]]
        a.update({**p, "filter": {**p["filter"], "conditions": conditions}})
        return {"actionids": [p["actionid"]]}
