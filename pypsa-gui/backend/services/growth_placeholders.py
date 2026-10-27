"""Setting a growth placeholder (E1 follow-up ``E1-set-growth-placeholders``).

A staged example built for entered planning years marks each growth value it
has no figure for as a placeholder: a ``growth_inputs`` row in the project's
``eh_template.json`` with ``set`` false, and an assumed ledger row
``placeholder <kind> <year>``. A user edit of the series that row names, in
that row's period, sets it: ``set`` true, ``source`` "entered", the value in
the row's unit, and the ledger row entered. The peak check, the scorecard, the
investment case and the PPA flags then read the period as set. An edit back to
the placeholder value sets it too (the user confirmed it); an acknowledgement
is not an edit and never sets one.

The five user writers of ``_user_ts`` (the time-series PUT and upload, the
profile upload, ``profile_gen.apply.assign_profile`` and the chat load-profile
tool) call ``before`` ahead of their write and ``mark_set`` after it, under the
PyPSA lock, in the same request (gatekeeper B plan verdict, conditions C1-C5).
Internal copies (``_backup_network_ts_to_user_ts``, the flat rebase, the rename,
the stress and DTC copies) are not edits and do not call it. Undo restores the
file with the series (``snapshot_meta`` / ``restore_meta``, C3).
"""
from __future__ import annotations

import json
import logging
import pathlib

import pandas as pd

_log = logging.getLogger(__name__)

#: The template metadata file (``project_templates.eh_templates.META_FILE``).
META_FILE = "eh_template.json"

#: ``kind`` → (component list, entity, field) of the series a growth row names
#: (``eh_templates._GROWTH_KINDS``; a test pins the two equal). ``load_scale``
#: names every load in its value.
KINDS = {
    "it_mw": ("loads", "it_load", "p_set"),
    "energized_mw": ("links", "grid_import", "eh_energization_mw"),
    "h2_offtake_mw": ("loads", "h2_offtake", "p_set"),
    "load_scale": ("loads", None, "p_set"),
    "nh3_delivery_tpd": ("loads", "nh3_delivery", "p_set"),
}

#: MWh_NH3 per t (LHV; ``eh_ammonia.MWH_PER_T``, a test pins the two equal).
MWH_PER_T = 5.166


def _value_of(kind: str, peak: float, old_peak: float | None, old_value: float) -> float | None:
    """The row's value from the edited period's peak. The energization
    schedule is MW itself; the NH3 delivery is MWh_NH3/h → t/d (the build's
    t/d × 5.166/24, inverted); a load's MW or scale moves with its peak, since
    the series is the example's shape times the value (None when the old peak
    is 0: no ratio)."""
    if kind == "energized_mw":
        return peak
    if kind == "nh3_delivery_tpd":
        return peak * 24.0 / MWH_PER_T
    if not old_peak or old_peak <= 0:
        return None
    return old_value * peak / old_peak


def _meta_path(project_dir=None) -> pathlib.Path | None:
    """``project_dir``'s ``eh_template.json`` (default: the active project's)."""
    if project_dir is None:
        from services.network_lengths import active_project_dir
        project_dir = active_project_dir()
    return pathlib.Path(project_dir) / META_FILE if project_dir is not None else None


def _read(path: pathlib.Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _open_rows(meta: dict | None, component: str, attribute: str) -> list[dict]:
    """The rows an edit of ``component``/``attribute`` may set: placeholders,
    and values already entered (a later edit moves them). The example's own
    values (``source`` "example") are not growth placeholders."""
    out = []
    for g in (meta or {}).get("growth_inputs") or []:
        if not isinstance(g, dict) or g.get("kind") not in KINDS:
            continue
        comp, _entity, field = KINDS[g["kind"]]
        if (comp, field) == (component, attribute) and g.get("source") != "example":
            out.append(g)
    return out


def _covers(row: dict, col: str) -> bool:
    entity = KINDS[row["kind"]][1]
    if entity is None:
        return isinstance(row.get("value"), dict) and col in row["value"]
    return col == entity


def _peaks(series: pd.Series) -> dict:
    """``{period: peak}`` of a series: per level-0 period on a MultiIndex,
    ``{None: peak}`` on a flat one."""
    s = pd.to_numeric(series, errors="coerce")
    if isinstance(s.index, pd.MultiIndex):
        return {int(p): float(v) for p, v in s.groupby(level=0).max().items()}
    return {None: float(s.max())} if len(s) else {}


def _current(n, component: str, attribute: str, col: str) -> pd.Series | None:
    df = getattr(getattr(n, f"{component}_t", None), "get", lambda *_: None)(attribute)
    if isinstance(df, pd.DataFrame) and col in df.columns:
        return df[col]
    static = getattr(n, component, None)
    if isinstance(static, pd.DataFrame) and col in static.index and attribute in static.columns:
        return pd.Series(float(static.at[col, attribute]), index=n.snapshots)
    return None


def before(n, component: str, attribute: str, columns, *, project_dir=None) -> dict | None:
    """The network's peaks per period of the columns an edit is about to
    write, ``{col: {period: peak}}``; None when the project has no growth row
    the edit could set (the common case: nothing more to do)."""
    rows = _open_rows(_read(_meta_path(project_dir)), component, attribute)
    cols = [str(c) for c in columns if any(_covers(r, str(c)) for r in rows)]
    if not cols:
        return None
    out = {}
    for col in cols:
        s = _current(n, component, attribute, col)
        out[col] = _peaks(s) if s is not None else {}
    return out


def _period_peaks(n, series: pd.Series) -> dict:
    """``{period: peak}`` the written series puts in each period it covers
    (gatekeeper C5): its own level-0 periods on a MultiIndex; every period
    when a flat series is broadcast under a multi-period axis; ``{None: …}``
    on a flat axis (the network's single year)."""
    peaks = _peaks(series)
    if None in peaks and isinstance(n.snapshots, pd.MultiIndex):
        return {int(p): peaks[None] for p in n.investment_periods}
    return peaks


def _ledger_row(meta: dict, kind: str, year) -> dict | None:
    item = f"placeholder {kind} {year}"
    for e in meta.get("ledger") or []:
        if isinstance(e, dict) and e.get("item") == item:
            return e
    return None


def _value_text(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} ×{v:g}" for k, v in value.items())
    return f"{value:g}"


def _enter(meta: dict, row: dict, attribute: str) -> None:
    """The row's ledger entry once the row is set: entered, not assumed."""
    e = _ledger_row(meta, row["kind"], row["year"])
    if e is None:
        return
    e["value"] = row["value"]
    e["label"] = str(e.get("label", "")).replace(" (placeholder)", "")
    e["source"] = "entered"
    e["note"] = (f"entered: an edit of {attribute} for {row['year']} sets it (the example's "
                 f"same-stage value was {_value_text(row['example_value'])})")


def mark_set(n, component: str, attribute: str, written: dict, prior: dict | None, *,
             project_dir=None) -> list[str]:
    """Set the growth rows an edit wrote: ``written`` is ``{column: the series
    written}``, ``prior`` what ``before`` returned for it. Writes the
    project's ``eh_template.json`` atomically when a row changed (the caller
    holds the PyPSA lock, C4). Returns ``<kind>_<year>`` of each row now set.
    ``load_scale`` sets a period only once every load it names is written
    (Q1 a); until then the loads written are kept on the row."""
    if not prior:
        return []
    path = _meta_path(project_dir)
    meta = _read(path)
    rows = _open_rows(meta, component, attribute)
    if not rows:
        return []
    flat_years = {r["year"] for r in rows}
    changed, now_set = False, []
    for col, series in written.items():
        col = str(col)
        if col not in prior or series is None:
            continue
        new = _period_peaks(n, series)
        for row in (r for r in rows if _covers(r, col)):
            year = row["year"]
            if year in new:
                key = year
            elif None in new and len(flat_years) == 1:
                key = None                     # a flat axis: its single year
            else:
                continue                       # another period's write (C5)
            old_peak = prior[col].get(key, prior[col].get(None))
            scale = isinstance(row["value"], dict)
            old_value = float(row["value"][col] if scale else row["value"])
            value = _value_of(row["kind"], new[key], old_peak, old_value)
            if value is None:
                continue
            value = round(float(value), 6)
            if scale:
                row["value"][col] = value
                entered = sorted({*row.get("entered_loads", []), col})
                row["entered_loads"] = entered
                done = set(entered) >= set(row["value"])
            else:
                row["value"] = value
                done = True
            changed = True
            if done:
                was = row.get("set")
                row["set"], row["source"] = True, "entered"
                _enter(meta, row, attribute)
                if not was:
                    now_set.append(f"{row['kind']}_{year}")
            else:
                e = _ledger_row(meta, row["kind"], year)
                if e is not None:
                    e["value"] = row["value"]
    if changed:
        _write(path, meta)
    return now_set


def _write(path: pathlib.Path, meta: dict) -> None:
    from services.atomic_io import atomic_write_text
    try:
        atomic_write_text(path, json.dumps(meta, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        # The series edit has landed; the row stays a placeholder (the safe
        # side: the period reads unknown) and the failure is logged.
        _log.warning("growth placeholder not recorded in %s: %s", path, exc)


def snapshot_meta() -> bytes | None:
    """The active project's ``eh_template.json`` bytes for the undo stack
    (None when it has none)."""
    path = _meta_path()
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def restore_meta(data: bytes | None) -> None:
    """Undo (C3): put back the ``eh_template.json`` the undone edit started
    from, so an undone edit does not stay entered."""
    path = _meta_path()
    if data is None or path is None:
        return
    try:
        if path.is_file() and path.read_bytes() == data:
            return
        from services.atomic_io import atomic_write_bytes
        atomic_write_bytes(path, data)
    except OSError as exc:
        _log.warning("undo: %s not restored: %s", path, exc)
