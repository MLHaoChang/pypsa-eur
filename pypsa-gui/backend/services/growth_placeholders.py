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
rows with the series (``snapshot_meta`` / ``restore_meta``, C3), and only the
rows: other writes to the file survive it (UM10-UNDO-1).
"""
from __future__ import annotations

import json
import logging
import pathlib

import pandas as pd

_log = logging.getLogger(__name__)

#: The template metadata file (``services.template_marker.MARKER_FILE``).
from services.template_marker import MARKER_FILE as META_FILE  # noqa: E402, F401 - the name callers use

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


def _dir_or_active(project_dir=None):
    if project_dir is None:
        from services.network_lengths import active_project_dir
        project_dir = active_project_dir()
    return project_dir


def _meta_path(project_dir=None) -> pathlib.Path | None:
    """The ``eh_template.json`` ``project_dir`` reads (default: the active
    project's): its own, or its base's for a sensitivity case (UM07-KIND-1)."""
    from services.template_marker import source_path
    project_dir = _dir_or_active(project_dir)
    return source_path(project_dir) if project_dir is not None else None


def _own_meta_path(project_dir=None) -> pathlib.Path | None:
    """Where an edit's write goes: the project's own ``eh_template.json``, so
    an edit on a case writes the case's copy and never its base's."""
    from services.template_marker import own_path
    project_dir = _dir_or_active(project_dir)
    return own_path(project_dir) if project_dir is not None else None


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
    from services import template_marker as tm
    own = _own_meta_path(project_dir)
    if own is None:
        return []
    try:
        with tm.write_lock(own.parent):
            return _mark_set(n, component, attribute, written, prior, project_dir)
    except (OSError, ValueError) as exc:
        # The lock is busy or cannot be taken (TimeoutError is an OSError;
        # file_lock refuses a lock outside its folder with ValueError). The
        # series edit has landed; the row stays a placeholder (the safe
        # side), as when the write fails.
        _log.warning("growth placeholder not recorded in %s: %s", own, exc)
        return []


def _mark_set(n, component, attribute, written, prior, project_dir) -> list[str]:
    meta = _read(_meta_path(project_dir))
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
        _write(_own_meta_path(project_dir), meta)
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
    """The active project's own ``eh_template.json`` bytes for the undo stack
    (None when it has none: a sensitivity case reads its base's)."""
    from services.template_marker import is_case
    path = _own_meta_path()
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        if is_case(path.parent):
            # None would read "no copy" and an undo would remove it: the
            # caller skips this snapshot instead.
            raise
        return None


def _parse(data: bytes):
    try:
        meta = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _is_placeholder_ledger(e) -> bool:
    return isinstance(e, dict) and str(e.get("item", "")).startswith("placeholder ")


def _ledger_of(meta: dict) -> list | None:
    """The marker's ledger list ([] when it has none); None when it is not a
    list (a marker the undo cannot merge into: fail closed)."""
    ledger = meta.get("ledger", [])
    return ledger if isinstance(ledger, list) else None


def _with_growth_of(current: dict, old: dict) -> dict | None:
    """``current`` with the keys an edit writes (``mark_set``) as ``old`` has
    them (UM10-UNDO-1): ``growth_inputs``, and the ``placeholder <kind>
    <year>`` ledger rows, matched both ways (GK B K1): a placeholder row not
    in ``old`` goes, one only in ``old`` comes back, and every other ledger
    row stays as it is now, in its order. Every other key stays as it is
    now. None when a ledger is not a list."""
    now, then = _ledger_of(current), _ledger_of(old)
    if now is None or then is None:
        return None
    out = dict(current)
    if "growth_inputs" in old:
        out["growth_inputs"] = old["growth_inputs"]
    else:
        out.pop("growth_inputs", None)
    left = [e for e in then if _is_placeholder_ledger(e)]
    ledger = []
    for e in now:
        if not _is_placeholder_ledger(e):
            ledger.append(e)
            continue
        match = next((k for k, o in enumerate(left) if o["item"] == e["item"]), None)
        if match is not None:
            ledger.append(left.pop(match))
    ledger.extend(left)
    if ledger or "ledger" in current:
        out["ledger"] = ledger
    return out


#: ``undo_rows`` when the rows cannot be read: compares unequal to anything.
class _NeverEqual:
    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    __hash__ = None

    def __repr__(self):
        return "UNREADABLE_ROWS"


UNREADABLE_ROWS = _NeverEqual()


def _rows_of(meta: dict):
    ledger = _ledger_of(meta)
    if ledger is None:
        return UNREADABLE_ROWS
    placeholders = sorted((e for e in ledger if _is_placeholder_ledger(e)),
                          key=lambda e: json.dumps(e, sort_keys=True))
    return {"growth_inputs": meta.get("growth_inputs"), "placeholder_ledger": placeholders}


def _old_marker(data: bytes | None, project_dir):
    """The marker the undone edit started from: ``data`` parsed; for None,
    the base's marker on a sensitivity case (it had no copy of its own) or
    {} for a project with no marker. UNREADABLE_ROWS when it cannot be read."""
    from services import template_marker as tm
    if data is not None:
        old = _parse(data)
        return old if old is not None else UNREADABLE_ROWS
    if project_dir is None or not tm.is_case(project_dir):
        return {}
    base = tm.resolve_base(project_dir)
    if base.state == tm.UNREADABLE:
        return UNREADABLE_ROWS
    if base.path is None:
        return {}
    old = _read(base.path)
    return old if old is not None else UNREADABLE_ROWS


def undo_rows(data: bytes | None, project_dir=None):
    """The keys an undo restores (``growth_inputs`` and the placeholder
    ledger rows) of the marker ``data`` (a ``snapshot_meta`` value; None:
    what the project reads without a copy of its own), for the undo
    read-back (GK B K3); the rest of the marker is not compared.
    UNREADABLE_ROWS when they cannot be read."""
    old = _old_marker(data, _dir_or_active(project_dir))
    return old if old is UNREADABLE_ROWS else _rows_of(old)


def restore_meta(data: bytes | None) -> str | None:
    """Undo (C3): put back the growth rows the undone edit started from, so an
    undone edit does not stay entered. Only the keys an edit writes go back
    (``growth_inputs`` and the placeholder ledger rows, UM10-UNDO-1): a write
    that is not on the undo stack, such as ``dc_backup.save_backup``,
    survives. ``data`` None on a sensitivity case: the case read its base's
    marker, whose rows go back; a copy that then equals the base's is
    removed, so the case reads its base's again (UM07-KIND-1).

    Returns None when the rows are back (or there was nothing to restore),
    else why the marker was left as it is (GK B K4): ``unreadable`` (a
    marker or the base that does not parse or cannot be read: no guessed
    restore), ``busy`` or ``write_failed``. A project that is not a case
    keeps its file."""
    from services import template_marker as tm
    path = _own_meta_path()
    if path is None or (data is None and not tm.is_case(path.parent)):
        return None  # no marker at the snapshot: a marker never goes
    try:
        with tm.write_lock(path.parent):
            if not path.is_file():
                return None
            current = _parse(path.read_bytes())
            old = _old_marker(data, path.parent)
            if current is None or old is UNREADABLE_ROWS:
                _log.warning("undo: %s not restored: a marker cannot be read", path)
                return "unreadable"
            merged = _with_growth_of(current, old)
            if merged is None:
                _log.warning("undo: %s not restored: a ledger is not a list", path)
                return "unreadable"
            if data is None and merged == old:
                path.unlink()
                return None
            if merged == current:
                return None
            from services.atomic_io import atomic_write_text
            atomic_write_text(path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
            return None
    except TimeoutError as exc:
        _log.warning("undo: %s not restored: %s", path, exc)
        return "busy"
    except (OSError, ValueError) as exc:
        # ValueError: file_lock refuses a lock outside its folder.
        _log.warning("undo: %s not restored: %s", path, exc)
        return "write_failed"
