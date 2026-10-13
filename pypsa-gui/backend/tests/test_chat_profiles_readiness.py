"""
P28 A3 (deferred spec 2026-09-28 §3.1, D-3): per-profile readiness on
`GET /api/chat/profiles`; `GET /api/chat/health` byte-stable.

`chat_ready` per profile is the `/health` rule applied to that profile:
a bearer profile is ready when its key env var is set, any other profile is
ready. `os.environ` membership only — never a network call, never a key NAME.

Local mode: the auth middleware injects the seeded local user on every
request, so the route answers there (`local_mode_client`). It answers 401
only when no user resolves — an anonymous hosted caller, or a local-mode
database whose identity was never seeded. The frontend falls back to
`/health`'s `chat_ready` in that case (spec §3.1 "Local mode").
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import main
from services import app_secrets, llm_config


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    before = dict(os.environ)
    previous_shell = app_secrets._SHELL_NAMES
    app_secrets._SHELL_NAMES = frozenset()
    yield
    app_secrets._SHELL_NAMES = previous_shell
    for k in set(os.environ) - set(before):
        del os.environ[k]
    for k, v in before.items():
        if os.environ.get(k) != v:
            os.environ[k] = v


def _profile(pid: str, *, auth: str, wire: str = "openai") -> llm_config.LLMProfile:
    return llm_config.LLMProfile(
        id=pid, label=pid.title(), preset="custom", wire=wire,
        base_url="http://127.0.0.1:1/v1", model="m", tools=True, vision=False,
        auth=auth, fallback_model=None, max_output_tokens=None,
    )


def _by_id(body: dict) -> dict[str, dict]:
    return {p["id"]: p for p in body["profiles"]}


def test_a_bearer_profile_with_its_key_set_is_ready(client, monkeypatch):
    p = _profile("keyed", auth="bearer")
    llm_config.save_profiles([p], llm_config.BUILTIN_SONNET_ID)
    monkeypatch.setenv(p.key_env, "sk-test")
    body = client.get("/api/chat/profiles").json()
    assert _by_id(body)["keyed"]["chat_ready"] is True


def test_a_bearer_profile_without_its_key_is_not_ready(client, monkeypatch):
    p = _profile("keyless", auth="bearer")
    llm_config.save_profiles([p], llm_config.BUILTIN_SONNET_ID)
    monkeypatch.delenv(p.key_env, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client.get("/api/chat/profiles").json()
    by = _by_id(body)
    assert by["keyless"]["chat_ready"] is False
    # The built-ins are bearer profiles on ANTHROPIC_API_KEY.
    assert by[llm_config.BUILTIN_SONNET_ID]["chat_ready"] is False


def test_an_auth_none_profile_is_ready(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_config.save_profiles([_profile("local", auth="none")], llm_config.BUILTIN_SONNET_ID)
    body = client.get("/api/chat/profiles").json()
    assert _by_id(body)["local"]["chat_ready"] is True


def test_each_profile_agrees_with_health_for_the_active_one(client, monkeypatch):
    """The per-profile rule IS the `/health` rule: for the active profile both
    routes say the same thing, with and without its key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for key in (None, "sk-ant-x"):
        if key:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key)
        h = client.get("/api/chat/health").json()
        body = client.get("/api/chat/profiles").json()
        active = _by_id(body)[body["active_profile_id"]]
        assert active["chat_ready"] is h["chat_ready"]


def test_profiles_carry_no_key_name_and_only_the_four_fields(client, monkeypatch):
    p = _profile("keyed", auth="bearer")
    llm_config.save_profiles([p], llm_config.BUILTIN_SONNET_ID)
    monkeypatch.setenv(p.key_env, "sk-secret-value")
    r = client.get("/api/chat/profiles")
    for prof in r.json()["profiles"]:
        assert set(prof) == {"id", "label", "wire", "chat_ready"}
        assert isinstance(prof["chat_ready"], bool)
    assert "PYPSA_GUI_LLM_KEY" not in r.text
    assert "sk-secret-value" not in r.text
    assert "active_profile_id" in r.json()


def test_health_key_set_is_unchanged(client, monkeypatch):
    """`/health` stays byte-stable (D-3): the exact key set, and the nested
    active-profile keys, are pinned."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client.get("/api/chat/health").json()
    assert set(body) == {
        "ok", "anthropic_api_key_present", "default_model",
        "confirmation_ttl_seconds", "active_profile", "chat_ready",
    }
    assert set(body["active_profile"]) == {"id", "label", "wire"}


def test_profiles_refuse_a_caller_with_no_user(anon_client):
    """No user resolves → 401 as before (the FE falls back to /health)."""
    assert anon_client.get("/api/chat/profiles").status_code == 401


def test_local_mode_seeds_its_identity_at_startup_so_profiles_answer(
        _auth_db, monkeypatch, tmp_path):
    """Spec review condition 11, re-verified: the spec says `/profiles`
    answers 401 in local mode. It does not — `main.lifespan` runs
    `local_mode.ensure_local_identity` at startup and the middleware injects
    that user on every request, so even a database whose identity was
    removed answers 200 once the app starts. The 401 path is the anonymous
    hosted caller above. The FE fallback (any error on the profiles read →
    `/health`'s `chat_ready` for the active profile) is kept regardless."""
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.remove_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            assert c.get("/api/chat/profiles").status_code == 200
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_profiles_in_local_mode_answer_with_readiness(_auth_db, monkeypatch, tmp_path):
    """The desktop build: the seeded local user is injected, so the route
    answers (this is the context the P28 smoke reads it in)."""
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_config.save_profiles([_profile("local", auth="none")], llm_config.BUILTIN_SONNET_ID)
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            r = c.get("/api/chat/profiles")
            assert r.status_code == 200, r.text
            by = _by_id(r.json())
            assert by["local"]["chat_ready"] is True
            assert by[llm_config.BUILTIN_SONNET_ID]["chat_ready"] is False
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)
