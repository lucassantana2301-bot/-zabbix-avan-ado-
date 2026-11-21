from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as demo


@pytest.fixture(autouse=True)
def reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo, "SLOW_SECONDS", 0.05)
    monkeypatch.setattr(demo, "CHAOS_TOKEN", "")
    demo._state.update(mode="normal")


client = TestClient(demo.app)


def test_normal_mode() -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/api/produtos").json()) == 3


def test_down_mode_returns_503() -> None:
    assert client.post("/chaos/down").json() == {"mode": "down"}
    res = client.get("/health")
    assert res.status_code == 503
    assert res.json() == {"status": "down"}
    assert client.get("/api/produtos").status_code == 503
    client.post("/chaos/normal")
    assert client.get("/health").status_code == 200


def test_slow_mode_still_ok() -> None:
    client.post("/chaos/slow")
    assert client.get("/health").json() == {"status": "ok"}


def test_invalid_mode_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.post("/chaos/explodir").status_code == 422
    monkeypatch.setattr(demo, "CHAOS_TOKEN", "segredo")
    assert client.post("/chaos/down").status_code == 401
    assert client.post("/chaos/down", headers={"x-chaos-token": "segredo"}).status_code == 200


def test_panel_renders() -> None:
    assert "Derrubar" in client.get("/").text
