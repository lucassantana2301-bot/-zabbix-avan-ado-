## O que muda

<!-- Resumo em 1–3 frases. Linke a issue: "Closes #123". -->

## Por quê

<!-- Contexto, problema ou decisão por trás da mudança. -->

## Como testei

<!-- Testes automatizados adicionados e/ou passos manuais. -->

## Checklist

- [ ] `(cd zbxctl && ruff check . && mypy src && pytest -q)` passa localmente
- [ ] `(cd demo-app && pytest -q)` passa localmente
- [ ] `zbxctl validate -f monitoring/zabbix.yaml` passa localmente
- [ ] `docker compose config --quiet` passa localmente
- [ ] Testes cobrem o comportamento novo ou o bug corrigido
- [ ] Documentação (README/ADR/CHANGELOG) atualizada quando aplicável
- [ ] Sem segredos, dados reais de clientes ou credenciais no diff
