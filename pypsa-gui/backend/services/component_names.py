"""
What a NEW component name may contain (OPEN-ITEMS item 8).

A component name travels from a request body into the network, then into
download filenames, spreadsheets, log lines and model-facing text. Nothing
checked it, and two defects followed, each fixed at its sink: a name could
close the untrusted-data fence (#18), and a name broke a `Content-Disposition`
header (#7). This is the root-cause rule, applied where a name ENTERS: every
interactive create and rename, a carrier created implicitly by a `carrier`
field, and every new import.

It is deliberately narrow. Real networks name things `H2 pipeline A -> B`,
`way/123456:a-220`, `merged_17+3`, `gas@2030` and `Grüße`, so spaces, `/`,
`:`, `<`, `>`, `+`, `@`, `#` and every letter stay legal. What is refused is
what no real network needs and what defeats a sink or hides from a reader:

  * control characters, Unicode category Cc. This covers C0 including tab
    and newline, DEL and C1. A newline forges a line in a log or a prompt; a
    control byte makes an HTTP header unsendable.
  * format characters, Cf: bidi overrides and isolates, zero-width spaces,
    the BOM. `rlo\\u202eexe.gnp` displays as `rlognp.exe`.
  * line and paragraph separators, Zl and Zp: newlines by another name.
  * lone surrogates, Cs. They cannot be encoded as UTF-8, so the netCDF
    save fails.
  * empty or whitespace-only names, and names over `MAX_LEN` characters.

Opening an EXISTING project does not apply this rule (owner decision,
2026-10-12): a network saved before the rule existed must stay openable. That
is why the sinks keep their own guards.
"""
from __future__ import annotations

import unicodedata

from fastapi import HTTPException

# Generous: the longest PyPSA-Eur names are well under 100 characters. A cap
# exists so a name cannot be used to smuggle a document into a prompt.
MAX_LEN = 256

_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})


def name_problem(name) -> str | None:
    """Why `name` may not be given to a new component, or None if it may."""
    if not isinstance(name, str):
        return "a name must be a string"
    if not name.strip():
        return "a name cannot be empty"
    if len(name) > MAX_LEN:
        return f"a name may be at most {MAX_LEN} characters (got {len(name)})"
    for ch in name:
        if unicodedata.category(ch) in _REFUSED_CATEGORIES:
            return (f"a name may not contain control, format or line-separator "
                    f"characters (found U+{ord(ch):04X})")
    return None


def require_valid_name(name, *, component_class: str) -> None:
    """Raise 422 before anything is mutated if `name` is not a valid new name."""
    problem = name_problem(name)
    if problem is not None:
        # `ascii()`, not the raw name: the message must not carry the very
        # characters it refuses into a log line or a prompt.
        raise HTTPException(422, {
            "error_kind": "invalid_component_name",
            "message": f"{component_class} name {ascii(name)[:80]} refused: {problem}",
        })


# Every PyPSA component table whose index is a set of names. Read from PyPSA
# rather than listed, so a component type added upstream is covered too.
def _name_indexes(n):
    for c in n.iterate_components():
        yield c.name, c.static.index if hasattr(c, "static") else c.df.index


def invalid_names_in_network(n) -> list[str]:
    """`Class 'name'` for every component in `n` whose name the rule refuses."""
    bad: list[str] = []
    for component_class, index in _name_indexes(n):
        bad.extend(f"{component_class} {ascii(str(name))[:80]}"
                   for name in index if name_problem(str(name)) is not None)
    return bad


def invalid_names_in_netcdf(path) -> list[str]:
    """The same check on a `network.nc` read WITHOUT loading it, so a bundle
    can be refused before it replaces the live network."""
    import xarray as xr

    try:
        with xr.open_dataset(path) as ds:
            bad: list[str] = []
            for var in ds.variables:
                # PyPSA stores each component's names as a `<list_name>_i`
                # coordinate (`buses_i`, `generators_i`, ...).
                if not str(var).endswith("_i") or str(var) == "snapshots":
                    continue
                bad.extend(f"{var[:-2]} {ascii(str(v))[:80]}"
                           for v in ds[var].values if name_problem(str(v)) is not None)
            return bad
    except Exception:  # noqa: BLE001 — an unreadable file fails in the loader itself
        return []


def refuse_invalid_names(bad: list[str]) -> None:
    if bad:
        raise HTTPException(422, {
            "error_kind": "invalid_component_name",
            "message": (f"{len(bad)} component name(s) refused (control, format or "
                        f"line-separator characters, empty, or over {MAX_LEN} "
                        f"characters): {', '.join(bad[:5])}"),
        })
