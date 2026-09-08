"""
Task 9 — LLM settings + profiles routes, connection test, health additions.

Route contract under test (spec 2026-08-14, Task 9):
    GET    /api/chat/settings/llm
    PUT    /api/chat/settings/llm/profiles/{id}
    DELETE /api/chat/settings/llm/profiles/{id}
    PUT    /api/chat/settings/llm/profiles/{id}/key
    DELETE /api/chat/settings/llm/profiles/{id}/key
    POST   /api/chat/settings/llm/active
    POST   /api/chat/settings/llm/profiles/{id}/test
    GET    /api/chat/profiles

Every `/settings/llm*` route is super-admin gated, matching
`/api/chat/settings/api-key` (test_chat_api_key_settings.py's own pattern is
mirrored here: `client` is an ORG admin who must be refused, `anon_client` is
unauthenticated, `super_admin_client` is seeded locally). `/api/chat/profiles`
is gated only on "authenticated" — any org member.

Isolation: `_isolated_llm_env` (autouse) redirects `PYPSAGUI_APP_DATA_DIR` to a
fresh `tmp_path` per test (so `llm-profiles.json` never touches the shared
session app-data dir conftest.py pins — the same leak Task 9's own conftest
fix addresses for tests that DON'T redirect the dir) and snapshots/restores
every `app_secrets`-managed `os.environ` entry a route mutates in-process
(`app_secrets.set_secret`/`clear_secret` apply immediately, independent of
the file redirect).
"""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from db.models import OrgMembership, User
from services import app_secrets
from tests.conftest import attach_session

import uuid
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_llm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    before = dict(os.environ)
    previous_shell = app_secrets._SHELL_NAMES
    app_secrets._SHELL_NAMES = frozenset()
    yield
    app_secrets._SHELL_NAMES = previous_shell
    # app_secrets.set_secret/clear_secret apply to os.environ IN-PROCESS,
    # independent of the PYPSAGUI_APP_DATA_DIR redirect above (see
    # app_secrets.py's own precedence docstring) — undo whatever a route
    # under test changed there.
    for name in list(os.environ):
        if name not in before and app_secrets.is_managed_key(name):
            os.environ.pop(name, None)
    for name, value in before.items():
        if app_secrets.is_managed_key(name) and os.environ.get(name) != value:
            os.environ[name] = value


def _seed_super_admin(session_local, org_id):
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"llm-super-admin-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=None,
            status="active",
            is_super_admin=True,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id, org_id=org_id, role="admin"))
        db.commit()
        return user.id


@pytest.fixture
def super_admin_client(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    user_id = _seed_super_admin(session_local, seeded_identity["org_id"])
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        with session_local() as db:
            row = db.get(User, user_id)
            if row is not None:
                db.delete(row)
                db.commit()


def _custom_profile_body(**overrides) -> dict:
    body = {
        "label": "My Ollama",
        "preset": "custom",
        "wire": "openai",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "tools": True,
        "vision": False,
        "auth": "bearer",
        "fallback_model": None,
        "max_output_tokens": None,
    }
    body.update(overrides)
    return body


# ─────────────────────────────────────────────────────────────────────────
# Super-admin gate on every /settings/llm* route
# ─────────────────────────────────────────────────────────────────────────

_SETTINGS_ROUTES = [
    ("get", "/api/chat/settings/llm", {}),
    ("put", "/api/chat/settings/llm/profiles/my-custom", {"json": _custom_profile_body()}),
    ("delete", "/api/chat/settings/llm/profiles/my-custom", {}),
    ("put", "/api/chat/settings/llm/profiles/my-custom/key", {"json": {"value": "sk-x"}}),
    ("delete", "/api/chat/settings/llm/profiles/my-custom/key", {}),
    ("post", "/api/chat/settings/llm/active", {"json": {"profile_id": "anthropic-sonnet"}}),
    ("post", "/api/chat/settings/llm/profiles/anthropic-sonnet/test", {}),
]


@pytest.mark.parametrize("method, path, kwargs", _SETTINGS_ROUTES)
def test_an_org_admin_is_refused_every_settings_route(client, method, path, kwargs):
    """`client` is an ORG admin (`is_super_admin=False`) — must be 403."""
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("method, path, kwargs", _SETTINGS_ROUTES)
def test_an_anonymous_caller_is_refused_every_settings_route(anon_client, method, path, kwargs):
    response = getattr(anon_client, method)(path, **kwargs)
    assert response.status_code == 401, response.text


# ─────────────────────────────────────────────────────────────────────────
# Member gate on GET /chat/profiles
# ─────────────────────────────────────────────────────────────────────────


def test_profiles_route_refuses_anonymous(anon_client):
    response = anon_client.get("/api/chat/profiles")
    assert response.status_code == 401, response.text


def test_profiles_route_allows_any_authenticated_member(client):
    """`client` is a plain ORG admin — NOT super-admin, still allowed."""
    response = client.get("/api/chat/profiles")
    assert response.status_code == 200, response.text
    body = response.json()
    ids = [p["id"] for p in body["profiles"]]
    assert "anthropic-sonnet" in ids and "anthropic-opus" in ids
    assert body["active_profile_id"] == "anthropic-sonnet"
    # Only id/label/wire per profile — no base_url, no key info.
    for p in body["profiles"]:
        assert set(p.keys()) == {"id", "label", "wire"}


# ─────────────────────────────────────────────────────────────────────────
# Profile CRUD
# ─────────────────────────────────────────────────────────────────────────


def test_create_profile_appears_in_get(super_admin_client):
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["id"] == "my-custom"
    assert out["label"] == "My Ollama"
    assert out["key_required"] is True
    assert out["key_present"] is False
    assert out["key_hint"] is None

    listing = super_admin_client.get("/api/chat/settings/llm").json()
    ids = [p["id"] for p in listing["profiles"]]
    assert "my-custom" in ids
    assert listing["active_profile_id"] == "anthropic-sonnet"
    assert isinstance(listing["presets"], list)


def test_key_env_in_put_body_is_rejected_422(super_admin_client):
    body = _custom_profile_body()
    body["key_env"] = "PYPSA_GUI_LLM_KEY__SNEAKY"
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=body
    )
    assert resp.status_code == 422, resp.text


def test_editing_a_builtin_profile_is_refused(super_admin_client):
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/anthropic-sonnet",
        json=_custom_profile_body(preset="anthropic", wire="anthropic",
                                  auth="bearer", model="claude-sonnet-5"),
    )
    assert resp.status_code == 409, resp.text


def test_put_then_get_key_never_returns_the_value(super_admin_client):
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    secret = "sk-super-secret-value-12345"
    put_resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom/key", json={"value": secret}
    )
    assert put_resp.status_code == 200, put_resp.text
    assert secret not in put_resp.text
    body = put_resp.json()
    assert body["key_present"] is True
    assert body["key_hint"] == "…" + secret[-4:]

    get_resp = super_admin_client.get("/api/chat/settings/llm")
    assert secret not in get_resp.text
    listing = get_resp.json()
    profile_out = next(p for p in listing["profiles"] if p["id"] == "my-custom")
    assert profile_out["key_present"] is True
    assert profile_out["key_hint"] == "…" + secret[-4:]


def test_delete_key_clears_it(super_admin_client):
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom/key", json={"value": "sk-abc123456"}
    )
    del_resp = super_admin_client.delete("/api/chat/settings/llm/profiles/my-custom/key")
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json()["key_present"] is False


def test_delete_profile_clears_its_namespaced_key_slot(super_admin_client):
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    secret = "sk-namespaced-secret-999"
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom/key", json={"value": secret}
    )
    key_env = "PYPSA_GUI_LLM_KEY__MY_CUSTOM"
    assert app_secrets.get_stored(key_env) == secret

    del_resp = super_admin_client.delete("/api/chat/settings/llm/profiles/my-custom")
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json()["ok"] is True
    assert app_secrets.get_stored(key_env) is None

    listing = super_admin_client.get("/api/chat/settings/llm").json()
    assert "my-custom" not in [p["id"] for p in listing["profiles"]]


def test_delete_active_profile_resets_active_to_builtin_sonnet(super_admin_client):
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    super_admin_client.post(
        "/api/chat/settings/llm/active", json={"profile_id": "my-custom"}
    )
    del_resp = super_admin_client.delete("/api/chat/settings/llm/profiles/my-custom")
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json()["active_profile_id"] == "anthropic-sonnet"


def test_deleting_a_builtin_profile_is_refused(super_admin_client):
    resp = super_admin_client.delete("/api/chat/settings/llm/profiles/anthropic-opus")
    assert resp.status_code == 409, resp.text


def test_deleting_an_unknown_profile_404s(super_admin_client):
    resp = super_admin_client.delete("/api/chat/settings/llm/profiles/does-not-exist")
    assert resp.status_code == 404, resp.text


# ─────────────────────────────────────────────────────────────────────────
# Active profile
# ─────────────────────────────────────────────────────────────────────────


def test_active_post_validates_the_id(super_admin_client):
    resp = super_admin_client.post(
        "/api/chat/settings/llm/active", json={"profile_id": "no-such-profile"}
    )
    assert resp.status_code == 404, resp.text


def test_active_post_happy_path(super_admin_client):
    resp = super_admin_client.post(
        "/api/chat/settings/llm/active", json={"profile_id": "anthropic-opus"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active_profile_id"] == "anthropic-opus"


# ─────────────────────────────────────────────────────────────────────────
# Connection test — verdict mapping via httpx.MockTransport, monkeypatched
# into chat_service._provider_for_profile's openai path.
# ─────────────────────────────────────────────────────────────────────────


def _sse_body(*chunks: bytes) -> bytes:
    out = b""
    for c in chunks:
        out += b"data: " + c + b"\n\n"
    return out + b"data: [DONE]\n\n"


@pytest.fixture
def mock_openai_profile(super_admin_client):
    """A saved custom openai-wire profile the /test endpoint can target."""
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/mock-openai",
        json=_custom_profile_body(label="Mock", auth="none"),
    )
    assert resp.status_code == 200, resp.text
    return "mock-openai"


@pytest.mark.parametrize(
    "handler_kind, expected_verdict",
    [
        ("ok", "ok"),
        ("unauthorized", "unauthorized"),
        ("not_found", "model_not_found"),
        ("timeout", "unreachable"),
    ],
)
def test_connection_test_verdicts(
    super_admin_client, mock_openai_profile, monkeypatch, handler_kind, expected_verdict,
):
    from services import chat_service
    from services.llm_openai_compat import OpenAICompatProvider

    def handler(request: httpx.Request) -> httpx.Response:
        if handler_kind == "ok":
            body = _sse_body(
                b'{"choices":[{"delta":{"content":"hi"}}]}',
                b'{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
            )
            return httpx.Response(200, content=body,
                                  headers={"content-type": "text/event-stream"})
        if handler_kind == "unauthorized":
            return httpx.Response(401, json={"error": "nope"})
        if handler_kind == "not_found":
            return httpx.Response(404, json={"error": "model not found"})
        if handler_kind == "timeout":
            raise httpx.ConnectTimeout("timed out")
        raise AssertionError(handler_kind)

    def _fake_provider_for_profile(profile, client=None):
        provider = OpenAICompatProvider(
            "http://mock/v1", api_key=None,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        return provider, None

    monkeypatch.setattr(chat_service, "_provider_for_profile", _fake_provider_for_profile)

    resp = super_admin_client.post(
        f"/api/chat/settings/llm/profiles/{mock_openai_profile}/test"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == expected_verdict
    if expected_verdict == "ok":
        assert body["latency_ms"] is not None and body["latency_ms"] >= 0
    else:
        assert body["latency_ms"] is None
    # Best-effort model list: the mock handler above never serves a
    # `/models`-shaped response, so it must fail silently -> null, never an
    # exception surfaced through the route.
    assert body["models"] is None


def test_connection_test_never_leaks_base_url_or_exception_text(
    super_admin_client, mock_openai_profile, monkeypatch,
):
    from services import chat_service
    from services.llm_openai_compat import OpenAICompatProvider

    secret_host = "internal-secret-host.example.invalid"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused to {secret_host}")

    def _fake_provider_for_profile(profile, client=None):
        provider = OpenAICompatProvider(
            f"http://{secret_host}/v1", api_key=None,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        return provider, None

    monkeypatch.setattr(chat_service, "_provider_for_profile", _fake_provider_for_profile)

    resp = super_admin_client.post(
        f"/api/chat/settings/llm/profiles/{mock_openai_profile}/test"
    )
    assert resp.status_code == 200, resp.text
    assert secret_host not in resp.text
    assert resp.json()["verdict"] == "unreachable"


# ─────────────────────────────────────────────────────────────────────────
# Health additions
# ─────────────────────────────────────────────────────────────────────────


def test_health_gains_active_profile_and_chat_ready_with_no_profiles_file(client):
    """Zero-config: no llm-profiles.json exists yet."""
    resp = client.get("/api/chat/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_profile"] == {
        "id": "anthropic-sonnet", "label": "Claude Sonnet", "wire": "anthropic",
    }
    assert isinstance(body["chat_ready"], bool)
    # Today's exact semantics, unchanged.
    assert "anthropic_api_key_present" in body
    assert "default_model" in body

    # Nothing enumerable: no profiles list, no base_urls, no key hints — even
    # though this route DOES require a session (see the pair of tests below),
    # an authenticated org member has no business right learning any of that
    # either; profile connection details stay super-admin-only via
    # `GET /settings/llm`.
    assert "profiles" not in body
    assert "base_url" not in resp.text
    assert "key_hint" not in resp.text
    assert "PYPSA_GUI_LLM_KEY__" not in resp.text


def test_health_requires_authentication_on_a_server_deployment(anon_client):
    """
    CORRECTED from this task's original brief, which asserted `/chat/health`
    was already unauthenticated and asked for that to be made explicit. That
    premise was false on this branch: `main._AUTH_PUBLIC_PATHS` (checked
    directly against `main.py`'s global auth middleware while implementing)
    has never included `/api/chat/health`, so the middleware 401s an
    anonymous `/api/*` caller here exactly like it does everywhere else.

    The first implementation of this task ADDED the exemption to make an
    (incorrectly) RED test go GREEN — which would have let any anonymous
    caller on a multi-tenant server read `chat_ready`, `default_model`, and
    the active profile's free-text `label`. That change was reverted;
    `main.py` carries a zero diff for this task. This test pins the
    boundary the reverted change would have punched a hole in.
    """
    resp = anon_client.get("/api/chat/health")
    assert resp.status_code == 401, resp.text


def test_health_answers_for_an_authenticated_caller(client):
    """`client` is a plain authenticated org member — not super-admin."""
    resp = client.get("/api/chat/health")
    assert resp.status_code == 200, resp.text


@pytest.fixture
def local_mode_client(_auth_db, monkeypatch, tmp_path):
    """
    Cookie-less client with local mode on — mirrors `test_local_mode_api.py`'s
    `local_client` fixture. This is the ACTUAL reason `/chat/health` never
    needed (and must not get) an `_AUTH_PUBLIC_PATHS` exemption: the desktop
    build has no login at all, and `main.py`'s auth middleware injects the
    seeded local user via `local_mode.get_local_user` on every request in
    that mode, session or not — so the route already answers without a
    session there, through the ordinary authenticated path.
    """
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_health_answers_without_a_session_in_local_mode(local_mode_client):
    resp = local_mode_client.get("/api/chat/health")
    assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────────
# Negative sibling tests — standing lesson.
#
# `_FOREIGN_LOCK_GATE_PREFIXES` / `_foreign_lock_gate_exempt` are a real
# write-blocking gate in `main.py` — but only on a DIFFERENT, unmerged branch
# (the 2026-08-14-project-write-safety-design spec). On THIS branch
# (feature/llm-provider-config @ 8ac14686) `main.py` does not define them at
# all: `git grep` across every branch shows them first introduced as
# `_FOREIGN_LOCK_GATE_PREFIXES = _UNDO_PREFIXES + ("/api/simulation/",)` i.e.
# `("/api/network/", "/api/io/", "/api/simulation/")`, which matches this
# task's brief verbatim, plus `_FOREIGN_LOCK_GATE_EXEMPT_EXACT` /
# `_FOREIGN_LOCK_GATE_EXEMPT_PATTERNS` behind a `_foreign_lock_gate_exempt`
# predicate — none of which exist here. This test checks for the symbol
# defensively (skips with a clear reason if absent) so it starts asserting
# the moment that other branch merges, instead of silently never covering
# the regression it exists to catch.
#
# Scoped to MY new routes' status only — this is NOT a claim that the
# predicate itself is correct in general; it is documented elsewhere
# (once it exists) as known-imperfect, wrongly gating some queue routes
# (docs/superpowers/findings/2026-08-27-requeue-is-a-cross-user-overwrite.md).
# ─────────────────────────────────────────────────────────────────────────

_NEW_ROUTE_PATHS = [
    "/api/chat/settings/llm",
    "/api/chat/settings/llm/profiles/some-id",
    "/api/chat/settings/llm/profiles/some-id/key",
    "/api/chat/settings/llm/active",
    "/api/chat/settings/llm/profiles/some-id/test",
    "/api/chat/profiles",
]


def test_new_llm_routes_are_not_under_the_foreign_lock_gate():
    prefixes = getattr(main, "_FOREIGN_LOCK_GATE_PREFIXES", None)
    if prefixes is None:
        pytest.skip(
            "_FOREIGN_LOCK_GATE_PREFIXES does not exist on this branch "
            "(feature/llm-provider-config @ 8ac14686) — it ships on a "
            "separate, unmerged work stream (project-write-safety spec). "
            "Nothing to assert against yet; see the docstring above. This "
            "is a SKIP, not coverage — it will start actually asserting the "
            "moment that other branch merges into this one, with no edit "
            "needed here. Do not synthesize the symbol locally to make it "
            "run; that would test a stand-in, not the real gate."
        )
    assert prefixes == ("/api/network/", "/api/io/", "/api/simulation/")
    exempt_fn = getattr(main, "_foreign_lock_gate_exempt", None)
    for path in _NEW_ROUTE_PATHS:
        assert not any(path.startswith(p) for p in prefixes), path
        if exempt_fn is not None:
            assert not exempt_fn(path), path


# ─────────────────────────────────────────────────────────────────────────
# A hand-edited profile file must not be able to 500 the settings pane.
#
# `llm_config._SLUG_RE` was `^[a-z0-9-]{1,48}$` matched with `.match()`, and
# Python's `$` matches before a trailing newline — so an id of `"custom\n"`
# passed validation on LOAD. `derive_key_env` then produced
# `PYPSA_GUI_LLM_KEY__CUSTOM\n`, which `app_secrets.is_managed_key` refuses
# (it is `\A`/`\Z` anchored), and `_profile_out`'s `app_secrets.status()`
# call raised `SecretValueError` straight out of the route.
#
# This is the ROUTE-LEVEL consequence of the predicate defect asserted at its
# definition in test_llm_config.py. It is kept as its own test because the
# two fail for different reasons and a future re-widening of the slug class
# could break this one while leaving the regex test green.
# ─────────────────────────────────────────────────────────────────────────


def test_a_hostile_profile_file_cannot_500_the_llm_settings_pane(
    super_admin_client, tmp_path,
):
    """`GET /settings/llm` stays 200 and simply omits the unloadable entry."""
    import json as _json

    from services import llm_config

    llm_config.profiles_path().parent.mkdir(parents=True, exist_ok=True)
    llm_config.profiles_path().write_text(_json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "custom\n", "label": "bad", "preset": "custom",
             "wire": "openai", "base_url": "http://localhost:11434/v1",
             "model": "m", "tools": True, "vision": False, "auth": "bearer",
             "fallback_model": None, "max_output_tokens": None},
            {"id": "good-one", "label": "good", "preset": "custom",
             "wire": "openai", "base_url": "http://localhost:11434/v1",
             "model": "m", "tools": True, "vision": False, "auth": "bearer",
             "fallback_model": None, "max_output_tokens": None},
        ],
    }), encoding="utf-8")

    resp = super_admin_client.get("/api/chat/settings/llm")
    assert resp.status_code == 200, (
        f"settings pane returned {resp.status_code}: {resp.text[:400]}"
    )
    ids = [p["id"] for p in resp.json()["profiles"]]
    assert "custom\n" not in ids
    # The valid sibling still loads — one bad entry must not take the rest down.
    assert "good-one" in ids


def test_a_percent_encoded_newline_in_the_profile_id_is_refused(
    super_admin_client,
):
    """
    The same slug hole, reachable over HTTP rather than by hand-editing.

    `profile_id` is a PATH parameter copied straight into `LLMProfile.id`, and
    Starlette percent-decodes it — so `.../profiles/custom%0A` arrives as
    `"custom\\n"`. While `_SLUG_RE` was `$`-anchored, `save_profiles` accepted
    it, the entry was PERSISTED, and `_profile_out` then raised
    `SecretValueError` out of this very handler. Worse than the file case:
    the bad entry stays on disk, so every subsequent `GET /settings/llm` 500s
    too — a super-admin could permanently brick their own settings pane.

    Must be a clean 422, and nothing may be written.
    """
    from services import llm_config

    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/custom%0A",
        json=_custom_profile_body(),
    )
    assert resp.status_code == 422, (
        f"expected 422, got {resp.status_code}: {resp.text[:300]}"
    )
    # Nothing persisted, and the pane still works.
    ids = {p.id for p in llm_config.load_profiles()[0]}
    assert "custom\n" not in ids
    assert super_admin_client.get("/api/chat/settings/llm").status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# C-13 — a namespaced key slot outliving the profile that owned it.
#
# `LLMProfile.key_env` returns None when `auth == "none"`, and every cleanup
# path asks the profile what its `key_env` is RIGHT NOW. So a profile that
# was created with `auth="bearer"`, given a key, and later edited to
# `auth="none"` (or onto a cataloged preset with its own shared key) leaves
# `PYPSA_GUI_LLM_KEY__<SLUG>` behind with no route that can reach it:
#
#   * DELETE .../key  → 409, "has auth=none and takes no key"
#   * DELETE the profile → the guard reads `key_env is None` and skips
#
# `_write_managed` iterates the stored values, so the orphan is re-persisted
# on every later save, forever. The sharp end is not the stale byte: it is
# that recreating a profile under the same id re-arms the OLD credential,
# so the app authenticates with a key the operator believes they deleted.
#
# The invariant these pin: `PYPSA_GUI_LLM_KEY__<SLUG>` is a pure function of
# the profile id, so it can only ever belong to that one profile — which
# makes it always safe to clear on that profile's behalf, and never safe to
# leave behind once that profile stops using it.
# ─────────────────────────────────────────────────────────────────────────

NAMESPACED = "PYPSA_GUI_LLM_KEY__MY_CUSTOM"


def _create_with_key(client, secret="sk-namespaced-secret-999", **overrides):
    client.put(
        "/api/chat/settings/llm/profiles/my-custom",
        json=_custom_profile_body(**overrides),
    )
    client.put(
        "/api/chat/settings/llm/profiles/my-custom/key", json={"value": secret}
    )
    assert app_secrets.get_stored(NAMESPACED) == secret
    return secret


def test_deleting_a_profile_that_flipped_to_auth_none_still_clears_its_slot(
    super_admin_client,
):
    """The finding as filed: the delete guard reads a `key_env` that is now None."""
    _create_with_key(super_admin_client)
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom",
        json=_custom_profile_body(auth="none"),
    )

    resp = super_admin_client.delete("/api/chat/settings/llm/profiles/my-custom")
    assert resp.status_code == 200, resp.text
    assert app_secrets.get_stored(NAMESPACED) is None, (
        "deleting the profile left its namespaced key behind"
    )


def test_flipping_a_profile_to_auth_none_clears_its_now_unreachable_slot(
    super_admin_client,
):
    """
    The sibling path: the profile still EXISTS, so nothing will ever delete
    it on the profile's behalf. `DELETE .../key` 409s on `auth=none`, so the
    operator has no route to the key at all while the profile lives.
    """
    _create_with_key(super_admin_client)
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom",
        json=_custom_profile_body(auth="none"),
    )
    assert resp.status_code == 200, resp.text

    assert super_admin_client.delete(
        "/api/chat/settings/llm/profiles/my-custom/key"
    ).status_code == 409, "precondition: the key route is closed at auth=none"
    assert app_secrets.get_stored(NAMESPACED) is None, (
        "the key survives with no route that can reach it"
    )


def test_moving_a_profile_onto_a_shared_preset_clears_its_namespaced_slot(
    super_admin_client,
):
    """
    Same orphaning by the other transition. On `preset="openai"` the profile's
    `key_env` becomes the SHARED `OPENAI_API_KEY`, so its private slot is
    just as unreachable as under `auth="none"` — and `DELETE .../key` now
    aims at the shared key instead, never at the orphan.
    """
    _create_with_key(super_admin_client)
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom",
        json=_custom_profile_body(preset="openai", base_url=None),
    )
    assert resp.status_code == 200, resp.text
    assert app_secrets.get_stored(NAMESPACED) is None, (
        "the namespaced key survives a move onto a shared provider key"
    )


def test_recreating_a_profile_does_not_silently_rearm_the_old_key(
    super_admin_client,
):
    """
    The consequence that makes this more than a stale byte: the operator
    removes a profile believing its credential went with it, then makes a new
    profile of the same name — and the app authenticates with the old key,
    which no screen ever showed them.
    """
    _create_with_key(super_admin_client)
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom",
        json=_custom_profile_body(auth="none"),
    )
    super_admin_client.delete("/api/chat/settings/llm/profiles/my-custom")

    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    listing = super_admin_client.get("/api/chat/settings/llm").json()
    recreated = next(p for p in listing["profiles"] if p["id"] == "my-custom")
    assert recreated["key_present"] is False, (
        "a recreated profile inherited the deleted profile's key"
    )


def test_deleting_a_shared_preset_profile_leaves_the_provider_key_alone(
    super_admin_client,
):
    """
    The property the OLD guard existed for, which nothing tested — so
    replacing that guard had to bring its own proof. A profile on
    `preset="openai"` shares `OPENAI_API_KEY` with every other openai
    profile; deleting one must not disarm the rest. (C-6 turns on the same
    fact from the other side: `DELETE .../key` is the ONLY route that can
    clear a shared key, so it must keep aiming at it.)
    """
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/shared-one",
        json=_custom_profile_body(preset="openai", base_url=None),
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/shared-two",
        json=_custom_profile_body(preset="openai", base_url=None),
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/shared-one/key",
        json={"value": "sk-shared-openai-key-1"},
    )
    assert app_secrets.get_stored("OPENAI_API_KEY") == "sk-shared-openai-key-1"

    resp = super_admin_client.delete("/api/chat/settings/llm/profiles/shared-one")
    assert resp.status_code == 200, resp.text
    assert app_secrets.get_stored("OPENAI_API_KEY") == "sk-shared-openai-key-1", (
        "deleting one profile disarmed every other profile on that provider"
    )


def test_deleting_a_profile_clears_its_own_slot_and_nothing_else(
    super_admin_client,
):
    """
    Blast radius, asserted as a whole rather than one name at a time.

    Cleanup now fires unconditionally, so the question is no longer "does it
    skip the shared keys" but "does it touch anything beyond the one slot it
    owns". `ANTHROPIC_API_KEY` is the worst thing it could reach: the two
    built-in profiles read it, and neither can be re-keyed from these
    routes, so disarming it would take the chat panel down with no way back.
    A second custom profile's slot covers the neighbouring case.
    """
    app_secrets.set_secret("ANTHROPIC_API_KEY", "sk-ant-builtin-key-123")
    app_secrets.set_secret("OPENAI_API_KEY", "sk-openai-shared-key-1")
    _create_with_key(super_admin_client)
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/other-custom",
        json=_custom_profile_body(),
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/other-custom/key",
        json={"value": "sk-other-custom-key-2"},
    )
    survivors = {
        "ANTHROPIC_API_KEY": "sk-ant-builtin-key-123",
        "OPENAI_API_KEY": "sk-openai-shared-key-1",
        "PYPSA_GUI_LLM_KEY__OTHER_CUSTOM": "sk-other-custom-key-2",
    }
    assert {k: app_secrets.get_stored(k) for k in survivors} == survivors

    resp = super_admin_client.delete("/api/chat/settings/llm/profiles/my-custom")
    assert resp.status_code == 200, resp.text

    assert app_secrets.get_stored(NAMESPACED) is None, "its own slot must go"
    assert {k: app_secrets.get_stored(k) for k in survivors} == survivors, (
        "deleting one profile reached beyond its own key slot"
    )


def test_editing_a_shared_preset_profile_leaves_the_provider_key_alone(
    super_admin_client,
):
    """
    The new PUT-side cleanup fires whenever a profile does not use its own
    slot — which is EVERY save of a cataloged-preset profile, not just the
    edit that moved it there. It must still clear only the private slot.
    """
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/shared-one",
        json=_custom_profile_body(preset="openai", base_url=None),
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/shared-one/key",
        json={"value": "sk-shared-openai-key-1"},
    )
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/shared-one",
        json=_custom_profile_body(preset="openai", base_url=None, label="Renamed"),
    )
    assert resp.status_code == 200, resp.text
    assert app_secrets.get_stored("OPENAI_API_KEY") == "sk-shared-openai-key-1"


def test_a_rejected_profile_edit_does_not_take_the_key_with_it(
    super_admin_client,
):
    """
    Ordering. The cleanup runs only after `save_profiles` succeeds, so a 422
    leaves both the stored profile and its key exactly as they were — a
    validation error must not be a way to destroy a credential.
    """
    _create_with_key(super_admin_client)
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom",
        json=_custom_profile_body(auth="none", base_url="ftp://nope.example/v1"),
    )
    assert resp.status_code == 422, resp.text
    assert app_secrets.get_stored(NAMESPACED) == "sk-namespaced-secret-999"


# ─────────────────────────────────────────────────────────────────────────
# A8 — the disclosure has to reach the surface the operator is looking at.
# `/settings/api-key` returns `app_secrets.status` verbatim so it carries
# the flag already; `_profile_out` picks fields by hand and does not.
# ─────────────────────────────────────────────────────────────────────────


def test_a_profile_reports_whether_its_key_can_be_redacted(super_admin_client):
    """
    A super-admin who saved a short key has no way to learn it will appear
    in logs unless the surface they saved it on says so.
    """
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom/key", json={"value": "sk-x9k2"}
    )
    listing = super_admin_client.get("/api/chat/settings/llm").json()
    profile = next(p for p in listing["profiles"] if p["id"] == "my-custom")
    assert profile["key_present"] is True
    assert profile["key_redactable"] is False, (
        "a key too short to be redacted is reported as if it were safe"
    )

    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom/key",
        json={"value": "sk-x9k2aaaaaaaa"},
    )
    listing = super_admin_client.get("/api/chat/settings/llm").json()
    profile = next(p for p in listing["profiles"] if p["id"] == "my-custom")
    assert profile["key_redactable"] is True


def test_a_profile_with_no_key_claims_neither(super_admin_client):
    """
    ADR-0001 again, at the route. An `auth="none"` profile has no key
    concept at all and an unset bearer profile has no value yet; neither is
    "will leak", so both ship null rather than False.
    """
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/no-key",
        json=_custom_profile_body(auth="none"),
    )
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/unset-key", json=_custom_profile_body()
    )
    listing = super_admin_client.get("/api/chat/settings/llm").json()
    by_id = {p["id"]: p for p in listing["profiles"]}
    assert by_id["no-key"]["key_redactable"] is None
    assert by_id["unset-key"]["key_redactable"] is None


def test_the_key_routes_report_redactability_at_the_moment_of_saving(
    super_admin_client,
):
    """The PUT response is what the pane renders right after a save."""
    super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom", json=_custom_profile_body()
    )
    resp = super_admin_client.put(
        "/api/chat/settings/llm/profiles/my-custom/key", json={"value": "sk-x9k2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["key_redactable"] is False
