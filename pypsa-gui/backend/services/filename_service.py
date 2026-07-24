"""
Safe filename derivation for chatbot uploads + agent exports (Phase A).

The on-disk PATH of an upload is the content-addressed `sha256(bytes)[:16]`
directory under `projects/<name>/uploads/`. The filename in `meta.json` is
display-only and never re-used as a path component, so the safety bar here
is "render-safe + cross-platform-portable", not "filesystem-injection-proof".

`safe_upload_filename` enforces:
  * No path-traversal sequences (`..`, `/`, `\`)
  * No control characters (`\x00`-`\x1f`)
  * No Windows reserved names (CON, PRN, NUL, AUX, COM1-9, LPT1-9 + extensions)
  * No leading dots (hidden-file-on-unix avoidance)
  * NFKD ASCII transliteration of non-ASCII characters (consistent on
    Windows + Linux + macOS without UTF-8 path concerns)
  * Capped at 200 characters (leaves headroom under the 255-byte limit
    on most filesystems even after path-component prefixes)
  * Falls back to ``export_<ts>.<ext>`` when the sanitised result is empty

The single public helper is intentionally small and side-effect-free so it
can be reused by both ``upload_service.add_upload`` (user uploads) and
``upload_service._write_agent_export`` (agent-generated files).
"""
from __future__ import annotations

import re
import time
import unicodedata

# Windows reserved device names — case-insensitive, with OR without extension.
# `con.txt` is reserved on Windows even though the dot makes it look benign.
_WIN_RESERVED = frozenset({
    "con", "prn", "nul", "aux",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})

# Filename characters considered unsafe on either Windows or POSIX. The slash
# variants are always rejected (they're path separators) and the rest cover
# Windows' stricter ruleset (colons in NTFS alternate data streams, etc.).
_UNSAFE_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')

_MAX_LEN = 200


def safe_upload_filename(name: str, ext: str = "") -> str:
    """
    Sanitise `name` for display + bundle export. NEVER used as the on-disk
    directory name — that's the file_id (sha256 hex). Returns a non-empty
    string that's safe to write into JSON, a Content-Disposition header,
    and a cross-platform zip archive.

    Args:
        name: The filename the user (or agent) supplied. May be empty.
        ext: Extension hint used when ``name`` is empty or fully scrubbed.
             Should NOT include the leading dot.

    Returns:
        A sanitised filename. Always non-empty.
    """
    raw = (name or "").strip()

    # NFKD-normalise + drop combining characters. Keeps "Müller" → "Muller"
    # instead of mojibake on a Windows zip extract.
    raw = unicodedata.normalize("NFKD", raw)
    raw = raw.encode("ascii", "ignore").decode("ascii")

    # Strip path-traversal sequences before character-by-character filtering.
    raw = raw.replace("..", "")

    # Replace any unsafe character with an underscore (NOT delete — preserves
    # the user's spacing intent).
    cleaned = _UNSAFE_CHARS_RE.sub("_", raw)

    # Trim leading dots / whitespace so dot-files don't sneak through.
    cleaned = cleaned.lstrip(". ")
    # Trim trailing dots / whitespace (Windows refuses to display these).
    cleaned = cleaned.rstrip(". ")

    if not cleaned:
        # Fall back to a timestamped synthetic name; caller-supplied ext used
        # so the agent-export path can still produce e.g. `export_*.xlsx`.
        ts = int(time.time())
        ext_clean = (ext or "").lstrip(".").lower() or "bin"
        return f"export_{ts}.{ext_clean}"

    # Cap length BEFORE the reserved-name check so a 5000-char "con.txt..."
    # input can't slip past the comparison.
    if len(cleaned) > _MAX_LEN:
        # Preserve the extension when truncating, so `.xlsx` survives.
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 8:
            keep = _MAX_LEN - len(dot) - len(suffix)
            cleaned = stem[:keep] + dot + suffix
        else:
            cleaned = cleaned[:_MAX_LEN]

    # Windows reserved-name guard. Compare the basename (before the FIRST dot),
    # case-insensitive, against the reserved set.
    base = cleaned.split(".", 1)[0].lower()
    if base in _WIN_RESERVED:
        cleaned = "_" + cleaned

    return cleaned
