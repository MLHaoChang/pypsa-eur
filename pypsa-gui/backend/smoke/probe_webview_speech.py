"""
Measure what a REAL cocoa WKWebView exposes for speech.

Same treatment `smoke/audit_downloads.py` gives downloads. Serves the probe
over loopback HTTP because the packaged app serves its SPA from
http://127.0.0.1:<port>, which WebKit treats as a secure context — an earlier
version of this probe loaded inline HTML, was NOT a secure context, and
reported a false `getUserMedia: false`.

    pixi run -e test python pypsa-gui/backend/smoke/probe_webview_speech.py

Measured 2026-08-05 on macOS 15 / arm64:
    webkitSpeechRecognition: "function"   SpeechRecognition: undefined
    speechSynthesis: object, 219 voices   mediaDevices: false
    secureContext: true                   .start() -> error: not-allowed
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import webview

PAGE = b"""<html><body style="font:13px system-ui">probing...
<script>
window.__probe = {phase: "init", events: []};
try {
  var Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  window.__probe.caps = {
    SpeechRecognition: typeof window.SpeechRecognition,
    webkitSpeechRecognition: typeof window.webkitSpeechRecognition,
    speechSynthesis: typeof window.speechSynthesis,
    voices_now: window.speechSynthesis ? window.speechSynthesis.getVoices().length : -1,
    mediaDevices: !!navigator.mediaDevices,
    secureContext: window.isSecureContext,
    origin: location.origin
  };
  var r = new Ctor();
  r.onstart = function(){ window.__probe.events.push("start"); };
  r.onerror = function(e){ window.__probe.events.push("error:" + (e.error || "?")); };
  r.start();
  window.__probe.phase = "started";
} catch (e) {
  window.__probe.phase = "threw";
  window.__probe.events.push("throw:" + (e && e.name ? e.name : String(e)));
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


def _probe(window):
    import time

    try:
        time.sleep(5)  # let onstart / onerror fire
        print("PROBE_JSON:" + json.dumps(json.loads(
            window.evaluate_js("JSON.stringify(window.__probe)")
        )))
    except Exception as exc:  # noqa: BLE001 — a probe that dies must say why
        print("PROBE_ERROR:" + repr(exc))
    finally:
        window.destroy()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _win = webview.create_window(
        "speech capability probe",
        url=f"http://127.0.0.1:{server.server_address[1]}/",
        width=420,
        height=160,
    )
    webview.start(_probe, _win)
