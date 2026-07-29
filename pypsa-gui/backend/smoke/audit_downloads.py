"""
Measure what a REAL cocoa WKWebView does with each download shape the frontend
uses, before writing a line of Task 6.

One case per process. Sequencing them in one page is unsound: the failure mode
under test (the webview NAVIGATES to the file instead of downloading it) kills
the JS context, so every later case would report nothing and be scored as a
different failure than the one that happened.

The only thing faked is the human clicking Save in the NSSavePanel. Whether the
delegate FIRES at all is WebKit's decision, not the panel's, and that is the
measurement.

Not under `tests/`, for the same two reasons `run_chat_smoke.py` is not: it
opens a real window (pytest would collect it on a headless box and fail), and
it is a measurement rather than an assertion.

**Re-run this on Windows.** The Windows half of the finding — that
`edgechromium.on_download_starting` gates on the same `ALLOW_DOWNLOADS` — was
READ from the installed source, not executed. WebView2's `DownloadStarting`
fires on a different set of conditions than WebKit's navigation policy, so
`blob_revoke` and `bundle_anchor` are the two cases worth trusting least.

usage:
    pixi run -e desktop python pypsa-gui/backend/smoke/audit_downloads.py \\
        <case> <allow_downloads:0|1> [open_external_links:0|1]

cases: blob_csv blob_xlsx blob_json blob_revoke url_download_attr
       window_open bundle_anchor big_anchor
"""
from __future__ import annotations

import http.server
import json
import socket
import socketserver
import sys
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

DEST = Path(__file__).parent / "audit-dest"
RESULT = Path(__file__).parent / "audit-result.json"

CASES = {
    # the 12 imperative sites: blob + synthetic <a>.click()
    "blob_csv": "text/csv",
    "blob_xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "blob_json": "application/json",          # a type WebKit CAN render
    # ChatPanel.tsx:544 — a declarative <a href download> at a real URL
    "url_download_attr": None,
    # OverviewPanel.tsx:147 — window.open on the bundle
    "window_open": None,
    # the candidate FIX for the bundle: an anchor with download, no target
    "bundle_anchor": None,
    # the bundle can be hundreds of MB. The native path streams via WKDownload
    # rather than marshalling, but "should" is what this whole audit exists to
    # replace.
    "big_anchor": None,
    # the shape ALL 12 real sites use: revoke on the same tick as click()
    "blob_revoke": "text/csv",
}

BIG_BYTES = 64 * 1024 * 1024

_zip = BytesIO()
with zipfile.ZipFile(_zip, "w") as z:
    z.writestr("bundle/marker.txt", "bundle payload")
ZIP_BYTES = _zip.getvalue()

PAGE = """<!doctype html><meta charset=utf-8><title>audit</title>
<body><h1>audit</h1><div id=log></div>
<script>
function report(o) {
  document.getElementById('log').textContent += JSON.stringify(o) + '\\n';
  if (window.pywebview && window.pywebview.api) window.pywebview.api.record(o);
}
// Proves whether the page survived. If the webview navigated away to render
// the file, this stops firing and its absence IS the finding.
setInterval(function () {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.alive(String(location.pathname));
  }
}, 400);

function run() {
  var kase = %(case)s;
  try {
    if (kase === 'window_open') {
      var w = window.open('/bundle.zip', '_blank');
      report({case: kase, opened: String(w)});
      return;
    }
    if (kase === 'url_download_attr' || kase === 'bundle_anchor' || kase === 'big_anchor') {
      var href = {url_download_attr: '/artifact.csv', bundle_anchor: '/bundle.zip',
                  big_anchor: '/big.bin'}[kase];
      var dl = {url_download_attr: 'case_url.csv', bundle_anchor: 'bundle.zip',
                big_anchor: 'big.bin'}[kase];
      var a = document.createElement('a');
      a.href = href;
      a.download = dl;
      document.body.appendChild(a);
      a.click();
      report({case: kase, clicked: true});
      return;
    }
    var mime = %(mime)s, name = %(name)s;
    // 1 MB, not 16 bytes: a payload small enough to fit in one buffer can hide
    // a revoke-too-early truncation that a real export would hit.
    var chunk = 'col_a,col_b\\n1,2\\n';
    var parts = [];
    for (var i = 0; i < 65536; i++) parts.push(chunk);
    var blob = new Blob(kase === 'blob_revoke' ? parts : [chunk], {type: mime});
    var url = URL.createObjectURL(blob);
    var a2 = document.createElement('a');
    a2.href = url;
    a2.download = name;
    document.body.appendChild(a2);
    a2.click();
    if (kase === 'blob_revoke') {
      // EXACTLY what all 12 real sites do: revoke synchronously, same tick.
      URL.revokeObjectURL(url);
    }
    report({case: kase, clicked: true, url: url.slice(0, 12),
            expected: blob.size});
  } catch (e) {
    report({case: kase, error: String(e)});
  }
}
window.addEventListener('pywebviewready', function () { setTimeout(run, 300); });
</script>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    case = "blob_csv"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            name = {"blob_csv": "case.csv", "blob_xlsx": "case.xlsx",
                    "blob_json": "case.json",
                    "blob_revoke": "case_revoke.csv"}.get(self.case, "case.bin")
            body = (PAGE % {
                "case": json.dumps(self.case),
                "mime": json.dumps(CASES.get(self.case) or "text/plain"),
                "name": json.dumps(name),
            }).encode()
            self._send(body, "text/html; charset=utf-8")
        elif self.path == "/bundle.zip":
            # exactly the shape of GET /api/projects/{name}/bundle
            self._send(ZIP_BYTES, "application/zip",
                       extra={"Content-Disposition": 'attachment; filename="bundle.zip"'})
        elif self.path == "/artifact.csv":
            self._send(b"col_a,col_b\n9,9\n", "text/csv")
        elif self.path == "/big.bin":
            self._send(b"\x5a" * BIG_BYTES, "application/octet-stream",
                       extra={"Content-Disposition": 'attachment; filename="big.bin"'})
        else:
            self.send_error(404)

    def _send(self, body, ctype, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def patch_save_panel(real_appkit):
    """Auto-accept the NSSavePanel into DEST. The human is the only fake part."""
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

    return _Shim()


def main() -> int:
    case = sys.argv[1]
    allow = sys.argv[2] == "1"
    Handler.case = case

    DEST.mkdir(parents=True, exist_ok=True)
    for p in DEST.iterdir():
        p.unlink()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    import webview
    import webview.platforms.cocoa as cocoa

    webview.settings["ALLOW_DOWNLOADS"] = allow
    # Default is True. Left at the default for the decisive cases so the audit
    # measures the configuration `gui.py` actually runs; forced False only where
    # a LinkActivated target=_blank would hand the URL to Safari and download
    # into the user's real ~/Downloads.
    if len(sys.argv) > 3:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = sys.argv[3] == "1"
    else:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    cocoa.AppKit = patch_save_panel(cocoa.AppKit)

    events: list = []
    alive: list = []

    class Api:
        def record(self, o):
            events.append(o)

        def alive(self, pathname):
            alive.append((time.monotonic(), pathname))

    window = webview.create_window(
        f"audit {case} allow={allow}", url=f"http://127.0.0.1:{port}/",
        width=520, height=340, js_api=Api(),
    )

    final_url: list = []

    def finish():
        time.sleep(20.0 if case == "big_anchor" else 6.0)
        # POSITIVE evidence of where the webview ended up. "no heartbeats" only
        # says the JS stopped talking, which a navigation and a broken harness
        # produce identically.
        try:
            final_url.append(window.get_current_url())
        except Exception as exc:
            final_url.append(f"<error {exc}>")
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=finish, daemon=True).start()
    webview.start(private_mode=True)

    landed = [
        {"name": p.name, "bytes": p.stat().st_size} for p in sorted(DEST.iterdir())
    ]
    last_path = alive[-1][1] if alive else None
    result = {
        "case": case,
        "allow_downloads": allow,
        "js_events": events,
        "heartbeats": len(alive),
        "last_pathname": last_path,
        "still_on_app_page": last_path == "/",
        "final_url": final_url[0] if final_url else None,
        "files_landed": landed,
    }
    print("RESULT " + json.dumps(result))
    prior = json.loads(RESULT.read_text()) if RESULT.exists() else []
    prior.append(result)
    RESULT.write_text(json.dumps(prior, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
