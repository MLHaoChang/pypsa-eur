"""
The launch splash: one self-contained HTML string.

It CANNOT be served by the backend — the whole reason it exists is that the
backend is not up yet. So no external stylesheet, no font file, no image
request: a webview that has to fetch anything shows a blank rectangle for
however long the fetch takes, which on a first launch is exactly when the user
is least sure the app is working.

That constraint is why the artwork here is CSS and inline SVG rather than the
photograph the web landing page uses. A background image would have to be
base64'd into this module — hundreds of kilobytes of Python source, re-read by
every future maintainer — and a drawn grid carries the same brand for about two.

Stages are supplied by `bootstrap.STAGES` and pushed in with `evaluate_js`.

Palette lifted from `frontend/public/brand.css` rather than re-invented, so the
splash and the app that replaces it are recognisably one product:

    --brand-black #151112   --brand-red #ff5252   --brand-red-soft #ff8a8a
    --brand-red-deep #e03131   --brand-ink #f4eaea   --brand-ink-dim #b9a7a9
"""
from __future__ import annotations

import html as _html
import json

# Shared by the splash and the message windows, so the two cannot drift apart.
_STYLE = """
  :root {
    --black:#151112; --red:#ff5252; --red-soft:#ff8a8a; --red-deep:#e03131;
    --red-deeper:#b02226; --ink:#f4eaea; --ink-dim:#b9a7a9;
    color-scheme: dark;
  }
  * { box-sizing: border-box; }
  html, body {
    margin:0; height:100%; overflow:hidden;
    background: var(--black); color: var(--ink);
    font: 15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  /* Two bloom sources, deliberately off-centre. A single centred radial reads
     as a vignette rather than as light. */
  body::before {
    content:""; position:fixed; inset:0; pointer-events:none;
    background:
      radial-gradient(58% 46% at 18% 4%, rgba(255,82,82,.20), transparent 70%),
      radial-gradient(52% 60% at 98% 94%, rgba(176,34,38,.30), transparent 72%);
  }
  .grid { position:fixed; inset:0; opacity:.55; pointer-events:none; }
  .wrap { position:relative; height:100%; display:flex; flex-direction:column;
          padding: 40px 44px 26px; }
"""

# An abstracted one-line diagram — buses on a horizon, lines between them.
# Drawn rather than photographed, and quiet enough to read as texture.
_GRID_SVG = """
<svg class="grid" viewBox="0 0 920 580" preserveAspectRatio="xMidYMid slice"
     xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="ln" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0"   stop-color="#ff8a8a" stop-opacity="0"/>
      <stop offset=".45" stop-color="#ff5252" stop-opacity=".55"/>
      <stop offset="1"   stop-color="#b02226" stop-opacity=".10"/>
    </linearGradient>
    <filter id="gl" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="5"/>
    </filter>
  </defs>
  <g stroke="url(#ln)" fill="none" stroke-width="1.15">
    <path d="M-20 470 L150 392 L322 438 L494 356 L666 404 L838 330 L960 372"/>
    <path d="M-20 528 L150 452 L322 498 L494 416 L666 464 L838 390 L960 432"/>
    <path d="M150 392 L150 528 M322 438 L322 498 M494 356 L494 416
             M666 404 L666 464 M838 330 L838 390"/>
    <path d="M-20 300 L200 246 L430 292 L640 214 L920 268" opacity=".45"/>
    <path d="M60 580 L60 300 M270 580 L270 246 M520 580 L520 292 M760 580 L760 214"
          opacity=".22"/>
  </g>
  <g fill="#ff8a8a" filter="url(#gl)" opacity=".85">
    <circle cx="150" cy="392" r="3.4"/><circle cx="494" cy="356" r="3.4"/>
    <circle cx="838" cy="330" r="3.4"/><circle cx="640" cy="214" r="2.6"/>
  </g>
</svg>
"""

HTML = f"""<!doctype html>
<meta charset="utf-8">
<title>PyPSA Studio</title>
<style>
{_STYLE}
  .top {{ display:flex; align-items:center; gap:10px; }}
  .logo {{
    width:30px; height:30px; border-radius:9px; display:grid; place-items:center;
    font-weight:900; font-size:14px; color:#14090a;
    background: linear-gradient(140deg, var(--red-soft), var(--red) 55%, var(--red-deep));
    box-shadow: 0 8px 22px rgba(255,82,82,.34);
  }}
  .word {{ font-size:15px; font-weight:700; letter-spacing:-.01em; }}
  .word span {{ color: var(--red); }}

  .body {{ flex:1; display:flex; align-items:center; gap:36px; min-height:0; }}
  .hero {{ flex:1 1 0; min-width:0; }}
  .eyebrow {{
    display:inline-flex; align-items:center; gap:8px; border-radius:999px;
    border:1px solid rgba(255,255,255,.14); background:rgba(33,27,28,.55);
    padding:5px 12px; font-size:10.5px; letter-spacing:.15em; text-transform:uppercase;
    color: var(--ink-dim);
  }}
  .dot {{ width:6px; height:6px; border-radius:50%; background:var(--red);
          box-shadow:0 0 10px rgba(255,82,82,.9); animation:pulse 1.8s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:.35 }} }}
  h1 {{ font-size:40px; line-height:1.06; letter-spacing:-.035em; font-weight:800;
        margin:18px 0 14px; }}
  h1 em {{ font-style:normal; color: var(--red); }}
  .lede {{ color: var(--ink-dim); font-size:13.5px; max-width:34ch; margin:0; }}

  .card {{
    width:288px; flex:0 0 auto; border-radius:20px; padding:20px;
    border:1px solid rgba(255,255,255,.14); background:rgba(33,27,28,.72);
    box-shadow:0 30px 80px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.08);
    backdrop-filter: blur(22px);
  }}
  .card h2 {{ font-size:10.5px; letter-spacing:.15em; text-transform:uppercase;
              color:var(--ink-dim); margin:0 0 14px; font-weight:600; }}
  .feat {{ display:flex; gap:10px; align-items:flex-start; margin-bottom:11px; font-size:12.5px; }}
  .feat b {{ font-weight:600; }}
  .feat i {{ font-style:normal; color:var(--red-soft); flex:0 0 auto; width:15px;
             text-align:center; margin-top:1px; }}
  .feat p {{ margin:1px 0 0; font-size:11.5px; color:var(--ink-dim); line-height:1.4; }}

  .status {{ margin-top:16px; padding-top:15px; border-top:1px solid rgba(255,255,255,.10); }}
  #stage {{ font-size:12.5px; color:var(--ink); min-height:1.5em; }}
  #detail {{ font-size:11.5px; color:var(--ink-dim); margin-top:5px; opacity:0;
             transition:opacity .4s ease; }}
  #detail.show {{ opacity:1; }}
  .bar {{ height:2px; margin-top:12px; border-radius:2px; overflow:hidden;
          background:rgba(255,255,255,.10); }}
  .bar i {{ display:block; width:38%; height:100%; border-radius:2px;
            background:linear-gradient(90deg, var(--red-soft), var(--red));
            animation:slide 1.5s ease-in-out infinite; }}
  @keyframes slide {{ 0% {{ transform:translateX(-105%) }} 100% {{ transform:translateX(265%) }} }}

  .foot {{ font-size:11px; color:var(--ink-dim); opacity:.8; }}

  /* The failure state is the splash's most important job: on a launch that
     never reaches a window, this is the only surface that can speak. */
  body.failed .bar, body.failed .feat, body.failed .card h2 {{ display:none; }}
  body.failed #stage {{ color: var(--red-soft); font-size:13px; }}
</style>
{_GRID_SVG}
<div class="wrap">
  <div class="top">
    <div class="logo">P</div>
    <div class="word"><span>PyPSA</span> Studio</div>
  </div>

  <div class="body">
    <div class="hero">
      <div class="eyebrow"><span class="dot"></span>Energy system planning</div>
      <h1>Plan the grid behind<br><em>tomorrow's cities.</em></h1>
      <p class="lede">Model demand, size generation and storage, and compare
      scenarios side by side — all on this machine.</p>
    </div>

    <div class="card">
      <h2>What's inside</h2>
      <div class="feat"><i>&#9672;</i><div><b>Build networks</b>
        <p>Buses, lines, generators, storage — on a map or a canvas.</p></div></div>
      <div class="feat"><i>&#9650;</i><div><b>Optimise</b>
        <p>Capacity expansion and dispatch, solved with HiGHS.</p></div></div>
      <div class="feat"><i>&#9681;</i><div><b>Compare scenarios</b>
        <p>Branch a project, change assumptions, read the difference.</p></div></div>
      <div class="feat"><i>&#10022;</i><div><b>Your files, local</b>
        <p>Plain folders you can open in Finder.</p></div></div>
      <div class="status">
        <div id="stage">Starting&#8230;</div>
        <div class="bar"><i></i></div>
        <div id="detail"></div>
      </div>
    </div>
  </div>

  <div class="foot">Built on PyPSA &#183; Powered by open optimisation</div>
</div>
<script>
  window.__stage = function (text) {{
    document.getElementById('stage').textContent = text;
  }};
  // Shown only if a stage outlasts its expected budget, so the user learns
  // that a long first-run import is working rather than stuck.
  window.__detail = function (text) {{
    var el = document.getElementById('detail');
    el.textContent = text;
    el.classList.toggle('show', !!text);
  }};
  window.__failed = function (text) {{
    document.body.classList.add('failed');
    document.getElementById('stage').textContent = text;
    window.__detail('');
  }};
</script>
"""


def set_stage_js(text: str) -> str:
    """`evaluate_js` payload for a stage change. JSON-quoted, not f-string."""
    return f"window.__stage({json.dumps(text)})"


def set_detail_js(text: str) -> str:
    return f"window.__detail({json.dumps(text)})"


def failed_js(text: str) -> str:
    return f"window.__failed({json.dumps(text)})"


def message_html(title: str, body: str) -> str:
    """
    A standalone message window — "already running", "could not start".

    A FUNCTION, not string surgery on `HTML`. `gui.py` used to build these with
    `HTML.split("<script>")[0]` plus three `.replace()` calls against exact
    markup: a coupling with no test and no failure mode. Change a tag in the
    splash and the replacements simply stop matching, so the user gets the
    splash's own text instead of the message. Nothing raises, nothing logs.

    Escaped, because the lock-failure path puts a filesystem path in here and
    app-data directories are user-named.
    """
    return f"""<!doctype html>
<meta charset="utf-8">
<title>PyPSA Studio</title>
<style>
{_STYLE}
  .wrap {{ justify-content:center; padding:32px 36px; }}
  .top {{ display:flex; align-items:center; gap:10px; margin-bottom:18px; }}
  .logo {{
    width:26px; height:26px; border-radius:8px; display:grid; place-items:center;
    font-weight:900; font-size:12px; color:#14090a;
    background: linear-gradient(140deg, var(--red-soft), var(--red) 55%, var(--red-deep));
  }}
  .word {{ font-size:13px; font-weight:700; }}
  .word span {{ color: var(--red); }}
  h1 {{ font-size:19px; line-height:1.25; letter-spacing:-.02em; margin:0 0 10px; }}
  p {{ margin:0; font-size:13px; color:var(--ink-dim); max-width:46ch; }}
</style>
{_GRID_SVG}
<div class="wrap">
  <div class="top">
    <div class="logo">P</div>
    <div class="word"><span>PyPSA</span> Studio</div>
  </div>
  <h1>{_html.escape(title)}</h1>
  <p>{_html.escape(body)}</p>
</div>
"""
