"""
The launch splash: one self-contained HTML string.

It CANNOT be served by the backend — the whole reason it exists is that the
backend is not up yet. So no external stylesheet, no font, no image: a webview
that has to fetch anything shows a blank rectangle for however long the fetch
takes, which on a first launch is exactly when the user is least sure the app
is working.

Stages are supplied by `bootstrap.STAGES` and pushed in with `evaluate_js`.
"""
from __future__ import annotations

import json

# Deliberately plain. This is on screen for ~2 s on a warm launch and for
# minutes on a first-run import, and it must look intentional in both cases.
HTML = """
<!doctype html>
<meta charset="utf-8">
<title>PyPSA GUI</title>
<style>
  :root { color-scheme: light dark; }
  html, body {
    margin: 0; height: 100%;
    display: flex; align-items: center; justify-content: center;
    font: 14px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif;
    background: #f6f7f9; color: #24292f;
  }
  @media (prefers-color-scheme: dark) {
    html, body { background: #16181d; color: #e6edf3; }
  }
  .wrap { text-align: center; max-width: 30rem; padding: 0 2rem; }
  h1 { font-size: 1.05rem; font-weight: 600; margin: 0 0 1.25rem; letter-spacing: .01em; }
  #stage { font-size: .875rem; opacity: .75; min-height: 1.5em; }
  #detail {
    font-size: .8125rem; opacity: 0; margin-top: .75rem;
    transition: opacity .4s ease;
  }
  #detail.show { opacity: .6; }
  .bar { width: 13rem; height: 2px; margin: 1.25rem auto 0;
         background: rgba(127,127,127,.22); overflow: hidden; border-radius: 2px; }
  .bar i { display: block; width: 40%; height: 100%; border-radius: 2px;
           background: currentColor; opacity: .55; animation: slide 1.4s ease-in-out infinite; }
  @keyframes slide { 0% { transform: translateX(-105%) } 100% { transform: translateX(255%) } }
  .failed .bar { display: none; }
  .failed #stage { opacity: 1; }
</style>
<div class="wrap">
  <h1>PyPSA GUI</h1>
  <div id="stage">Starting…</div>
  <div class="bar"><i></i></div>
  <div id="detail"></div>
</div>
<script>
  window.__stage = function (text) {
    document.getElementById('stage').textContent = text;
  };
  // Shown only if a stage outlasts its expected budget, so the user learns
  // that a long first-run import is working rather than stuck.
  window.__detail = function (text) {
    var el = document.getElementById('detail');
    el.textContent = text;
    el.classList.add('show');
  };
  window.__failed = function (text) {
    document.body.classList.add('failed');
    document.getElementById('stage').textContent = text;
    document.getElementById('detail').textContent = '';
  };
</script>
"""


def set_stage_js(text: str) -> str:
    """`evaluate_js` payload for a stage change. JSON-quoted, not f-string."""
    return f"window.__stage({json.dumps(text)})"


def set_detail_js(text: str) -> str:
    return f"window.__detail({json.dumps(text)})"


def failed_js(text: str) -> str:
    return f"window.__failed({json.dumps(text)})"
