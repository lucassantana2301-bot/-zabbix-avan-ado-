"""Esquema declarativo do monitoramento (monitoring/zabbix.yaml).

Validado com Pydantic antes de qualquer chamada à API: erro de digitação
no YAML falha no `zbxctl validate`, no CI, e nunca em produção.
"""

from __future__ import annotations

import os
import re
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Severity(IntEnum):
    """Mapeamento oficial de severidade de trigger do Zabbix."""

    not_classified = 0
    information = 1
    warning = 2
    average = 3
    high = 4
    disaster = 5


SeverityName = Literal["not_classified", "information", "warning", "average", "high", "disaster"]
Duration = Annotated[str, Field(pattern=r"^\d+[smhd]$")]

_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DownTrigger(Strict):
    severity: SeverityName = "high"


class SlowTrigger(Strict):
    threshold_ms: int = Field(gt=0, le=60_000)
    window: Duration = "2m"
    severity: SeverityName = "warning"


class WebCheck(Strict):
    """Cenário web de um passo: GET na URL, valida status e (opcional) conteúdo."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[\w\- ]+$")
    url: str = Field(pattern=r"^https?://")
    status_codes: str = Field(default="200", pattern=r"^[\d,\- ]+$")
    required: str | None = Field(default=None, max_length=255)
    interval: Duration = "30s"
    timeout: Duration = "10s"
    retries: int = Field(default=1, ge=1, le=10)
    down: DownTrigger | None = DownTrigger()
    slow: SlowTrigger | None = None


class AgentInterface(Strict):
    dns: str = Field(min_length=1)
    port: int = Field(default=10050, ge=1, le=65535)


class Host(Strict):
    # Sem espaço: o nome técnico do host entra nas expressões de trigger.
    name: str = Field(min_length=1, max_length=128, pattern=r"^[\w.\-]+$")
    groups: list[str] = Field(min_length=1)
    templates: list[str] = []
    agent: AgentInterface | None = None
    tags: dict[str, str] = {}
    web_checks: list[WebCheck] = []

    @model_validator(mode="after")
    def unique_checks(self) -> Host:
        names = [c.name for c in self.web_checks]
        if len(names) != len(set(names)):
            raise ValueError(f"host {self.name}: web_checks com nomes repetidos")
        return self


class Telegram(Strict):
    bot_token: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    min_severity: SeverityName = "warning"
    notify_user: str = "Admin"


class Alerting(Strict):
    telegram: Telegram | None = None


class MonitoringConfig(Strict):
    host_groups: list[str] = []
    hosts: list[Host] = []
    alerting: Alerting = Alerting()

    @model_validator(mode="after")
    def consistent(self) -> MonitoringConfig:
        host_names = [h.name for h in self.hosts]
        if len(host_names) != len(set(host_names)):
            raise ValueError("hosts com nomes repetidos")
        declared = set(self.host_groups)
        for h in self.hosts:
            missing = set(h.groups) - declared
            if missing:
                raise ValueError(f"host {h.name}: grupos não declarados em host_groups: {sorted(missing)}")
        return self

    @field_validator("host_groups")
    @classmethod
    def unique_groups(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("host_groups com nomes repetidos")
        return v


class ConfigError(ValueError):
    pass


def _expand_env(node: object, env: dict[str, str], missing: set[str]) -> object:
    """Substitui ${VAR} por variáveis de ambiente: segredos nunca ficam no YAML versionado."""
    if isinstance(node, str):

        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in env:
                missing.add(name)
                return ""
            return env[name]

        return _ENV_REF.sub(repl, node)
    if isinstance(node, list):
        return [_expand_env(v, env, missing) for v in node]
    if isinstance(node, dict):
        return {k: _expand_env(v, env, missing) for k, v in node.items()}
    return node


def load_config(path: Path, env: dict[str, str] | None = None) -> MonitoringConfig:
    env = dict(os.environ) if env is None else env
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"não foi possível ler {path}: {exc}") from exc

    # Telegram é opcional: sem as variáveis, o bloco é descartado em vez de falhar.
    alerting = raw.get("alerting") or {}
    telegram = alerting.get("telegram")
    if telegram is not None:
        probe: set[str] = set()
        _expand_env(telegram, env, probe)
        if probe:
            alerting.pop("telegram")

    missing: set[str] = set()
    expanded = _expand_env(raw, env, missing)
    if missing:
        raise ConfigError(f"variáveis de ambiente ausentes: {', '.join(sorted(missing))}")
    try:
        return MonitoringConfig.model_validate(expanded)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
