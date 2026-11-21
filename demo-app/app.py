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
from pathlib import Path
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


# O painel fica num arquivo próprio para ser editado como HTML de verdade.
PANEL_HTML = (Path(__file__).with_name("panel.html")).read_text(encoding="utf-8")
