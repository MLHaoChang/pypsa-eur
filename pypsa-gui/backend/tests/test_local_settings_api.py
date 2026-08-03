"""
The desktop Settings routes.

Follows `tests/test_local_mode_api.py`: NO importlib.reload and NO sys.modules
surgery. `local_mode.is_local_mode()` reads os.environ per call, so the app
object conftest already imported serves both modes and a fixture only has to
flip the environment.
"""
import os

import pytest
from fastapi.testclient import TestClient

import local_mode
import local_settings
import main


@pytest.fixture
def local_client(_auth_db, monkeypatch, tmp_path):
    """Local mode on, app data isolated to tmp_path."""
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


@pytest.fixture
def no_probe(monkeypatch):
    """Neutralise the network probe. Probe mapping is tested separately."""
    monkeypatch.setattr(
        "routers.local_settings.probe_api_key",
        lambda: ("valid", "Key accepted."),
    )


# ── the gate ──────────────────────────────────────────────────────────────
# These three are the security property of this router. In web mode the
# server's Anthropic key is not something an authenticated user may replace,
# and the log path is not theirs to learn. 404, not 403: the surface does not
# exist there — matching every other door closed by reject_unless_local_mode.

def test_get_is_404_in_web_mode(client):
    assert client.get("/api/local-settings").status_code == 404


def test_put_is_404_in_web_mode(client):
    r = client.put("/api/local-settings/anthropic-key", json={"api_key": "sk-ant-x"})
    assert r.status_code == 404


def test_reveal_is_404_in_web_mode(client):
    assert client.post("/api/local-settings/reveal-log").status_code == 404


# ── read ──────────────────────────────────────────────────────────────────

def test_get_reports_no_key_on_a_fresh_profile(local_client):
    body = local_client.get("/api/local-settings").json()

    assert body["key_set"] is False
    assert body["key_hint"] is None
    assert body["log_path"].endswith("pypsa-gui.log")


def test_get_never_returns_the_key_itself(local_client, no_probe):
    secret = "sk-ant-supersecretvalue9999"
    local_client.put("/api/local-settings/anthropic-key", json={"api_key": secret})

    raw = local_client.get("/api/local-settings").text

    assert secret not in raw
    assert local_client.get("/api/local-settings").json()["key_hint"] == "9999"


# ── write ─────────────────────────────────────────────────────────────────

def test_put_persists_and_publishes_the_key(local_client, no_probe):
    r = local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "valid"
    assert r.json()["key_set"] is True
    assert local_settings.stored_api_key() == "sk-ant-abc123def456"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-abc123def456"


def test_put_takes_effect_without_a_restart(local_client, no_probe):
    """chat_health reads os.environ per request; setting the key must flip it."""
    assert local_client.get("/api/chat/health").json()["anthropic_api_key_present"] is False

    local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert local_client.get("/api/chat/health").json()["anthropic_api_key_present"] is True


def test_empty_string_clears_key_and_environment(local_client, no_probe):
    local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    r = local_client.put("/api/local-settings/anthropic-key", json={"api_key": ""})

    assert r.json()["status"] == "cleared"
    assert r.json()["key_set"] is False
    assert local_settings.stored_api_key() is None
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_key_is_saved_even_when_the_probe_cannot_reach_anthropic(
    local_client, monkeypatch,
):
    """
    Being offline is not a reason to discard what the user just typed — but
    'unreachable' must be reported as unreachable, never as success.
    """
    monkeypatch.setattr(
        "routers.local_settings.probe_api_key",
        lambda: ("unreachable", "connection refused"),
    )

    r = local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert r.json()["status"] == "unreachable"
    assert r.json()["key_set"] is True
    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


def test_a_rejected_key_is_still_saved_and_reported_distinctly(
    local_client, monkeypatch,
):
    monkeypatch.setattr(
        "routers.local_settings.probe_api_key",
        lambda: ("rejected", "invalid x-api-key"),
    )

    r = local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert r.json()["status"] == "rejected"
    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


# ── probe mapping ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "exc_name, expected",
    [
        ("AuthenticationError", "rejected"),
        ("PermissionDeniedError", "rejected"),
        ("APIConnectionError", "unreachable"),
    ],
)
def test_probe_maps_sdk_exceptions(monkeypatch, exc_name, expected):
    import anthropic

    from routers import local_settings as routes

    exc_class = getattr(anthropic, exc_name)

    class _Models:
        def list(self, **kwargs):
            # `__new__` without `__init__`: the SDK's exceptions require
            # (message, response, body) to construct, and none of that is
            # relevant here — only the class matters, because that is what the
            # `except` clause in probe_api_key dispatches on.
            raise exc_class.__new__(exc_class)

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    status, _detail = routes.probe_api_key()

    assert status == expected


def test_probe_reports_valid_when_the_call_returns(monkeypatch):
    import anthropic

    from routers import local_settings as routes

    class _Models:
        def list(self, **kwargs):
            return object()

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    assert routes.probe_api_key()[0] == "valid"


def test_probe_maps_an_unexpected_exception_to_unreachable(monkeypatch):
    """Unknown failure is 'we could not check', never 'the key is fine'."""
    import anthropic

    from routers import local_settings as routes

    class _Models:
        def list(self, **kwargs):
            raise RuntimeError("something else entirely")

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    assert routes.probe_api_key()[0] == "unreachable"


def test_probe_reports_sdk_not_installed_when_anthropic_import_fails(monkeypatch):
    """
    The one probe status with no other coverage. Setting the sys.modules entry
    to None makes the next `import anthropic` raise ImportError, same as the
    package genuinely being absent from a build.
    """
    import sys

    from routers import local_settings as routes

    monkeypatch.setitem(sys.modules, "anthropic", None)

    status, _detail = routes.probe_api_key()

    assert status == "sdk_not_installed"


# ── secret hygiene ────────────────────────────────────────────────────────

def test_the_key_literal_never_reaches_a_log_or_a_response(
    local_client, monkeypatch, caplog,
):
    """
    Drives the REAL probe code path — only the SDK client is faked, so the
    route's own exception handling is what runs. No network: a test that dials
    Anthropic is slow, flaky, and fails offline.

    Nothing may carry the key literal out — not the response, not a log record.
    """
    import logging

    import anthropic

    secret = "sk-ant-donotlogme1234567890"

    class _Models:
        def list(self, **kwargs):
            raise anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    with caplog.at_level(logging.DEBUG):
        put = local_client.put(
            "/api/local-settings/anthropic-key", json={"api_key": secret},
        )
        get = local_client.get("/api/local-settings")

    # Positive control: prove caplog actually captured something, so the
    # `secret not in caplog.text` assertions above can't pass vacuously
    # against an empty capture.
    assert "AuthenticationError" in caplog.text
    assert secret not in caplog.text
    assert secret not in put.text
    assert secret not in get.text


def test_probe_detail_never_carries_sdk_exception_text(monkeypatch, caplog):
    """
    The detail strings are fixed. An SDK message that happened to embed the key
    could not survive into the response, because it is never formatted in.
    """
    import logging

    import anthropic

    from routers import local_settings as routes

    class _Models:
        def list(self, **kwargs):
            raise RuntimeError("x-api-key sk-ant-leakedthroughtheexception")

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    with caplog.at_level(logging.DEBUG):
        status, detail = routes.probe_api_key()

    assert status == "unreachable"
    # Positive control: prove caplog actually captured something, so the
    # "not in caplog.text" assertion below can't pass vacuously against an
    # empty capture.
    assert "RuntimeError" in caplog.text
    assert "sk-ant-leakedthroughtheexception" not in detail
    assert "sk-ant-leakedthroughtheexception" not in caplog.text


# ── reveal ────────────────────────────────────────────────────────────────

def test_reveal_runs_a_fixed_command_with_no_request_input(local_client, monkeypatch):
    """
    The whole safety argument for the only subprocess call in the app: every
    element of argv is either a literal or derived from app_paths. If a future
    change lets a request parameter reach argv, this test is what catches it.
    """
    seen = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return None

    monkeypatch.setattr("routers.local_settings.subprocess.run", _fake_run)

    r = local_client.post("/api/local-settings/reveal-log")

    assert r.status_code == 200, r.text
    assert r.json()["revealed"] is True
    assert isinstance(seen["argv"], list), "argv must be a list — never a shell string"
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["check"] is False

    # Every element is either a hardcoded literal or the server-computed path.
    # Nothing else may ever appear here.
    from pathlib import Path

    log_path = r.json()["log_path"]
    literals = {"open", "-R", "explorer", "xdg-open"}
    permitted_paths = {log_path, str(Path(log_path).parent), f"/select,{log_path}"}
    for part in seen["argv"]:
        assert part in literals or part in permitted_paths, (
            f"argv element {part!r} is neither a hardcoded literal nor the "
            f"server-computed log path — a request parameter may have reached argv"
        )


def test_reveal_creates_the_log_file_if_it_is_missing(local_client, monkeypatch):
    """A reveal that selects nothing reads as a broken button."""
    monkeypatch.setattr("routers.local_settings.subprocess.run", lambda *a, **k: None)

    r = local_client.post("/api/local-settings/reveal-log")

    from pathlib import Path
    assert Path(r.json()["log_path"]).exists()


def test_reveal_failure_is_reported_not_raised(local_client, monkeypatch):
    """
    200 with revealed=false, not a 500. The pane still shows the path and a
    Copy button, so the feature degrades instead of dead-ending.
    """
    def _boom(*args, **kwargs):
        raise OSError("no file manager on this box")

    monkeypatch.setattr("routers.local_settings.subprocess.run", _boom)

    r = local_client.post("/api/local-settings/reveal-log")

    assert r.status_code == 200, r.text
    assert r.json()["revealed"] is False
    assert "no file manager" in r.json()["detail"]
    assert r.json()["log_path"].endswith("pypsa-gui.log")


@pytest.mark.parametrize(
    "platform, expected_head",
    [("darwin", ["open", "-R"]), ("win32", ["explorer"]), ("linux", ["xdg-open"])],
)
def test_reveal_argv_per_platform(monkeypatch, tmp_path, platform, expected_head):
    from routers import local_settings as routes

    monkeypatch.setattr(routes.sys, "platform", platform)

    argv = routes._reveal_argv(tmp_path / "pypsa-gui.log")

    assert argv[: len(expected_head)] == expected_head
