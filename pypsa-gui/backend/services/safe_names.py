"""
Display name -> directory name, portably (phase 1b, E1).

Until this phase a project directory was a UUID and none of this mattered. E1
puts the user's own name on disk in a place they open in Finder/Explorer, and
D1 targets Windows and macOS, whose rules disagree:

  Windows  rejects ``<>:"/\\|?*`` and control characters; reserves ``CON``,
           ``PRN``, ``AUX``, ``NUL``, ``COM1-9`` and ``LPT1-9`` **including
           with an extension** (``CON.nc`` is still the console device);
           silently strips trailing dots and spaces from a path component; and
           defaults to a 260-character path limit.
  macOS    is case-insensitive by default, so ``Belgium Grid`` and
           ``belgium grid`` are one directory.
  POSIX    hides a leading dot. A legacy ``.foo`` would import invisible;
           ``routers/projects.py:188`` already rejects those on the way in.

Two functions, deliberately separate. ``safe_dir_name`` is a pure string
transform with no knowledge of the filesystem; ``unique_dir_name`` resolves
collisions against a caller-supplied set. Callers assemble that set from the
database **union** the directory listing — see ``storage_paths._taken_names``.

``_MAX_LEN`` caps the DIRECTORY. Truncating ``Project.name`` is a separate
concern in a separate place: the column is ``String(64)`` but SQLite does not
enforce it, so the row is truncated defensively by the importer.
"""
from __future__ import annotations

import itertools
import unicodedata
from typing import Iterable

# Windows' forbidden set. `/` and `\` are in here as separators, not just as
# illegal characters: a name carrying either would otherwise create a nested
# directory outside the intended parent.
_FORBIDDEN = set('<>:"/\\|?*')

_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# 96, not 255. The cap that matters is Windows' 260-character *path*, and the
# directory sits under `~/Documents/PyPSA GUI/Projects/` with a `snapshots/`
# subtree and file names below it. A per-component cap this side of the limit
# leaves room for all of that.
_MAX_LEN = 96

# What an empty result becomes. Never "", which would resolve to the parent.
_FALLBACK = "project"


def safe_dir_name(name: str) -> str:
    """
    A single path component that is legal on Windows, macOS and Linux.

    Non-ASCII is preserved: all three platforms store UTF-8 names, and folding
    to ASCII would make ``Étude`` and ``Etude`` collide for no reason.
    """
    text = str(name).strip()
    text = "".join("_" if (c in _FORBIDDEN or ord(c) < 32) else c for c in text)

    # Leading dots hide the directory on POSIX; trailing dots and spaces are
    # stripped by Windows itself, so `Study.` and `Study` would silently become
    # one directory.
    text = text.strip(" .")
    if not text:
        return _FALLBACK

    # Defuse BEFORE truncating: a reserved stem is at most 4 characters, so the
    # added underscore always survives the cut. Doing it after would let
    # `CON.<200 chars>` come back over the cap.
    stem, dot, rest = text.partition(".")
    if stem.upper() in _RESERVED:
        text = f"{stem}_{dot}{rest}"

    if len(text) > _MAX_LEN:
        # Re-strip: cutting mid-name can expose a trailing dot or space, which
        # Windows would then remove — reintroducing the collision the cap is
        # meant to be orthogonal to.
        text = text[:_MAX_LEN].rstrip(" .")

    return text or _FALLBACK


def fold(name: str) -> str:
    """
    The key two directory names collide under.

    **NFC first, then casefold** — and both halves are load-bearing:

      * ``casefold`` rather than ``lower``: macOS and Windows are both
        case-insensitive by default, and ``lower`` gets ``ß``/``ss`` wrong.
      * ``NFC``: APFS and NTFS are normalisation-INSENSITIVE but
        form-PRESERVING. ``Café`` typed in a browser (NFC) and ``Café`` from a
        Finder-era directory name (NFD) are *the same directory on disk* while
        being different Python strings, different SQLite TEXT values, and
        therefore invisible both to this allocator and to
        ``UniqueConstraint("org_id", "storage_path")``. Without this, importing
        an NFD-named project and then creating an NFC-named one hands them one
        directory and the first save overwrites the other project.
    """
    return unicodedata.normalize("NFC", str(name)).casefold()


def unique_dir_name(name: str, taken: Iterable[str]) -> str:
    """
    ``safe_dir_name`` plus a ``  (2)``-style suffix until the name is free.

    ``taken`` is annotated ``Iterable[str]`` and iterated exactly once, so a
    one-shot generator works. A bare ``Container`` would not: the body needs to
    build the folded set described in ``fold``.
    """
    base = safe_dir_name(name)
    folded = {fold(entry) for entry in taken}
    if fold(base) not in folded:
        return base

    for n in itertools.count(2):
        suffix = f" ({n})"
        stem = base[: _MAX_LEN - len(suffix)].rstrip(" .") or _FALLBACK
        candidate = f"{stem}{suffix}"
        if fold(candidate) not in folded:
            return candidate

    raise AssertionError("unreachable: itertools.count is infinite")
