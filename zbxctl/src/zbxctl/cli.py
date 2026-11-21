"""CLI: zbxctl validate | plan | apply | wait."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from zbxctl import __version__
from zbxctl.api import ZabbixAPI, ZabbixError
from zbxctl.config import ConfigError, load_config
from zbxctl.reconcile import PlanError, Reconciler, summarize

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CHANGES = 2  # `plan --detailed-exitcode` encontrou diferenças (útil no CI)


def _connect(args: argparse.Namespace) -> ZabbixAPI:
    url = args.url or os.environ.get("ZABBIX_URL", "http://localhost:8080")
    token = os.environ.get("ZABBIX_API_TOKEN")
    timeout = float(os.environ.get("ZABBIX_TIMEOUT", "120"))
    api = ZabbixAPI(url, token=token, timeout=timeout)
    if not token:
        user = os.environ.get("ZABBIX_USER", "Admin")
        password = os.environ.get("ZABBIX_PASSWORD")
        if not password:
            raise ConfigError("defina ZABBIX_API_TOKEN ou ZABBIX_PASSWORD")
        api.login(user, password)
    return api


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.file))
    checks = sum(len(h.web_checks) for h in cfg.hosts)
    alert = "Telegram" if cfg.alerting.telegram else "sem alertas"
    print(f"OK: {len(cfg.host_groups)} grupo(s), {len(cfg.hosts)} host(s), {checks} web check(s), {alert}.")
    return EXIT_OK


def cmd_plan(args: argparse.Namespace, *, apply: bool = False) -> int:
    cfg = load_config(Path(args.file))
    with _connect(args) as api:
        reconciler = Reconciler(api, cfg)
        changes = reconciler.plan()
        if not changes:
            print("Nenhuma mudança: o Zabbix já está igual ao YAML.")
            return EXIT_OK
        for change in changes:
            print(change.render())
        print(summarize(changes), flush=True)
        if apply:
            print("Aplicando:")
            reconciler.apply(changes, on_done=lambda c: print(f"  ok {c.kind} {c.name!r}", flush=True))
            print("Aplicado.")
            return EXIT_OK
    return EXIT_CHANGES if args.detailed_exitcode else EXIT_OK


def cmd_wait(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("ZABBIX_URL", "http://localhost:8080")
    with ZabbixAPI(url) as api:
        version = api.wait_ready(timeout_s=args.timeout)
    print(f"Zabbix {version} pronto.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zbxctl", description="Monitoramento como código para Zabbix 7")
    parser.add_argument("--version", action="version", version=f"zbxctl {__version__}")
    parser.add_argument("--url", help="URL do frontend (padrão: $ZABBIX_URL ou http://localhost:8080)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("validate", "valida o YAML sem acessar o Zabbix"),
        ("plan", "mostra o que mudaria"),
        ("apply", "aplica as mudanças"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("-f", "--file", default="monitoring/zabbix.yaml")
        if name == "plan":
            p.add_argument("--detailed-exitcode", action="store_true", help="sai com 2 se houver mudanças")

    w = sub.add_parser("wait", help="espera a API responder")
    w.add_argument("--timeout", type=float, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "plan":
            return cmd_plan(args)
        if args.command == "apply":
            args.detailed_exitcode = False
            return cmd_plan(args, apply=True)
        return cmd_wait(args)
    except (ConfigError, PlanError) as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
    except ZabbixError as exc:
        print(f"erro da API do Zabbix: {exc}", file=sys.stderr)
    except (httpx.HTTPError, TimeoutError) as exc:
        print(f"falha de conexão: {exc}", file=sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
