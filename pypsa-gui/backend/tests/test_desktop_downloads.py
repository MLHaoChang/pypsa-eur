"""
Downloads (phase 2a, Task 6).

Every export in the app — 12 `createObjectURL` sites, ChatPanel's artifact
anchor, the project bundle — depends on ONE pywebview setting, and its default
is the wrong one. Measured on a real cocoa WKWebView (the harness is
`smoke/audit_downloads.py`, the results are in the plan):

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


def test_the_real_pywebview_default_is_the_dangerous_one():
    """
    Pins the premise rather than trusting it. If a future pywebview ships with
    `ALLOW_DOWNLOADS=True`, `apply` becomes a no-op and this test says so
    loudly instead of leaving a comment that quietly stopped being true.

    Skipped rather than failed where pywebview is absent: this asserts a fact
    about the dependency, not about our code.
    """
    webview = pytest.importorskip("webview")

    assert webview.settings["ALLOW_DOWNLOADS"] is False, (
        "pywebview's default changed — re-read desktop/downloads.py, the "
        "reasoning there is written against False"
    )


def test_the_gui_enables_downloads_before_the_window_can_reach_a_link(tmp_path, monkeypatch):
    """
    The unit tests above pass whether or not anything CALLS `apply`. This is
    the one that fails if the line is dropped from `gui.py` — which is the
    realistic regression, since nothing else in the app references the setting.

    Drives the real `gui.main()` with `webview.start` stubbed, so the assertion
    is against pywebview's actual settings object, not a copy.
    """
    webview = pytest.importorskip("webview")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))

    from desktop import bootstrap, gui

    monkeypatch.setitem(webview.settings, "ALLOW_DOWNLOADS", False)
    monkeypatch.setattr(webview, "create_window", lambda *a, **k: object())
    # Never runs the bootstrap callable: this is about what is true BEFORE the
    # GUI loop hands the user a page with an Export button on it.
    monkeypatch.setattr(webview, "start", lambda *a, **k: None)

    try:
        assert gui.main() == 0
    finally:
        bootstrap.remove_file_logging()

    assert webview.settings["ALLOW_DOWNLOADS"] is True
