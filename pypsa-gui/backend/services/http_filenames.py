"""
Build a `Content-Disposition` header that survives a hostile filename.

Why this exists
---------------
Four download routes interpolated a caller-controlled name straight into the
header:

    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'}

where `name` is a project name or a component name — both of which the API
accepts with quotes, control characters and path separators in them. Two
distinct things went wrong, and neither is response splitting (verified against
a real uvicorn, not assumed):

* **A quote closes the quoted-string early.** uvicorn passes
  `filename="ev"il.zip"` through verbatim, and a conforming parser reads the
  filename as `ev`.
* **A control character kills the response.** uvicorn raises
  `RuntimeError: Invalid HTTP header value.` while sending, and the client gets
  an empty reply — the download is simply broken, with a 500 in the log and
  nothing useful at the browser.

So this is a correctness bug in the download routes rather than a hole, and the
fix belongs at the header, not at name validation. A route must not depend on
what names the product happens to allow today: `content_disposition()` produces
a valid header for ANY input string.

What it emits
-------------
RFC 6266, which is what browsers implement:

    attachment; filename="<ascii-safe>"; filename*=UTF-8''<percent-encoded>

The `filename*` parameter carries the real name and wins in every browser since
IE9; the quoted `filename` is the fallback for anything that does not understand
it. Both are always safe to place in a header.

**When the name is already clean ASCII, the output is byte-identical to the old
f-string** — no `filename*`, same quoting. That is deliberate: the vast majority
of downloads are named `load_profiles_template.xlsx` and their headers should
not churn just because this helper exists.
"""
from __future__ import annotations

import unicodedata
import urllib.parse

# RFC 5987 attr-char: what may appear un-encoded in `filename*`. Everything
# else is percent-encoded.
_ATTR_CHAR_SAFE = "!#$&+-.^_`|~"


def _ascii_fallback(filename: str) -> str:
    """
    The `filename="..."` value: ASCII, no control characters, no quote or
    backslash, no path separators.

    Non-ASCII is TRANSLITERATED, not dropped. Dropping is what the first cut
    did, and it turns `naïve.zip` into `nave.zip` and `café_report.csv` into
    `caf_report.csv` — names a European user would reasonably call wrong.
    NFKD decomposition first gives `naive.zip` and `cafe_report.csv`, which is
    the same approach `services/filename_service.py::safe_upload_filename`
    already takes for upload names.

    Known limitation: a stem with NO Latin base characters at all still scrubs
    to nothing, so `日本語.xlsx` falls back to `xlsx`. `filename*` carries the
    real name and every browser since IE9 prefers it, so this only shows up on
    a client that ignores RFC 5987 entirely.

    A name that scrubs away completely becomes `download`, because
    `filename=""` is worse than a generic name.
    """
    # NFKD splits an accented letter into base + combining mark; the ASCII
    # encode then keeps the base and drops the mark.
    filename = unicodedata.normalize("NFKD", filename)
    out = []
    for ch in filename:
        if ch in '"\\/':
            out.append("_")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            # Control characters are the ones uvicorn refuses to send.
            out.append("_")
        elif ord(ch) > 0x7E:
            continue  # non-ASCII: `filename*` has it
        else:
            out.append(ch)
    cleaned = "".join(out).strip()
    # A leading dot makes the download a hidden file on unix; a name that is
    # only dots is not a name at all.
    cleaned = cleaned.lstrip(".")
    return cleaned or "download"


def content_disposition(filename: str, *, disposition: str = "attachment") -> str:
    """
    A complete `Content-Disposition` header VALUE for `filename`.

        >>> content_disposition("report.xlsx")
        'attachment; filename="report.xlsx"'
        >>> content_disposition('ev"il.zip')
        'attachment; filename="ev_il.zip"; filename*=UTF-8\\'\\'ev%22il.zip'

    `disposition` is `attachment` (download) or `inline` (preview in-tab).
    """
    fallback = _ascii_fallback(filename)
    header = f'{disposition}; filename="{fallback}"'

    # Only add `filename*` when it says something the fallback does not. This
    # keeps every ordinary download's header exactly as it was.
    if filename != fallback:
        encoded = urllib.parse.quote(filename, safe=_ATTR_CHAR_SAFE, encoding="utf-8")
        header += f"; filename*=UTF-8''{encoded}"
    return header
