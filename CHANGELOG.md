# Changelog

Todas as mudanças relevantes deste projeto são documentadas aqui.

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto adota o
[Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

## [1.0.0] - 2026-11-22

Primeira versão estável.

### Adicionado

- **Stack de observabilidade** em Docker Compose: Zabbix 7.0 LTS (server, frontend e Agent 2) sobre
  PostgreSQL 16 e Grafana 11 com o plugin do Zabbix e um dashboard provisionados.
- **zbxctl**: CLI de monitoramento como código (`validate`, `plan`, `apply`, `wait`) que sincroniza
  grupos, hosts, templates, web checks, triggers, media type, mídia do usuário e ação de alerta a partir
  de um YAML versionado. Idempotente e restrito ao que ele mesmo gerencia (tag `managed-by=zbxctl`).
- **Alertas no Telegram** por webhook próprio, com mensagens de problema e de resolução.
- **Loja demo** (FastAPI) com painel de caos autenticado para simular quedas e lentidão.
- **Teste ponta a ponta no CI** contra o Zabbix real: aplica o YAML, garante `plan` vazio na segunda
  execução, derruba a loja e espera o problema abrir e ser resolvido.
- **Pacote do zbxctl** (wheel e sdist) anexado à release e **imagem da loja demo** no GHCR com SBOM
  e atestado de proveniência.

[Não lançado]: https://github.com/lucassantana2301-bot/-zabbix-avan-ado-/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/lucassantana2301-bot/-zabbix-avan-ado-/releases/tag/v1.0.0
