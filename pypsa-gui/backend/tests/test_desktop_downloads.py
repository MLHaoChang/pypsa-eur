"""
Downloads (phase 2a, Task 6).

Every export in the app — 11 `createObjectURL` sites and ChatPanel's artifact
anchor — depends on ONE pywebview setting, and its default is the wrong one.
Measured on a real cocoa WKWebView (the harness is `smoke/audit_downloads.py`,
the results are in the plan):

    ALLOW_DOWNLOADS=True   save panel, correct bytes, page intact
    ALLOW_DOWNLOADS=False  csv/json: THE WEBVIEW NAVIGATES TO THE FILE and the
                           SPA is gone, in a window with no back button and no
                           address bar. xlsx: silent no-op.

So this is not "downloads are a nice-to-have that is off". Off is an app that
destroys itself when the user clicks Export.

`downloads.py` is webview-free for the same reason `launcher.py` and
`bootstrap.py` are: the backend suite covers it on a headless box.
"""
from __future__ import annotations

import pytest

from desktop import downloads


def test_downloads_are_enabled_or_export_navigates_the_app_away_from_itself():
    """
    The whole task in one assertion. `cocoa.py:279` reads

        if action.shouldPerformDownload() and webview_settings['ALLOW_DOWNLOADS']

    so with the setting off a `download` anchor is not a download at all — it
    falls through to ordinary navigation.
    """
    settings = {"ALLOW_DOWNLOADS": False}

    downloads.apply(settings)

    assert settings["ALLOW_DOWNLOADS"] is True


def test_apply_touches_nothing_else():
    """
    `webview.settings` is a live global holding unrelated keys — the SSL
    policy, the drag-region selector, the debug-menu switches. A helper that
    rebuilt the mapping instead of setting one key would silently reset them.
    """
    settings = {
        "ALLOW_DOWNLOADS": False,
        "IGNORE_SSL_ERRORS": False,
        "OPEN_EXTERNAL_LINKS_IN_BROWSER": True,
        "DRAG_REGION_SELECTOR": ".pywebview-drag-region",
    }

    downloads.apply(settings)

    assert settings["IGNORE_SSL_ERRORS"] is False
    assert settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert settings["DRAG_REGION_SELECTOR"] == ".pywebview-drag-region"


def test_apply_is_idempotent():
    """The retry path in `gui.py` could reach this twice."""
    settings = {"ALLOW_DOWNLOADS": False}

    downloads.apply(settings)
    downloads.apply(settings)

    assert settings["ALLOW_DOWNLOADS"] is True


def _webview():
    """
    Import pywebview, FAILING loudly if it is missing.

    Deliberately not `pytest.importorskip`. A skip is exactly the failure mode
    this file exists to prevent: these two tests already silently skipped for a
    whole review cycle because the canonical `pixi run gui-tests` resolved to
    an environment without pywebview, and the suite read green while the
    setting they guard could have been deleted. `test_desktop_environment.py`
    states the same rule — "a skip here is the test quietly evaporating".
    """
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - environment defect
        raise AssertionError(
            "pywebview is missing. These tests guard the desktop download "
            "setting and must not be skipped — run `pixi run gui-tests`, which "
            "resolves to the `test` environment."
        ) from exc
    return webview


def test_the_real_pywebview_default_is_the_dangerous_one():
    """
    Pins the premise rather than trusting it. If a future pywebview ships with
    `ALLOW_DOWNLOADS=True`, `apply` becomes a no-op and this test says so
    loudly instead of leaving a comment that quietly stopped being true.
    """
    webview = _webview()

    assert webview.settings["ALLOW_DOWNLOADS"] is False, (
        "pywebview's default changed — re-read desktop/downloads.py, the "
        "reasoning there is written against False"
    )


def test_the_gui_enables_downloads_BEFORE_any_window_exists(tmp_path, monkeypatch):
    """
    The unit tests above pass whether or not anything CALLS `apply`. This is
    the one that fails if the line is dropped from `gui.py` — the realistic
    regression, since nothing else in the app references the setting.

    Asserted from INSIDE the `create_window` stub, not after `main()` returns.
    Checking afterwards proved only that `apply` ran somewhere in `main()`: an
    earlier version passed with the call moved below `_start_gui(...)`, where
    in the real app it would land after `webview.start()` had blocked for the
    whole session — every export taking the navigate-away path, suite green.
    """
    webview = _webview()
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))

    from desktop import bootstrap, gui, launcher

    seen: dict = {}
    sockets: list = []

    real_bind = launcher.bind_socket

    def bind_and_record():
        # `main()` binds a listening socket that only `_bootstrap` takes
        # ownership of, and `start` is stubbed so `_bootstrap` never runs.
        # Without this the test leaks a bound port and an fd per run.
        sock = real_bind()
        sockets.append(sock)
        return sock

    monkeypatch.setattr(launcher, "bind_socket", bind_and_record)
    monkeypatch.setitem(webview.settings, "ALLOW_DOWNLOADS", False)
    def fake_create_window(*_args, **_kwargs):
        seen.setdefault("at_first_window", webview.settings["ALLOW_DOWNLOADS"])
        return object()

    monkeypatch.setattr(webview, "create_window", fake_create_window)
    monkeypatch.setattr(webview, "start", lambda *a, **k: None)

    try:
        assert gui.main() == 0
    finally:
        bootstrap.remove_file_logging()
        for sock in sockets:
            sock.close()

    # Without this the socket cleanup above silently becomes a no-op if
    # `gui.py` ever switches to `from desktop.launcher import bind_socket` —
    # the patch is on the module attribute, so a direct import bypasses it and
    # the test leaks a bound port per run while still passing.
    assert sockets, "bind_socket was not intercepted — the cleanup did nothing"
    assert seen.get("at_first_window") is True, (
        "downloads were not enabled before the first window was created"
    )
