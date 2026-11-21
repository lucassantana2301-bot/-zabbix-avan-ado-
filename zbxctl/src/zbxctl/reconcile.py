"""Plan/apply idempotente, no estilo Terraform.

1. `plan()` lê o estado atual via API e compara com o YAML.
2. Cada diferença vira um `Change` com uma ação adiada (`run`).
3. `apply()` executa as mudanças em ordem; ids criados (grupo, host,
   media type) ficam num contexto compartilhado para os passos seguintes.

Web checks e triggers criados pelo zbxctl recebem a tag `managed-by=zbxctl`:
o que sair do YAML é removido no próximo apply, sem tocar no que foi criado à mão.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from zbxctl.api import JSON, ZabbixAPI
from zbxctl.config import Host, MonitoringConfig, Severity, Telegram, WebCheck

MANAGED_TAG = {"tag": "managed-by", "value": "zbxctl"}
MEDIA_TYPE_NAME = "Telegram (zbxctl)"
ACTION_NAME = "zbxctl: alertas para o Telegram"

# Tipos da API (constantes documentadas do Zabbix)
INTERFACE_AGENT = 1
MEDIA_TYPE_WEBHOOK = 4
EVENT_SOURCE_TRIGGER = 0
CONDITION_TRIGGER_SEVERITY = 4
OPERATOR_GREATER_OR_EQUAL = 5
OPERATION_SEND_MESSAGE = 0
OPERATION_NOTIFY_ALL_INVOLVED = 11

Action = Literal["create", "update", "delete"]


@dataclass
class Context:
    group_ids: dict[str, str] = field(default_factory=dict)
    host_ids: dict[str, str] = field(default_factory=dict)
    media_type_id: str | None = None


@dataclass
class Change:
    action: Action
    kind: str
    name: str
    detail: str
    run: Callable[[Context], None]

    def render(self) -> str:
        symbol = {"create": "+", "update": "~", "delete": "-"}[self.action]
        suffix = f" ({self.detail})" if self.detail else ""
        return f"  {symbol} {self.kind} {self.name!r}{suffix}"


class PlanError(ValueError):
    pass


def key_param(value: str) -> str:
    """Parâmetro de item key: aspas quando há vírgula, colchete, aspas ou espaço nas pontas."""
    if any(c in value for c in ',[]"') or value != value.strip():
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _seconds(ms: int) -> str:
    text = f"{ms / 1000:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def down_trigger(host: str, check: WebCheck) -> tuple[str, str]:
    return (
        f"{check.name} fora do ar em {host}",
        f"last(/{host}/web.test.fail[{key_param(check.name)}])<>0",
    )


def slow_trigger(host: str, check: WebCheck) -> tuple[str, str]:
    assert check.slow is not None
    n = key_param(check.name)
    return (
        f"{check.name} lento em {host}",
        f"avg(/{host}/web.test.time[{n},{n},resp],{check.slow.window})>{_seconds(check.slow.threshold_ms)}",
    )


def severity_mask(minimum: Severity) -> int:
    """Bitmask de severidades do media do usuário: todas a partir da mínima."""
    return sum(1 << s for s in range(int(minimum), 6))


TELEGRAM_SCRIPT = """\
var params = JSON.parse(value);
if (!params.token || !params.chat_id) {
    throw 'token e chat_id são obrigatórios';
}
var request = new HttpRequest();
request.addHeader('Content-Type: application/json');
var body = JSON.stringify({
    chat_id: params.chat_id,
    text: params.subject + '\\n\\n' + params.message,
    disable_web_page_preview: true
});
var response = request.post('https://api.telegram.org/bot' + params.token + '/sendMessage', body);
if (request.getStatus() !== 200) {
    throw 'Telegram respondeu ' + request.getStatus() + ': ' + response;
}
return 'OK';
"""

MESSAGE_TEMPLATES = [
    {
        "eventsource": EVENT_SOURCE_TRIGGER,
        "recovery": 0,
        "subject": "🔴 {EVENT.SEVERITY}: {EVENT.NAME}",
        "message": "Host: {HOST.NAME}\nInício: {EVENT.DATE} {EVENT.TIME}\nEvento: #{EVENT.ID}",
    },
    {
        "eventsource": EVENT_SOURCE_TRIGGER,
        "recovery": 1,
        "subject": "✅ Resolvido: {EVENT.NAME}",
        "message": (
            "Host: {HOST.NAME}\n"
            "Resolvido em {EVENT.RECOVERY.DATE} {EVENT.RECOVERY.TIME}\n"
            "Duração: {EVENT.DURATION}"
        ),
    },
]


def _tags(d: dict[str, str]) -> list[JSON]:
    return [{"tag": k, "value": v} for k, v in sorted(d.items())]


def _norm_tags(tags: list[JSON]) -> list[tuple[str, str]]:
    return sorted((str(t["tag"]), str(t.get("value", ""))) for t in tags)


class Reconciler:
    def __init__(self, api: ZabbixAPI, config: MonitoringConfig) -> None:
        self.api = api
        self.cfg = config

    # ------------------------------------------------------------------ plan

    def plan(self) -> list[Change]:
        ctx_seed = Context()
        changes: list[Change] = []
        changes += self._plan_groups(ctx_seed)
        template_ids = self._resolve_templates()
        hosts_state = self._read_hosts()
        for host in self.cfg.hosts:
            changes += self._plan_host(host, hosts_state.get(host.name), template_ids, ctx_seed)
        changes += self._plan_alerting(ctx_seed)
        self._seed = ctx_seed
        return changes

    def apply(self, changes: list[Change]) -> None:
        ctx = getattr(self, "_seed", Context())
        for change in changes:
            change.run(ctx)

    # ------------------------------------------------------------------ grupos

    def _plan_groups(self, seed: Context) -> list[Change]:
        if not self.cfg.host_groups:
            return []
        existing = self.api.call(
            "hostgroup.get", {"output": ["groupid", "name"], "filter": {"name": self.cfg.host_groups}}
        )
        for g in existing:
            seed.group_ids[g["name"]] = g["groupid"]

        changes = []
        for name in self.cfg.host_groups:
            if name in seed.group_ids:
                continue

            def create(ctx: Context, name: str = name) -> None:
                result = self.api.call("hostgroup.create", {"name": name})
                ctx.group_ids[name] = result["groupids"][0]

            changes.append(Change("create", "host group", name, "", create))
        return changes

    # ------------------------------------------------------------------ hosts

    def _resolve_templates(self) -> dict[str, str]:
        names = sorted({t for h in self.cfg.hosts for t in h.templates})
        if not names:
            return {}
        found = self.api.call("template.get", {"output": ["templateid", "host"], "filter": {"host": names}})
        ids = {t["host"]: t["templateid"] for t in found}
        missing = [n for n in names if n not in ids]
        if missing:
            raise PlanError(f"templates não encontrados no Zabbix: {missing}")
        return ids

    def _read_hosts(self) -> dict[str, JSON]:
        names = [h.name for h in self.cfg.hosts]
        if not names:
            return {}
        hosts = self.api.call(
            "host.get",
            {
                "output": ["hostid", "host"],
                "filter": {"host": names},
                "selectHostGroups": ["groupid"],
                "selectParentTemplates": ["templateid"],
                "selectInterfaces": ["interfaceid", "type", "dns", "port", "main"],
                "selectTags": ["tag", "value"],
            },
        )
        return {h["host"]: h for h in hosts}

    def _plan_host(
        self, host: Host, state: JSON | None, template_ids: dict[str, str], seed: Context
    ) -> list[Change]:
        changes: list[Change] = []
        want_templates = {template_ids[t] for t in host.templates}

        def group_refs(ctx: Context) -> list[JSON]:
            return [{"groupid": ctx.group_ids[g]} for g in host.groups]

        if state is None:

            def create(ctx: Context) -> None:
                params: JSON = {
                    "host": host.name,
                    "groups": group_refs(ctx),
                    "templates": [{"templateid": t} for t in sorted(want_templates)],
                    "tags": _tags(host.tags),
                }
                if host.agent:
                    params["interfaces"] = [
                        {
                            "type": INTERFACE_AGENT,
                            "main": 1,
                            "useip": 0,
                            "ip": "",
                            "dns": host.agent.dns,
                            "port": str(host.agent.port),
                        }
                    ]
                result = self.api.call("host.create", params)
                ctx.host_ids[host.name] = result["hostids"][0]

            changes.append(
                Change("create", "host", host.name, f"{len(host.web_checks)} web check(s)", create)
            )
            for check in host.web_checks:
                changes += self._plan_web_check(host, check, None)
                changes += self._plan_triggers(host, check, [])
            return changes

        host_id = state["hostid"]
        seed.host_ids[host.name] = host_id
        diffs: list[str] = []

        have_groups = {g["groupid"] for g in state.get("hostgroups", [])}
        want_groups_known = {seed.group_ids.get(g) for g in host.groups}
        if None in want_groups_known or have_groups != want_groups_known:
            diffs.append("grupos")
        have_templates = {t["templateid"] for t in state.get("parentTemplates", [])}
        if have_templates != want_templates:
            diffs.append("templates")
        if _norm_tags(state.get("tags", [])) != _norm_tags(_tags(host.tags)):
            diffs.append("tags")

        if diffs:

            def update(ctx: Context) -> None:
                params: JSON = {
                    "hostid": host_id,
                    "groups": group_refs(ctx),
                    "templates": [{"templateid": t} for t in sorted(want_templates)],
                    "tags": _tags(host.tags),
                }
                removed = have_templates - want_templates
                if removed:
                    # templates_clear desvincula e apaga os itens herdados.
                    params["templates_clear"] = [{"templateid": t} for t in sorted(removed)]
                self.api.call("host.update", params)

            changes.append(Change("update", "host", host.name, ", ".join(diffs), update))

        if host.agent:
            agent_ifaces = [i for i in state.get("interfaces", []) if int(i["type"]) == INTERFACE_AGENT]
            current = next((i for i in agent_ifaces if int(i.get("main", 0)) == 1), None)
            want = {"dns": host.agent.dns, "port": str(host.agent.port)}
            if current is None:

                def add_iface(_: Context) -> None:
                    self.api.call(
                        "hostinterface.create",
                        {"hostid": host_id, "type": INTERFACE_AGENT, "main": 1, "useip": 0, "ip": "", **want},
                    )

                changes.append(Change("create", "interface", f"{host.name}/agent", want["dns"], add_iface))
            elif current["dns"] != want["dns"] or str(current["port"]) != want["port"]:

                def upd_iface(_: Context, iid: str = current["interfaceid"]) -> None:
                    self.api.call("hostinterface.update", {"interfaceid": iid, "useip": 0, **want})

                changes.append(Change("update", "interface", f"{host.name}/agent", want["dns"], upd_iface))

        tests = self.api.call(
            "httptest.get",
            {
                "output": ["httptestid", "name", "delay", "retries"],
                "hostids": [host_id],
                "selectSteps": ["httpstepid", "name", "url", "status_codes", "required", "timeout", "no"],
                "selectTags": ["tag", "value"],
            },
        )
        triggers = self.api.call(
            "trigger.get",
            {
                "output": ["triggerid", "description", "expression", "priority"],
                "hostids": [host_id],
                "expandExpression": True,
                "selectTags": ["tag", "value"],
            },
        )
        by_name = {t["name"]: t for t in tests}
        for check in host.web_checks:
            changes += self._plan_web_check(host, check, by_name.get(check.name))
            changes += self._plan_triggers(host, check, triggers)

        # Remoção do que é gerenciado pelo zbxctl e saiu do YAML.
        wanted_checks = {c.name for c in host.web_checks}
        for t in tests:
            if t["name"] not in wanted_checks and _is_managed(t):

                def drop_test(_: Context, tid: str = t["httptestid"]) -> None:
                    self.api.call("httptest.delete", [tid])

                changes.append(Change("delete", "web check", f"{host.name}/{t['name']}", "", drop_test))

        wanted_triggers = {down_trigger(host.name, c)[0] for c in host.web_checks if c.down} | {
            slow_trigger(host.name, c)[0] for c in host.web_checks if c.slow
        }
        for tr in triggers:
            if tr["description"] not in wanted_triggers and _is_managed(tr):

                def drop_trigger(_: Context, tid: str = tr["triggerid"]) -> None:
                    self.api.call("trigger.delete", [tid])

                changes.append(Change("delete", "trigger", tr["description"], "", drop_trigger))
        return changes

    def _plan_web_check(self, host: Host, check: WebCheck, state: JSON | None) -> list[Change]:
        step: JSON = {
            "no": 1,
            "name": check.name,
            "url": check.url,
            "status_codes": check.status_codes,
            "timeout": check.timeout,
            "required": check.required or "",
            "follow_redirects": 1,
        }
        label = f"{host.name}/{check.name}"

        if state is None:

            def create(ctx: Context) -> None:
                self.api.call(
                    "httptest.create",
                    {
                        "name": check.name,
                        "hostid": ctx.host_ids[host.name],
                        "delay": check.interval,
                        "retries": check.retries,
                        "steps": [step],
                        "tags": [MANAGED_TAG],
                    },
                )

            return [Change("create", "web check", label, check.url, create)]

        current = (state.get("steps") or [{}])[0]
        diffs = []
        if state["delay"] != check.interval or int(state["retries"]) != check.retries:
            diffs.append("intervalo/tentativas")
        for key in ("url", "status_codes", "timeout", "required"):
            if str(current.get(key, "")) != str(step[key]):
                diffs.append(key)
        if not diffs:
            return []

        def update(_: Context) -> None:
            new_step = dict(step)
            if current.get("httpstepid"):
                new_step["httpstepid"] = current["httpstepid"]
            self.api.call(
                "httptest.update",
                {
                    "httptestid": state["httptestid"],
                    "delay": check.interval,
                    "retries": check.retries,
                    "steps": [new_step],
                },
            )

        return [Change("update", "web check", label, ", ".join(diffs), update)]

    def _plan_triggers(self, host: Host, check: WebCheck, existing: list[JSON]) -> list[Change]:
        wanted: list[tuple[str, str, int]] = []
        if check.down:
            d, e = down_trigger(host.name, check)
            wanted.append((d, e, int(Severity[check.down.severity])))
        if check.slow:
            d, e = slow_trigger(host.name, check)
            wanted.append((d, e, int(Severity[check.slow.severity])))

        by_desc = {t["description"]: t for t in existing}
        changes = []
        for description, expression, priority in wanted:
            current = by_desc.get(description)
            if current is None:

                def create(_: Context, d: str = description, e: str = expression, p: int = priority) -> None:
                    self.api.call(
                        "trigger.create",
                        {
                            "description": d,
                            "expression": e,
                            "priority": p,
                            "manual_close": 1,
                            "tags": [MANAGED_TAG],
                        },
                    )

                changes.append(Change("create", "trigger", description, Severity(priority).name, create))
            elif current["expression"] != expression or int(current["priority"]) != priority:

                def update(
                    _: Context, tid: str = current["triggerid"], e: str = expression, p: int = priority
                ) -> None:
                    self.api.call("trigger.update", {"triggerid": tid, "expression": e, "priority": p})

                changes.append(Change("update", "trigger", description, "expressão/severidade", update))
        return changes

    # ------------------------------------------------------------------ alertas

    def _plan_alerting(self, seed: Context) -> list[Change]:
        tg = self.cfg.alerting.telegram
        if tg is None:
            return []
        changes: list[Change] = []

        users = self.api.call(
            "user.get",
            {
                "output": ["userid", "username"],
                "filter": {"username": [tg.notify_user]},
                "selectMedias": "extend",
            },
        )
        if not users:
            raise PlanError(f"usuário {tg.notify_user!r} não existe no Zabbix")
        user = users[0]

        media_types = self.api.call(
            "mediatype.get",
            {"output": "extend", "filter": {"name": [MEDIA_TYPE_NAME]}, "selectMessageTemplates": "extend"},
        )
        params = _media_params(tg)
        if not media_types:

            def create_mt(ctx: Context) -> None:
                result = self.api.call(
                    "mediatype.create",
                    {
                        "name": MEDIA_TYPE_NAME,
                        "type": MEDIA_TYPE_WEBHOOK,
                        "script": TELEGRAM_SCRIPT,
                        "timeout": "15s",
                        "parameters": params,
                        "message_templates": MESSAGE_TEMPLATES,
                    },
                )
                ctx.media_type_id = result["mediatypeids"][0]

            changes.append(Change("create", "media type", MEDIA_TYPE_NAME, "webhook", create_mt))
        else:
            mt = media_types[0]
            seed.media_type_id = mt["mediatypeid"]
            current_params = sorted((p["name"], p["value"]) for p in mt.get("parameters", []))
            if mt.get("script") != TELEGRAM_SCRIPT or current_params != sorted(
                (p["name"], p["value"]) for p in params
            ):

                def update_mt(_: Context, mid: str = mt["mediatypeid"]) -> None:
                    self.api.call(
                        "mediatype.update",
                        {
                            "mediatypeid": mid,
                            "script": TELEGRAM_SCRIPT,
                            "parameters": params,
                            "message_templates": MESSAGE_TEMPLATES,
                        },
                    )

                changes.append(
                    Change("update", "media type", MEDIA_TYPE_NAME, "script/parâmetros", update_mt)
                )

        mask = severity_mask(Severity[tg.min_severity])
        mine = [
            m
            for m in user.get("medias", [])
            if seed.media_type_id and m.get("mediatypeid") == seed.media_type_id
        ]
        media_ok = (
            len(mine) == 1 and mine[0].get("sendto") == tg.chat_id and int(mine[0].get("severity", 0)) == mask
        )
        if not media_ok:

            def set_media(ctx: Context) -> None:
                assert ctx.media_type_id is not None
                others = [
                    _clean_media(m)
                    for m in user.get("medias", [])
                    if m.get("mediatypeid") != ctx.media_type_id
                ]
                ours = {
                    "mediatypeid": ctx.media_type_id,
                    "sendto": tg.chat_id,
                    "active": 0,
                    "severity": mask,
                    "period": "1-7,00:00-24:00",
                }
                self.api.call("user.update", {"userid": user["userid"], "medias": [*others, ours]})

            changes.append(
                Change("update", "user media", tg.notify_user, f"a partir de {tg.min_severity}", set_media)
            )

        actions = self.api.call(
            "action.get",
            {"output": ["actionid", "name"], "filter": {"name": [ACTION_NAME]}, "selectFilter": "extend"},
        )
        min_sev = str(int(Severity[tg.min_severity]))

        def action_body(ctx: Context) -> JSON:
            assert ctx.media_type_id is not None
            return {
                "filter": {
                    "evaltype": 0,
                    "conditions": [
                        {
                            "conditiontype": CONDITION_TRIGGER_SEVERITY,
                            "operator": OPERATOR_GREATER_OR_EQUAL,
                            "value": min_sev,
                        }
                    ],
                },
                "operations": [
                    {
                        "operationtype": OPERATION_SEND_MESSAGE,
                        "opmessage": {"default_msg": 1, "mediatypeid": ctx.media_type_id},
                        "opmessage_usr": [{"userid": user["userid"]}],
                    }
                ],
                "recovery_operations": [
                    {"operationtype": OPERATION_NOTIFY_ALL_INVOLVED, "opmessage": {"default_msg": 1}}
                ],
            }

        if not actions:

            def create_action(ctx: Context) -> None:
                self.api.call(
                    "action.create",
                    {
                        "name": ACTION_NAME,
                        "eventsource": EVENT_SOURCE_TRIGGER,
                        "esc_period": "1h",
                        **action_body(ctx),
                    },
                )

            changes.append(
                Change("create", "action", ACTION_NAME, f"severidade >= {tg.min_severity}", create_action)
            )
        else:
            conditions = actions[0].get("filter", {}).get("conditions", [])
            current_min = next(
                (c["value"] for c in conditions if int(c["conditiontype"]) == CONDITION_TRIGGER_SEVERITY),
                None,
            )
            if current_min != min_sev or not seed.media_type_id:

                def update_action(ctx: Context, aid: str = actions[0]["actionid"]) -> None:
                    self.api.call("action.update", {"actionid": aid, **action_body(ctx)})

                changes.append(
                    Change("update", "action", ACTION_NAME, f"severidade >= {tg.min_severity}", update_action)
                )
        return changes


def _media_params(tg: Telegram) -> list[JSON]:
    return [
        {"name": "chat_id", "value": "{ALERT.SENDTO}"},
        {"name": "message", "value": "{ALERT.MESSAGE}"},
        {"name": "subject", "value": "{ALERT.SUBJECT}"},
        {"name": "token", "value": tg.bot_token},
    ]


def _clean_media(m: JSON) -> JSON:
    """user.update só aceita estes campos em medias; o resto é somente leitura."""
    return {k: m[k] for k in ("mediatypeid", "sendto", "active", "severity", "period") if k in m}


def _is_managed(obj: JSON) -> bool:
    return any(
        t.get("tag") == MANAGED_TAG["tag"] and t.get("value") == MANAGED_TAG["value"]
        for t in obj.get("tags", [])
    )


def summarize(changes: list[Change]) -> str:
    counts: dict[Any, int] = {"create": 0, "update": 0, "delete": 0}
    for c in changes:
        counts[c.action] += 1
    return (
        f"Plano: {counts['create']} para criar, {counts['update']} para alterar, "
        f"{counts['delete']} para remover."
    )
