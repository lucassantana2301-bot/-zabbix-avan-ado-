"""Loja de demonstração monitorada pelo Zabbix, com modo caos.

Modos:
  normal -> /health responde 200 rápido
  slow   -> /health demora ~2,5 s (dispara o trigger de lentidão)
  down   -> /health e /api/produtos respondem 503 (dispara o trigger de queda)

O painel em / tem botões para alternar o modo durante a demonstração.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

Mode = Literal["normal", "slow", "down"]

app = FastAPI(title="Loja demo", docs_url=None, redoc_url=None)

_state: dict[str, object] = {"mode": "normal", "since": time.time()}
SLOW_SECONDS = float(os.getenv("SLOW_SECONDS", "2.5"))
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")

PRODUCTS = [
    {"id": 1, "nome": "Camiseta Observabilidade", "preco": 79.9},
    {"id": 2, "nome": "Caneca On-call", "preco": 39.9},
    {"id": 3, "nome": "Adesivo 99,99% de uptime", "preco": 9.9},
]


async def _apply_chaos() -> JSONResponse | None:
    mode = _state["mode"]
    if mode == "down":
        return JSONResponse({"status": "down"}, status_code=503)
    if mode == "slow":
        await asyncio.sleep(SLOW_SECONDS)
    return None


@app.get("/health")
async def health() -> JSONResponse:
    failure = await _apply_chaos()
    return failure or JSONResponse({"status": "ok"})


@app.get("/api/produtos")
async def products() -> JSONResponse:
    failure = await _apply_chaos()
    return failure or JSONResponse(PRODUCTS)


@app.get("/chaos")
async def chaos_state() -> dict[str, object]:
    return {"mode": _state["mode"], "since": _state["since"]}


@app.post("/chaos/{mode}")
async def set_chaos(mode: Mode, x_chaos_token: str | None = Header(default=None)) -> dict[str, object]:
    # Com CHAOS_TOKEN definido, só quem tem o token muda o modo.
    if CHAOS_TOKEN and not secrets.compare_digest(x_chaos_token or "", CHAOS_TOKEN):
        raise HTTPException(status_code=401, detail="token de caos inválido")
    _state.update(mode=mode, since=time.time())
    return {"mode": mode}


@app.get("/", response_class=HTMLResponse)
async def panel() -> str:
    return PANEL_HTML


PANEL_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simulador de incidente</title>
<style>
  :root { --bg:#f4f5f2; --ink:#16201b; --soft:#5d6862; --ok:#1f8a4c; --warn:#c77d0a; --down:#c0392b; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--ink);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { width:min(34rem, 92vw); }
  h1 { font-size:1rem; font-weight:600; margin:0 0 2rem; color:var(--soft); }
  .lamp { display:flex; align-items:center; gap:1.25rem; margin-bottom:2.5rem; }
  .dot { width:4.5rem; height:4.5rem; border-radius:50%; background:var(--ok);
         box-shadow:0 0 0 .6rem color-mix(in srgb, var(--ok) 18%, transparent); transition: background .3s, box-shadow .3s; }
  .state { font-size:clamp(2rem, 7vw, 3rem); font-weight:800; line-height:1; letter-spacing:-.02em; }
  .since { color:var(--soft); margin-top:.35rem; }
  [data-mode=slow] .dot { background:var(--warn); box-shadow:0 0 0 .6rem color-mix(in srgb, var(--warn) 20%, transparent); }
  [data-mode=down] .dot { background:var(--down); box-shadow:0 0 0 .6rem color-mix(in srgb, var(--down) 20%, transparent);
                          animation: pulse 1.2s infinite; }
  @keyframes pulse { 50% { box-shadow:0 0 0 1.2rem color-mix(in srgb, var(--down) 8%, transparent); } }
  @media (prefers-reduced-motion: reduce) { [data-mode=down] .dot { animation:none; } }
  .actions { display:grid; grid-template-columns:repeat(3, 1fr); gap:.6rem; }
  button { font:inherit; font-weight:650; padding:.9rem .5rem; border-radius:8px; border:2px solid var(--ink);
           background:transparent; color:var(--ink); cursor:pointer; }
  button[aria-pressed=true] { background:var(--ink); color:var(--bg); }
  button:focus-visible { outline:3px solid var(--warn); outline-offset:2px; }
  p.hint { color:var(--soft); font-size:.9rem; margin-top:1.5rem; }
</style>
</head>
<body>
<main data-mode="normal">
  <h1>Loja demo, monitorada pelo Zabbix</h1>
  <div class="lamp" role="status" aria-live="polite">
    <div class="dot" aria-hidden="true"></div>
    <div><div class="state" id="state">Operando normal</div><div class="since" id="since"></div></div>
  </div>
  <div class="actions">
    <button data-set="normal">Normal</button>
    <button data-set="slow">Deixar lento</button>
    <button data-set="down">Derrubar</button>
  </div>
  <p class="hint">O Zabbix checa o /health a cada 15 s. Ao derrubar, o alerta chega no Telegram em menos de 1 minuto.</p>
</main>
<script>
const LABEL = { normal: "Operando normal", slow: "Lento", down: "Fora do ar" };
const main = document.querySelector("main");
function render(s) {
  main.dataset.mode = s.mode;
  document.getElementById("state").textContent = LABEL[s.mode];
  const secs = Math.max(0, Math.round(Date.now() / 1000 - s.since));
  document.getElementById("since").textContent = "há " + secs + " s";
  document.querySelectorAll("[data-set]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.set === s.mode)));
}
async function refresh() { render(await (await fetch("/chaos")).json()); }
document.querySelectorAll("[data-set]").forEach(b => b.addEventListener("click", async () => {
  const token = new URLSearchParams(location.search).get("token") || "";
  const res = await fetch("/chaos/" + b.dataset.set, { method: "POST", headers: { "x-chaos-token": token } });
  if (!res.ok) { alert("Não autorizado: abra o painel com ?token=SEU_TOKEN"); return; }
  refresh();
}));
refresh(); setInterval(refresh, 1000);
</script>
</body>
</html>
"""
