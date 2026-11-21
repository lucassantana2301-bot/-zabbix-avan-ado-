# Atalhos da demonstração. Requer Docker e Python 3.11+.
export ZABBIX_URL ?= http://localhost:8080
export ZABBIX_PASSWORD ?= zabbix
# O .env (se existir) alimenta o compose e os atalhos de caos.
-include .env
export

CHAOS = @test -n "$$CHAOS_TOKEN" || { echo "Defina CHAOS_TOKEN no .env (ou use o painel com login)."; exit 1; }; \
	curl -fsS -X POST -H "x-chaos-token: $$CHAOS_TOKEN" http://localhost:8000/chaos

.PHONY: up install wait plan apply demo down slow normal logs test stop

up:            ## Sobe Zabbix, Postgres, agente, Grafana e a loja demo
	docker compose up -d --build

install:       ## Instala o zbxctl num virtualenv local
	python3 -m venv .venv && .venv/bin/pip install -q -e "./zbxctl[dev]"

wait:          ## Espera a API do Zabbix responder
	.venv/bin/zbxctl wait

plan:          ## Mostra o diff entre o YAML e o Zabbix
	.venv/bin/zbxctl plan

apply: wait    ## Aplica monitoring/zabbix.yaml
	.venv/bin/zbxctl apply

down:          ## Derruba a loja (dispara o alerta)
	$(CHAOS)/down; echo

slow:          ## Deixa a loja lenta
	$(CHAOS)/slow; echo

normal:        ## Religa a loja (alerta de resolvido)
	$(CHAOS)/normal; echo

demo: up install apply  ## Tudo de uma vez: sobe, instala e aplica
	@echo "Zabbix: http://localhost:8080  Grafana: http://localhost:3000  Loja: http://localhost:8000"

logs:
	docker compose logs -f --tail=50 zabbix-server

test:          ## Testes do zbxctl e da loja
	cd zbxctl && ../.venv/bin/pytest -q
	cd demo-app && ../.venv/bin/pip install -q -r requirements.txt && ../.venv/bin/pytest -q

stop:          ## Para e remove tudo (inclusive volumes)
	docker compose down -v
