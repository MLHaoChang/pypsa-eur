"""
The one pywebview setting every export in the app depends on.

**Webview-free**, like `launcher.py` and `bootstrap.py` and for the same
reason: the backend suite covers it on a headless box, and `gui.py` stays the
only module that imports a toolkit.

This file is four lines of code and a page of reasoning, because the reasoning
is the part that is hard to recover. Measured on a real cocoa WKWebView with
`smoke/audit_downloads.py` — read that, or the table in the plan, before
changing anything here.

**`ALLOW_DOWNLOADS` defaults to `False`, and `False` is not "downloads are
off".** `platforms/cocoa.py` opens its navigation-policy decision with

    if action.shouldPerformDownload() and webview_settings['ALLOW_DOWNLOADS']:
        handler(WKNavigationActionPolicyDownload)
        return

so with the setting off, a link carrying the `download` attribute is not
treated as a download at all — it falls through to ordinary navigation, and
what happens next depends on whether WebKit can render the file:

  * `text/csv`, `application/json`, and anything else renderable: **the webview
    navigates to the file and the SPA is gone.** Measured `final_url` was
    `blob:http://127.0.0.1:58297/369dd84b-...`. There is no back button and no
    address bar in this window, so the user's only move is to quit the app —
    losing whatever was not saved. Nine of the twelve blob exports are CSV.
  * `.xlsx` and other unrenderable types: `decidePolicyForNavigationResponse`
    falls to `decisionHandler(Cancel)` and nothing happens at all, with no
    error anywhere.

With it ON, all thirteen anchor-based sites work through the native save panel
with NO frontend change: correct bytes, the page survives, `revokeObjectURL`
on the same tick (which all twelve real sites do) does not truncate, and 64 MB
streams byte-exact. An earlier plan for this task specified a JS->Python bridge
that marshalled the bytes; it was unnecessary, and its own design notes warned
that a large bundle would have become "a JSON array of integers".

Windows reaches the same switch from the other side —
`platforms/edgechromium.py::on_download_starting` opens with
`if not webview_settings['ALLOW_DOWNLOADS']: args.Cancel = True`. That half was
READ, not run; `smoke/audit_downloads.py` ships so acceptance step 8 can
execute it there.

**What this widens:** with downloads allowed, any *navigation* whose response
has a MIME type WebKit cannot render raises a save panel. The SPA routes
client-side and talks to the backend over XHR — neither is a navigation — so
nothing in this app reaches that path today. Stated because it is a real
consequence, not because it is a known problem.

`window.open` is NOT fixed by this and cannot be: cocoa's
`webView_createWebViewWithConfiguration_...` acts only on
`WKNavigationTypeLinkActivated`, and a JS `window.open` is not that, so it
returns `nil` on both settings. The bundle export therefore uses an anchor —
see `OverviewPanel.tsx`.
"""
from __future__ import annotations

from typing import MutableMapping

SETTING = "ALLOW_DOWNLOADS"


def apply(settings: MutableMapping[str, object]) -> None:
    """
    Enable downloads on a pywebview settings mapping, in place.

    Takes the mapping rather than importing `webview` so this stays testable
    without a GUI toolkit. Call it before `webview.start()`.
    """
    settings[SETTING] = True
