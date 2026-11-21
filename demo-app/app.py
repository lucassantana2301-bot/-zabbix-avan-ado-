"""Loja de demonstração monitorada pelo Zabbix, com modo caos e login.

Modos:
  normal -> /health responde 200 rápido
  slow   -> /health demora ~2,5 s (dispara o trigger de lentidão)
  down   -> /health e /api/produtos respondem 503 (dispara o trigger de queda)

Quem pode mudar o modo:
  - uma pessoa logada no painel (sessão em cookie HttpOnly), ou
  - uma automação com o cabeçalho X-Chaos-Token igual a CHAOS_TOKEN (usado no CI).

/health, /api/produtos e GET /chaos continuam públicos: o Zabbix precisa enxergá-los.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

Mode = Literal["normal", "slow", "down"]

log = logging.getLogger("loja-demo")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Loja demo", docs_url=None, redoc_url=None, openapi_url=None)

_state: dict[str, object] = {"mode": "normal", "since": time.time()}
SLOW_SECONDS = float(os.getenv("SLOW_SECONDS", "2.5"))
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")

SESSION_COOKIE = "loja_sessao"
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", str(8 * 3600)))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
MAX_FAILURES = 5
LOCKOUT_WINDOW = 5 * 60
MAX_SESSIONS = 1000

PRODUCTS = [
    {"id": 1, "nome": "Camiseta Observabilidade", "preco": 79.9},
    {"id": 2, "nome": "Caneca On-call", "preco": 39.9},
    {"id": 3, "nome": "Adesivo 99,99% de uptime", "preco": 9.9},
]


# --- Credenciais -------------------------------------------------------------------------

# scrypt (stdlib) com parâmetros do OWASP: custo de memória alto torna força bruta cara.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


@dataclass(frozen=True)
class Credentials:
    email: str
    salt: bytes
    digest: bytes

    @classmethod
    def create(cls, email: str, password: str) -> Credentials:
        salt = secrets.token_bytes(16)
        return cls(email.strip().lower(), salt, hash_password(password, salt))

    def verify(self, email: str, password: str) -> bool:
        # Calcula o hash mesmo com e-mail errado: o tempo de resposta não revela quem existe.
        password_ok = secrets.compare_digest(
            hash_password(password, self.salt), self.digest
        )
        email_ok = secrets.compare_digest(
            email.strip().lower().encode(), self.email.encode()
        )
        return password_ok and email_ok


def _load_credentials() -> Credentials:
    email = os.getenv("DEMO_EMAIL", "admin@sistema.demo")
    password = os.getenv("DEMO_PASSWORD", "")
    if not password:
        # Sem senha configurada, gera uma forte e mostra só no log do container.
        password = secrets.token_urlsafe(12)
        log.warning(
            "DEMO_PASSWORD não definida. Senha gerada para %s: %s", email, password
        )
    return Credentials.create(email, password)


CREDENTIALS = _load_credentials()


# --- Sessões e limite de tentativas -------------------------------------------------------


@dataclass
class Session:
    email: str
    expires_at: float


# Guardamos só o SHA-256 do token: um dump de memória não vira sessão válida.
_sessions: dict[str, Session] = {}
_failures: dict[str, deque[float]] = {}


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _prune_sessions(now: float) -> None:
    for key in [k for k, s in _sessions.items() if s.expires_at <= now]:
        del _sessions[key]


def create_session(email: str) -> str:
    now = time.time()
    _prune_sessions(now)
    if len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions, key=lambda k: _sessions[k].expires_at)
        del _sessions[oldest]
    token = secrets.token_urlsafe(32)
    _sessions[_token_key(token)] = Session(email, now + SESSION_TTL)
    return token


def current_session(request: Request) -> Session | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        return None
    session = _sessions.get(_token_key(token))
    if session is None or session.expires_at <= time.time():
        return None
    return session


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


def _recent_failures(ip: str, now: float) -> deque[float]:
    attempts = _failures.setdefault(ip, deque())
    while attempts and now - attempts[0] > LOCKOUT_WINDOW:
        attempts.popleft()
    return attempts


def _same_origin(request: Request) -> bool:
    """Defesa extra contra CSRF, além do SameSite=Strict do cookie."""
    origin = request.headers.get("origin")
    if origin is None:
        return request.headers.get("sec-fetch-site", "same-origin") in {
            "same-origin",
            "none",
        }
    return urlsplit(origin).netloc == request.headers.get("host", "")


# --- Cabeçalhos de segurança --------------------------------------------------------------


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- Loja e caos --------------------------------------------------------------------------


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
async def set_chaos(
    mode: Mode, request: Request, x_chaos_token: str | None = Header(default=None)
) -> dict[str, object]:
    token_ok = bool(CHAOS_TOKEN) and secrets.compare_digest(
        x_chaos_token or "", CHAOS_TOKEN
    )
    session_ok = current_session(request) is not None and _same_origin(request)
    if not (token_ok or session_ok):
        raise HTTPException(
            status_code=401, detail="Entre no painel para mudar o modo do sistema."
        )
    _state.update(mode=mode, since=time.time())
    who = "token" if token_ok else "painel"
    log.info("modo do sistema alterado para %s via %s", mode, who)
    return {"mode": mode}


# --- Login --------------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


@app.post("/api/login", status_code=204)
async def login(body: LoginIn, request: Request) -> Response:
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Origem não permitida.")
    ip, now = _client_ip(request), time.time()
    attempts = _recent_failures(ip, now)
    if len(attempts) >= MAX_FAILURES:
        retry = int(LOCKOUT_WINDOW - (now - attempts[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas. Tente de novo em {max(1, round(retry / 60))} min.",
            headers={"Retry-After": str(retry)},
        )
    # scrypt é CPU-bound: roda fora do event loop para não travar o /health.
    if not await asyncio.to_thread(CREDENTIALS.verify, body.email, body.password):
        attempts.append(now)
        log.warning("login recusado para %s a partir de %s", body.email[:64], ip)
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")

    attempts.clear()
    response = Response(status_code=204)
    response.set_cookie(
        SESSION_COOKIE,
        create_session(CREDENTIALS.email),
        max_age=SESSION_TTL,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/logout", status_code=204)
async def logout(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _sessions.pop(_token_key(token), None)
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=COOKIE_SECURE, samesite="strict"
    )
    return response


@app.get("/api/me")
async def me(request: Request) -> dict[str, str]:
    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    return {"email": session.email}


# --- Páginas ------------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse, response_model=None)
async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
    if current_session(request) is not None:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(LOGIN_HTML)


@app.get("/", response_class=HTMLResponse, response_model=None)
async def panel(request: Request) -> HTMLResponse | RedirectResponse:
    if current_session(request) is None:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(PANEL_HTML)


# As páginas ficam em arquivos próprios para serem editadas como HTML de verdade.
_HERE = Path(__file__).parent
PANEL_HTML = (_HERE / "panel.html").read_text(encoding="utf-8")
LOGIN_HTML = (_HERE / "login.html").read_text(encoding="utf-8")
