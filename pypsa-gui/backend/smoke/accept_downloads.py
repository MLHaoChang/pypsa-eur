"""
Task 6 end-to-end, in the REAL app.

The audit measured the mechanism in a throwaway page. This drives the shipped
`gui.main()`: real single-instance lock, real socket, real backend import, real
uvicorn, real SPA, and a real click on the real "Export bundle" button. The
only substitution is the human clicking Save in the native panel — whether the
panel is reached at all is what is being measured, and that is WebKit's
decision, not the panel's.

Two things this can catch that the audit could not:
  * `gui.main()` not actually applying the setting in the shipped path.
  * the button being wired to something other than what the vitest render
    test asserts (that test renders the component in jsdom; this one clicks
    it inside WebKit).
"""
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

# Relative, not absolute: this repo is developed on Windows and macOS both.
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

HERE = Path(
    os.environ.get("PYPSAGUI_ACCEPT_DIR")
    or Path(__file__).resolve().parent / "_accept_downloads"
)
HERE.mkdir(parents=True, exist_ok=True)
DEST = HERE / "download-dest"

# GUARD: a destructive save (POST /api/projects/<name>) runs below. It must
# never be able to reach the real projects tree, which is irreplaceable.
# Asserted rather than assumed — an unset variable would silently target it.
_appdata = os.environ.get("PYPSAGUI_APP_DATA_DIR", "")
assert _appdata, "refusing to run: set PYPSAGUI_APP_DATA_DIR to a throwaway directory"
assert BACKEND / "projects" not in Path(_appdata).resolve().parents, \
    f"refusing to run: PYPSAGUI_APP_DATA_DIR is inside the real projects tree ({_appdata})"
assert Path(_appdata).resolve() != (BACKEND / "projects").resolve(), \
    "refusing to run: PYPSAGUI_APP_DATA_DIR IS the real projects tree"

DEST.mkdir(parents=True, exist_ok=True)
for p in DEST.iterdir():
    p.unlink()

import webview


def _auto_accept_save_panel():
    """
    Answer the native Save panel without a human, into DEST.

    macOS only. Windows reaches the same `ALLOW_DOWNLOADS` switch through
    `edgechromium.on_download_starting`, but its dialog is a
    `WinForms.SaveFileDialog` and needs its own shim — and importing the cocoa
    backend there fails outright. Until that exists the run is still useful on
    Windows; a human clicks Save.
    """
    if sys.platform != "darwin":
        print("NOTE: not darwin — the save dialog will NOT be auto-accepted. "
              "Click Save when it appears.", flush=True)
        return

    import webview.platforms.cocoa as cocoa

    real_appkit = cocoa.AppKit

    class _Panel:
        _name = "unnamed.bin"

        def setDirectoryURL_(self, _): pass
        def setTitle_(self, _): pass
        def setNameFieldStringValue_(self, n): self._name = str(n) or self._name
        def runModal(self): return real_appkit.NSFileHandlingPanelOKButton
        def filename(self): return str(DEST / self._name)

    class _NSSavePanel:
        @staticmethod
        def savePanel(): return _Panel()

    class _Shim:
        def __getattr__(self, name):
            if name == "NSSavePanel":
                return _NSSavePanel
            return getattr(real_appkit, name)

    cocoa.AppKit = _Shim()


_auto_accept_save_panel()

from desktop import gui, launcher  # noqa: E402

launcher.resolve_legacy_root = lambda backend_dir=None: None

PROJECT = "DownloadProject"
# 'button' clicks the real control; 'probe' clicks a hand-built anchor. Run
# SEPARATELY: the backend's Content-Disposition overrides the anchor's
# `download` attribute, so both produce the same filename and a single run
# cannot attribute the file to either one.
ACTION = sys.argv[1] if len(sys.argv) > 1 else "button"
out = {"action": ACTION}


def api(method, path, body=None, port=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    hdrs = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path, json.dumps(body) if body is not None else None, hdrs)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw or b"null")
    except Exception:
        return r.status, raw[:200]


def js(window, script, label):
    try:
        return window.evaluate_js(script)
    except Exception as exc:
        out.setdefault("js_errors", {})[label] = repr(exc)
        return None


def work():
    for _ in range(900):
        time.sleep(0.25)
        st = getattr(gui, "_S", None)
        if st and st.get("main_window") is not None and st.get("server"):
            break
    else:
        out["never_up"] = True
        print("RESULT " + json.dumps(out), flush=True)
        os._exit(1)

    port = st["port"]
    window = st["main_window"]

    # The setting, read from the LIVE pywebview global in the shipped path —
    # not from a copy, and not from `downloads.apply` being called on a dict.
    out["allow_downloads_live"] = webview.settings["ALLOW_DOWNLOADS"]

    try:
        out["reset"] = api("POST", "/api/network/reset", port=port)[0]
        out["add_bus"] = api("POST", "/api/network/buses",
                             {"name": "DownloadBus", "v_nom": 380.0}, port=port)[0]
        out["save"] = api("POST", f"/api/projects/{PROJECT}", port=port)[0]

        # The app opens on its project launcher. Reload so the just-saved
        # project appears there, then open it the way a user does — by
        # clicking. No store seeding: the point of an end-to-end is that the
        # path is the real one.
        js(window, "location.reload(); true", "reload")
        time.sleep(8)

        out["click_open"] = js(window, f"""
          (function () {{
            var want = 'Open ' + {json.dumps(PROJECT)};
            var els = [].slice.call(document.querySelectorAll('button,[role=button],a'));
            var t = els.filter(function (e) {{
              return (e.textContent || '').trim() === want;
            }});
            if (!t.length) return 'not-found';
            t[0].click();
            return 'clicked';
          }})()
        """, "open")
        time.sleep(10)

        out["path_after_open"] = js(window, "location.pathname", "loc0")

        out["click_project_info"] = js(window, """
          (function () {
            var els = [].slice.call(document.querySelectorAll('button,[role=button],a,div'));
            var t = els.filter(function (e) {
              return (e.textContent || '').trim() === 'Project info';
            });
            if (!t.length) return 'not-found';
            t[t.length - 1].click();
            return 'clicked';
          })()
        """, "project_info")
        time.sleep(4)
        out["buttons"] = js(window, """
          [].slice.call(document.querySelectorAll('button,[role=button],a'))
            .map(function (e) { return (e.textContent || '').trim().slice(0, 40); })
            .filter(function (t) { return t; }).slice(0, 60)
        """, "buttons")

        # There are TWO "Export bundle" controls. The sidebar one OPENS AN
        # EXPORT MODAL (Sidebar.tsx) and downloads nothing by itself; the
        # OverviewPanel one is the download. Clicking the first match hit the
        # modal opener and produced no file — which read exactly like a broken
        # download. Take the LAST match, which is the panel that was just
        # opened above it.
        out["export_count"] = js(window, """
          [].slice.call(document.querySelectorAll('button'))
            .filter(function (e) { return /Export bundle/i.test(e.textContent || ''); })
            .length
        """, "count")
        if ACTION == "blob":
            # A REAL `URL.createObjectURL` + `a.download` site — Sidebar.tsx's
            # export modal. The claim that 13 sites need no change rests on
            # the audit page until one of the real ones is exercised in the
            # real app. This is that.
            out["open_modal"] = js(window, """
              (function () {
                var t = [].slice.call(document.querySelectorAll('button,[role=button],a'))
                  .filter(function (e) { return /Export bundle/i.test(e.textContent || ''); });
                if (!t.length) return 'not-found';
                t[0].click();
                return 'clicked';
              })()
            """, "open_modal")
            time.sleep(3)
            out["click_download_file"] = js(window, """
              (function () {
                var t = [].slice.call(document.querySelectorAll('button'))
                  .filter(function (e) { return (e.textContent || '').trim() === 'Download File'; });
                if (!t.length) return 'not-found';
                t[0].click();
                return 'clicked';
              })()
            """, "download_file")

        elif ACTION == "spy":
            # Record every anchor click the page makes, and every error, then
            # press the real button. This distinguishes "the export handler
            # never ran" from "it ran and WebKit ignored the anchor" — which an
            # empty destination directory cannot. It is also how the stale-build
            # trap was caught: zero anchor clicks with zero errors meant the
            # served bundle predated the source change.
            #
            # `href` should be a `blob:` URL. The export fetches the bytes
            # through axios first and saves those, so that an error RESPONSE is
            # never written to disk as a bundle; an `/api/...` href here would
            # mean that regression is back.
            js(window, """
              window.__clicks = [];
              window.__errors = [];
              window.addEventListener('error', function (e) {
                window.__errors.push(String(e.message));
              });
              var orig = HTMLAnchorElement.prototype.click;
              HTMLAnchorElement.prototype.click = function () {
                window.__clicks.push({
                  href: this.getAttribute('href'),
                  download: this.getAttribute('download'),
                  attached: document.body.contains(this)
                });
                return orig.apply(this, arguments);
              };
              true
            """, "spy_install")
            out["click_export"] = js(window, """
              (function () {
                var t = [].slice.call(document.querySelectorAll('button')).filter(function (e) {
                  return /Export bundle/i.test(e.textContent || '');
                });
                if (!t.length) return 'not-found';
                var b = t[t.length - 1];
                b.click();
                return {disabled: !!b.disabled, html: b.outerHTML.slice(0, 200)};
              })()
            """, "export")
            time.sleep(6)
            out["anchor_clicks"] = js(window, "window.__clicks", "clicks")
            out["page_errors"] = js(window, "window.__errors", "errors")

        elif ACTION == "button":
            out["click_export"] = js(window, """
              (function () {
                var t = [].slice.call(document.querySelectorAll('button')).filter(function (e) {
                  return /Export bundle/i.test(e.textContent || '');
                });
                if (!t.length) return 'not-found';
                t[t.length - 1].click();
                return 'clicked';
              })()
            """, "export")
        else:
            # Same anchor shape the audit measured, against the real bundle
            # endpoint in the real window. If this lands a file and the button
            # does not, the defect is the wiring; if neither does, it is the
            # mechanism inside a real SPA.
            out["probe_anchor"] = js(window, f"""
              (function () {{
                var a = document.createElement('a');
                a.href = '/api/projects/' + encodeURIComponent({json.dumps(PROJECT)}) + '/bundle';
                a.download = 'probe.pypsaproj.zip';
                document.body.appendChild(a);
                a.click();
                a.remove();
                return 'clicked';
              }})()
            """, "probe")

        for _ in range(30):
            time.sleep(1)
            if any(DEST.iterdir()):
                break
        out["seconds_to_land"] = _ + 1

        out["still_on_app"] = js(window, "location.pathname", "loc")
        out["page_text_tail"] = js(
            window, "(document.body.innerText || '').slice(-400)", "text")
    except Exception as exc:
        out["error"] = repr(exc)

    out["files_landed"] = [
        {"name": p.name, "bytes": p.stat().st_size} for p in sorted(DEST.iterdir())
    ]

    h = st.get("close_handler")
    if h:
        h.on_closing()
        h.wait_for_completion(90)
    print("RESULT " + json.dumps(out), flush=True)


def watchdog():
    time.sleep(300)
    out["timeout"] = True
    out["files_landed"] = [
        {"name": p.name, "bytes": p.stat().st_size} for p in sorted(DEST.iterdir())
    ]
    print("RESULT " + json.dumps(out), flush=True)
    os._exit(1)


_orig = gui._bootstrap


def spy(state):
    gui._S = state
    return _orig(state)


gui._bootstrap = spy
threading.Thread(target=watchdog, daemon=True).start()
threading.Thread(target=work, daemon=True).start()
code = gui.main()
print("EXITED " + json.dumps({"status": code}), flush=True)
