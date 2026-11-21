from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app as demo

EMAIL, PASSWORD = "lucas@sistema.demo", "senha-forte-123"


@pytest.fixture(autouse=True)
def reset(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(demo, "SLOW_SECONDS", 0.05)
    monkeypatch.setattr(demo, "CHAOS_TOKEN", "")
    monkeypatch.setattr(demo, "CREDENTIALS", demo.Credentials.create(EMAIL, PASSWORD))
    demo._state.update(mode="normal")
    demo._sessions.clear()
    demo._failures.clear()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(demo.app)


@pytest.fixture
def logged(client: TestClient) -> TestClient:
    assert (
        client.post(
            "/api/login", json={"email": EMAIL, "password": PASSWORD}
        ).status_code
        == 204
    )
    return client


# --- Loja -----------------------------------------------------------------------------------


def test_normal_mode(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/api/produtos").json()) == 3


def test_down_mode_returns_503(logged: TestClient) -> None:
    assert logged.post("/chaos/down").json() == {"mode": "down"}
    res = logged.get("/health")
    assert res.status_code == 503
    assert res.json() == {"status": "down"}
    assert logged.get("/api/produtos").status_code == 503
    logged.post("/chaos/normal")
    assert logged.get("/health").status_code == 200


def test_slow_mode_still_ok(logged: TestClient) -> None:
    logged.post("/chaos/slow")
    assert logged.get("/health").json() == {"status": "ok"}


def test_invalid_mode(logged: TestClient) -> None:
    assert logged.post("/chaos/explodir").status_code == 422


# --- Autorização do caos ------------------------------------------------------------------


def test_chaos_requires_login_or_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.post("/chaos/down").status_code == 401
    assert client.get("/health").status_code == 200  # nada mudou

    monkeypatch.setattr(demo, "CHAOS_TOKEN", "segredo")
    assert (
        client.post("/chaos/down", headers={"x-chaos-token": "errado"}).status_code
        == 401
    )
    assert (
        client.post("/chaos/down", headers={"x-chaos-token": "segredo"}).status_code
        == 200
    )


def test_empty_token_never_authorizes(client: TestClient) -> None:
    # CHAOS_TOKEN vazio desliga o caminho por token, mesmo com cabeçalho vazio.
    assert client.post("/chaos/down", headers={"x-chaos-token": ""}).status_code == 401


def test_cross_site_chaos_is_blocked(logged: TestClient) -> None:
    res = logged.post(
        "/chaos/down", headers={"origin": "https://site-malicioso.example"}
    )
    assert res.status_code == 401
    assert logged.get("/health").status_code == 200


# --- Login ----------------------------------------------------------------------------------


def test_panel_redirects_to_login(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"
    assert "Entre no painel." in client.get("/login").text


def test_login_sets_hardened_cookie(client: TestClient) -> None:
    res = client.post(
        "/api/login", json={"email": "  LUCAS@sistema.demo ", "password": PASSWORD}
    )
    assert res.status_code == 204
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "Derrubar" in client.get("/").text
    assert client.get("/api/me").json() == {"email": EMAIL}
    # Só o hash do token fica em memória.
    raw = client.cookies.get(demo.SESSION_COOKIE)
    assert raw is not None and raw not in demo._sessions


def test_wrong_credentials_are_generic(client: TestClient) -> None:
    wrong_pass = client.post("/api/login", json={"email": EMAIL, "password": "x"})
    wrong_user = client.post(
        "/api/login", json={"email": "outro@sistema.demo", "password": PASSWORD}
    )
    assert wrong_pass.status_code == wrong_user.status_code == 401
    assert (
        wrong_pass.json()
        == wrong_user.json()
        == {"detail": "E-mail ou senha incorretos."}
    )


def test_lockout_after_five_failures(client: TestClient) -> None:
    for _ in range(demo.MAX_FAILURES):
        assert (
            client.post(
                "/api/login", json={"email": EMAIL, "password": "x"}
            ).status_code
            == 401
        )
    res = client.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) > 0


def test_login_rejects_cross_origin(client: TestClient) -> None:
    res = client.post(
        "/api/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"origin": "https://site-malicioso.example"},
    )
    assert res.status_code == 403


def test_login_validates_payload(client: TestClient) -> None:
    assert client.post("/api/login", json={"email": EMAIL}).status_code == 422
    assert (
        client.post(
            "/api/login", json={"email": "a" * 300, "password": "x"}
        ).status_code
        == 422
    )


def test_logout_invalidates_session(logged: TestClient) -> None:
    assert logged.post("/api/logout").status_code == 204
    assert logged.get("/api/me").status_code == 401
    assert logged.post("/chaos/down").status_code == 401


def test_expired_session(logged: TestClient) -> None:
    for session in demo._sessions.values():
        session.expires_at = 0
    assert logged.get("/api/me").status_code == 401


def test_security_headers(client: TestClient) -> None:
    headers = client.get("/login").headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
