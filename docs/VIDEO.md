# Roteiro do vídeo para o LinkedIn

Duração alvo: **45 a 60 segundos**, vertical (1080x1920) ou quadrado (1080x1080).
Grave a tela com OBS ou com a gravação nativa do sistema, e o celular com o Telegram aberto.

## Antes de gravar

1. `cp .env.example .env` e preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
2. `make demo` e espere uns 3 minutos (o primeiro boot do Zabbix é lento).
3. Deixe a stack rodando **pelo menos 15 minutos** antes de gravar, para o gráfico de tempo de resposta ter histórico.
4. No Grafana, ajuste o período para **Last 15 minutes**.
5. Teste uma vez: `make down`, confirme que o alerta chega, `make normal`.
6. Arrume as janelas: navegador com Grafana à esquerda, painel da loja à direita e o celular com o Telegram ao lado (ou espelhado na tela).

## Cenas

| # | Tempo | O que aparece | Texto na tela |
| --- | --- | --- | --- |
| 1 | 0–4 s | Grafana todo verde, "Loja online: No ar" | "E se a sua aplicação cair agora?" |
| 2 | 4–10 s | Terminal: `zbxctl plan` e `zbxctl apply` | "Todo o monitoramento é código: YAML + Git" |
| 3 | 10–14 s | Painel da loja, clique em **Derrubar** | "Simulando uma queda" |
| 4 | 14–30 s | Grafana: "Loja online" fica vermelho e aparece o problema na lista | "O Zabbix detecta em segundos" |
| 5 | 30–36 s | Celular: notificação do Telegram 🔴 | "E avisa no Telegram" |
| 6 | 36–42 s | Clique em **Normal**, Grafana volta ao verde | "Corrigido" |
| 7 | 42–48 s | Telegram: ✅ Resolvido, com a duração | "Alerta de resolvido com o tempo do incidente" |
| 8 | 48–55 s | README no GitHub com o selo do CI verde | "Zabbix 7 · Grafana · Python · Docker · CI ponta a ponta" |

Dicas:
- Corte as esperas na edição (CapCut ou DaVinci Resolve): entre derrubar e o alerta passam de 20 a 40 segundos.
- Use legenda nas cenas: a maioria assiste sem som no LinkedIn.
- Termine com o link do repositório no primeiro comentário, não no texto do post (o LinkedIn reduz o alcance de posts com link).

## Texto sugerido para o post

> Montei um ambiente de observabilidade com Zabbix 7 onde **todo o monitoramento é código**.
>
> Em vez de configurar host, trigger e alerta clicando na interface, eu descrevo tudo num YAML e um CLI em Python que eu escrevi (zbxctl) aplica no Zabbix pela API, com plan e apply, igual ao Terraform.
>
> No vídeo: derrubo a aplicação, o Zabbix detecta, o Grafana fica vermelho e o alerta chega no Telegram. Religo e chega o "resolvido" com a duração do incidente.
>
> O que tem por trás:
> 🔹 Zabbix 7 LTS + PostgreSQL + Agent 2 em Docker Compose
> 🔹 Grafana com o plugin do Zabbix provisionado
> 🔹 zbxctl: idempotente e com testes (mypy strict, 26 testes)
> 🔹 CI que sobe o Zabbix de verdade, derruba a aplicação e verifica se o alerta dispara
>
> Link do repositório nos comentários 👇
>
> #Zabbix #Observabilidade #DevOps #Monitoramento #Grafana #Python #Docker #SRE
