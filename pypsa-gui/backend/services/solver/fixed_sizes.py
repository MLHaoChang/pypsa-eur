"""
The fixed-size run (slice F1; modular-cards §4, Capacity › sizing off).

``SolverConfig.fixed_sizes`` turns a capacity run into dispatch on fixed
sizes. Flipping ``*_nom_extendable`` off is not the way: preflight rejects a
never-built candidate at zero capacity. The run instead reuses
``adequacy.sweep.freeze_capacities``: extendability stays on and each size is
clamped to ``min = size, max = size + 1e-6``.

The size comes from one of two sources:

* ``"capacity_run"``: the last sizing run's ``*_nom_opt``. Every successful
  sizing solve (``fixed_sizes == "off"``, not an operational sweep solve)
  writes ``n.meta["sizing_record"]``: ``{revision, solved_at, sha256, sizes,
  topology}`` (``revision`` and ``sha256`` are provenance; the check reads the
  vector and the topology hash).
  ``n.meta`` travels with the network through netCDF and copies. The record
  is replaced only by the next sizing solve. "capacity_run" is refused
  without a record (``fixed_sizes_no_capacity_run``), after a topology edit
  (the record's topology hash, its component set or ``dispatch_status`` no
  longer match: the record no longer counts), and
  when the network's sizes differ from it (``fixed_sizes_source_changed``):
  a nameplate run, a results load or a hand edit may have overwritten
  ``*_nom_opt``. The comparison has a 1e-4 MW tolerance, because a
  capacity-run run may itself "build" up to the freeze's +1e-6 MW.
* ``"nameplate"``: the configured ``*_nom``. Refused when every extendable size
  is 0 (``fixed_sizes_nameplate_empty``); a zero candidate is named
  (``fixed_sizes_zero_nameplate``).

Incompatible with the myopic and rolling strategies, which freeze capacity
themselves, and with the power-flow mode. N-1 (SCLOPF) and multi-period full
runs are supported (``tests/test_fixed_sizes.py``).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

MODES = ("off", "capacity_run", "nameplate")
META_RECORD = "sizing_record"
# The size tolerance of the source check, far above the freeze's +1e-6 MW
# headroom. `commercial.connection.FEE_SIZE_TOLERANCE_MW` repeats it (the
# commercial package may not import the solver); a test pins them equal.
TOLERANCE_MW = 1e-4
# Every component table whose rows make the topology the record was solved on.
_TOPOLOGY_TABLES = ("buses", "generators", "loads", "storage_units", "stores", "links",
                    "lines", "transformers")


def _extendable(n):
    # The set `sweep.freeze_capacities` pins (imported here: sweep pulls in the solver).
    from services.adequacy.sweep import _CAPACITY_ATTRS

    for attr, nom in _CAPACITY_ATTRS:
        df = getattr(n, attr, None)
        ext = f"{nom}_extendable"
        if df is None or df.empty or ext not in df.columns:
            continue
        for name in df.index[df[ext].astype(bool)]:
            yield attr, nom, str(name)


def sizes(n, column: str = "opt") -> dict[str, float | None]:
    """``{"<table>/<name>": MW}`` of every extendable asset: its ``*_nom_opt``
    (``column="opt"``) or its ``*_nom`` (``"nom"``). A missing or non-finite
    size is None (strict JSON in ``n.meta``)."""
    out: dict[str, float | None] = {}
    for attr, nom, name in _extendable(n):
        df = getattr(n, attr)
        col = f"{nom}_opt" if column == "opt" else nom
        v = float(df.at[name, col]) if col in df.columns else float("nan")
        out[f"{attr}/{name}"] = v if math.isfinite(v) else None
    return out


def _topology(n) -> str:
    """A hash of every component's name: any row added or removed changes it."""
    names = sorted(f"{t}/{i}" for t in _TOPOLOGY_TABLES
                   for i in getattr(getattr(n, t, None), "index", ()))
    return hashlib.sha256("|".join(names).encode()).hexdigest()


def _sha(vector: dict[str, float | None]) -> str:
    raw = json.dumps({k: None if v is None else round(v, 6) for k, v in vector.items()},
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def record_sizing(n, revision: int | None) -> dict:
    """Write the record of a successful sizing solve into ``n.meta``."""
    vector = sizes(n, "opt")
    rec = {"revision": revision, "solved_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "sha256": _sha(vector), "sizes": vector, "topology": _topology(n)}
    n.meta[META_RECORD] = rec
    return rec


def source_problem(n) -> tuple[str, str] | None:
    """``(code, message)`` when the "capacity_run" source is unusable, else None."""
    from services.dispatch_status import dispatch_status

    rec = n.meta.get(META_RECORD)
    current = sizes(n, "opt")
    # A topology edit drops the source: a record from another component set
    # (or one older than the topology hash) is not this network's plan.
    if (not isinstance(rec, dict) or not isinstance(rec.get("sizes"), dict)
            or rec.get("topology") != _topology(n) or set(rec["sizes"]) != set(current)
            or dispatch_status(n) != "fresh"):
        return ("fixed_sizes_no_capacity_run",
                "Sizing off on \"the capacity run\" needs a capacity run of this network: "
                "run with sizing on first (none recorded, or components were added or "
                "removed since).")

    def same(now, then) -> bool:
        return (now is not None and then is not None
                and abs(float(now) - float(then)) <= TOLERANCE_MW)

    moved = [k for k, v in current.items() if not same(v, rec["sizes"][k])]
    if moved:
        return ("fixed_sizes_source_changed",
                f"The sizes on the network are no longer the capacity run's of "
                f"{rec.get('solved_at')} ({', '.join(moved[:5])}"
                f"{' …' if len(moved) > 5 else ''}): a nameplate run or a results load "
                "replaced them. Run with sizing on again, or use the nameplate.")
    return None


def issues(n, cfg) -> list[tuple[str, str, str, str]]:
    """``(severity, code, name, message)`` for a ``fixed_sizes`` request."""
    mode = getattr(cfg, "fixed_sizes", "off")
    if mode == "off":
        return []
    if mode not in MODES:
        return [("error", "fixed_sizes_unknown", "",
                 f"Sizing source {mode!r} is not one of {', '.join(MODES)}.")]
    out = []
    strategy = getattr(cfg, "solve_strategy", "full")
    if getattr(cfg, "mode", "lopf") != "lopf":
        out.append(("error", "fixed_sizes_pf_mode", "",
                    "Sizing off is a dispatch run: it needs the LOPF mode, not power flow."))
    if strategy == "myopic":
        out.append(("error", "fixed_sizes_with_myopic", "",
                    "Sizing off cannot run with the myopic strategy, which freezes "
                    "capacity per period itself."))
    if strategy == "rolling":
        out.append(("error", "fixed_sizes_with_rolling", "",
                    "Sizing off cannot run with the rolling strategy, which decouples "
                    "windows and freezes capacity itself."))
    if mode == "capacity_run":
        problem = source_problem(n)
        if problem is not None:
            out.append(("error", problem[0], "", problem[1]))
    else:
        plate = sizes(n, "nom")
        if plate and not any(v is not None and v > 0 for v in plate.values()):
            out.append(("error", "fixed_sizes_nameplate_empty", "",
                        "Sizing off on the nameplate, but every extendable asset has a "
                        "nameplate of 0: there is nothing to dispatch."))
        for key, v in plate.items():
            if not (v is not None and v > 0):
                out.append(("warning", "fixed_sizes_zero_nameplate", key.split("/", 1)[1],
                            f"{key.split('/', 1)[1]} has a nameplate of 0: with sizing off "
                            "it is not built in this run."))
    return out


def freeze_kwargs(n, mode: str) -> dict:
    """``sweep.freeze_capacities`` keywords for a fixed-size run: the recorded
    vector for "capacity_run" (so repeated runs never drift by the freeze's
    +1e-6), the configured ``*_nom`` for "nameplate"."""
    if mode == "capacity_run":
        return {"source": "opt", "sizes": (n.meta.get(META_RECORD) or {}).get("sizes")}
    return {"source": "nom", "sizes": None}


def run_label(n, cfg) -> dict:
    """``_state["sizing"]`` for this run: ``{"mode": "optimised"}`` or the
    fixed source (with the capacity run's revision and time)."""
    mode = getattr(cfg, "fixed_sizes", "off")
    if mode == "off":
        return {"mode": "optimised"}
    out = {"mode": "fixed", "source": mode}
    if mode == "capacity_run":
        rec = n.meta.get(META_RECORD) or {}
        out.update(source_revision=rec.get("revision"), source_solved_at=rec.get("solved_at"))
    return out
