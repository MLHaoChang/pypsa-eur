"""
The data-centre curtailable load (DC-07 S2; issue 07, GK A answers Q1-Q3, conditions C15-C21).

A data-centre project whose ``dc_load`` spec sets ``curtailment_price_eur_per_mwh`` and a
``curtailable_share`` above 0 gets a transient Generator ``__dccurt_cooling`` (carrier
``dc_curtailment``) on ``dc_mv``, the cooling load's bus. Running it is curtailing that much of
the facility: the LP pays the price for each MWh it does not serve.

- The cap (C16): ``p(t) <= min(curtailable_share * F(t), cooling(t))`` with
  ``F = it_load + cooling``, read from the built network's loads, so the PUE temperature slope
  and the AI workload are in it. The generator sits on the cooling bus and never feeds the IT
  bus.
- The duration (Q3, C19, C21): with ``curtail_max_duration_h`` = D set, every run of
  consecutive snapshots spanning D plus one snapshot of weighted hours holds
  ``sum(w * p) <= D * share * max F`` over the run. This is an energy proxy: curtailment can
  still be spread inside a run; consecutive hours are not enforced (``CLAIM_LIMIT``, written to
  ``n.meta`` and the docs). A D the horizon cannot hold is refused.
- Under the operational run (C20, C9) the rows carry in across windows: the dispatch before a
  window's start is final and enters each run that spans the start as a constant, read only
  inside ``operational.run_windows`` (``operational.current_window``). A D the first window
  cannot hold is refused; plain rolling with a duration stays refused.
- The lifecycle is the DSR tier's (``services/solver/assumptions.py``): mark transient, add,
  capture into ``captured["dc_curtailment"]``, remove. The carrier and prefix are in
  ``slack.SLACK_CARRIERS`` / ``SLACK_NAME_PREFIXES`` and never in the involuntary tier, so the
  curtailed energy is never unserved energy, never in the ENS cap, DtC or adequacy (C18).

Off (no price, or a share of 0) nothing is read or written: the network, the LP, network.nc and
the fingerprint stay as they were (C15).
"""

from __future__ import annotations

import logging
import math
import pathlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from services.adequacy.slack import DC_CURTAILMENT_CARRIER, DC_CURTAILMENT_PREFIX
from services.pypsa_service import PyPSAService
from services.solver.runtime import _safe_log

#: The template's loads (``eh_templates._apply_dc_profile``): it_load on it_bus, cooling on dc_mv.
IT_LOAD = "it_load"
COOLING_LOAD = "cooling"
COOLING_BUS = "dc_mv"
GENERATOR = f"{DC_CURTAILMENT_PREFIX}{COOLING_LOAD}"
#: ``n.meta`` key of a solved run's record.
META_KEY = "dc_curtailment"
#: ``buses_t`` frame of a solved run's curtailed MW per snapshot (column ``COOLING_BUS``),
#: committed beside the record's ``axis_hash`` the way ``settlement_inputs.commit_dsr`` does.
SERIES_ATTR = "dc_curtailment_p"
NOT_ESTABLISHED = "curtailment_not_established"
CLAIM_LIMIT = (
    "energy proxy: curtailment can still be spread; consecutive hours are not enforced; "
    "carried across operational windows"
)

NO_COOLING_LOAD = "no_cooling_load"
DURATION_EXCEEDS_HORIZON = "duration_exceeds_horizon"
#: C21's operational half: D plus one snapshot does not fit in the first window.
DURATION_EXCEEDS_WINDOW = "duration_exceeds_window"
#: C20c: the dispatch before a window's start is missing, not solved, or not this run's.
CARRY_IN_UNAVAILABLE = "carry_in_unavailable"
#: C20a: the dispatch before a window's start already exceeds a run's bound.
CARRY_IN_OVER_BOUND = "carry_in_over_bound"
#: Plain rolling (PyPSA's loop) continues past a failed window, so a failed window could become a
#: carry-in source (C20): a plain rolling run with a duration is refused. The operational run
#: (rolling on fixed sizes) carries in instead.
ROLLING_WITHOUT_CARRY_IN = "duration_under_rolling_not_supported"

_log = logging.getLogger(__name__)


class DcCurtailmentRefused(ValueError):
    """The curtailable load cannot be modelled as the spec asks; ``kind`` names why."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Terms:
    """What the spec asks of the solve."""

    price: float
    share: float
    max_duration_h: float | None


def terms_of(spec) -> Terms | None:
    """The spec's curtailment terms; None when off (no price, or a share of 0; C15)."""
    if spec is None:
        return None
    price = getattr(spec, "curtailment_price_eur_per_mwh", None)
    share = float(getattr(spec, "curtailable_share", 0.0) or 0.0)
    if price is None or share <= 0:
        return None
    if not math.isfinite(float(price)) or float(price) < 0:
        raise ValueError("curtailment_price_eur_per_mwh must be finite and ≥ 0")
    return Terms(float(price), share, getattr(spec, "curtail_max_duration_h", None))


def project_terms() -> Terms | None:
    """``terms_of`` the spec of the project being solved (``dc_backup.project_spec``, as DC-06's
    ``project_backup`` reads it); None for an unsaved or non-DC project."""
    ctx = PyPSAService.solve_target_context()
    return terms_for(getattr(ctx, "storage_dir", None) if ctx is not None else None)


def terms_for(storage) -> Terms | None:
    """``terms_of`` the saved spec of the project in ``storage``; None without one."""
    from project_templates import eh_templates as tpl
    from services.adequacy import dc_backup

    if not storage:
        return None
    try:
        _tid, spec, _planning = dc_backup.project_spec(pathlib.Path(storage), tpl)
    except (dc_backup.DcBackupError, OSError, ValueError):
        return None
    return terms_of(spec)


def _dense_load(n, name: str) -> pd.Series:
    return n.get_switchable_as_dense("Load", "p_set")[name].astype(float)


def facility(n) -> pd.Series:
    """F(t) = it_load + cooling, MW; refuses a network without the cooling load on dc_mv (C18)."""
    loads = n.loads
    if (
        COOLING_LOAD not in loads.index
        or str(loads.at[COOLING_LOAD, "bus"]) != COOLING_BUS
        or IT_LOAD not in loads.index
    ):
        raise DcCurtailmentRefused(
            NO_COOLING_LOAD,
            f"Curtailable load: the network has no \u201c{COOLING_LOAD}\u201d load on "
            f"\u201c{COOLING_BUS}\u201d beside \u201c{IT_LOAD}\u201d, so there is nowhere to "
            "curtail but the IT bus. Curtailment is placed on "
            "the cooling bus only; clear curtailment_price_eur_per_mwh or restore the load.",
        )
    return _dense_load(n, IT_LOAD) + _dense_load(n, COOLING_LOAD)


def cap(n, terms: Terms) -> pd.Series:
    """The most the generator can run in each snapshot: min(share * F, cooling), never < 0 (C16)."""
    f = facility(n)
    return np.minimum(terms.share * f, _dense_load(n, COOLING_LOAD)).clip(lower=0.0)


def _hours(n, sns) -> pd.Series:
    return n.snapshot_weightings["generators"].reindex(sns).astype(float)


def _groups(sns) -> list[np.ndarray]:
    """Positions of each period's snapshots (a run never crosses a period)."""
    if isinstance(sns, pd.MultiIndex):
        lvl = np.asarray(sns.get_level_values(0))
        return [np.flatnonzero(lvl == p) for p in pd.unique(lvl)]
    return [np.arange(len(sns))]


def _scan(w: np.ndarray, d_h: float, ks) -> list[tuple[int, int]]:
    """(first, last) of the run from each local position in ``ks`` over one period's weights."""
    cum = np.concatenate([[0.0], np.cumsum(w)])
    out: list[tuple[int, int]] = []
    for k in ks:
        need = cum[k] + d_h + w[k] - 1e-9
        j = int(np.searchsorted(cum, need, side="left"))  # cum[j] >= need
        if j <= len(w):
            out.append((int(k), j - 1))
        elif cum[-1] - cum[k] > d_h + 1e-9:
            out.append((int(k), len(w) - 1))
    return out


def runs(hours: pd.Series, d_h: float, sns) -> list[tuple[int, int]]:
    """(first, last) positions of every run: from each snapshot i, the fewest consecutive
    snapshots whose weighted hours reach D plus snapshot i's own (Q3). Near the horizon's end,
    where no full run starts, the stretch to the end is bounded too when it spans more than D
    weighted hours (with uneven weights it need not sit inside an earlier full run)."""
    w = hours.to_numpy()
    out: list[tuple[int, int]] = []
    for pos in _groups(sns):
        out.extend(
            (int(pos[i]), int(pos[j])) for i, j in _scan(w[pos], d_h, range(len(pos)))
        )
    return out


def fits(hours: pd.Series, d_h: float, sns) -> bool:
    """True when some period spans D plus one snapshot: a full run exists (C21)."""
    w = hours.to_numpy()
    return any(
        float(w[pos].sum()) >= d_h + float(w[pos[0]]) - 1e-9
        for pos in _groups(sns)
        if len(pos)
    )


def check(n, terms: Terms, sns=None) -> None:
    """Every refusal before the solve: no cooling load (C18), a D the horizon cannot hold (C21)."""
    facility(n)
    if terms.max_duration_h is None:
        return
    sns = n.snapshots if sns is None else sns
    if not fits(_hours(n, sns), float(terms.max_duration_h), sns):
        total = float(_hours(n, sns).sum())
        raise DcCurtailmentRefused(
            DURATION_EXCEEDS_HORIZON,
            f"Curtailable load: curtail_max_duration_h {terms.max_duration_h:g} h plus one snapshot "
            f"does not fit in the horizon ({total:g} weighted hours), so no curtailment run can be "
            "bounded. Shorten the duration or lengthen the horizon.",
        )


def apply(
    n, phase, undo_actions: list, captured: dict, terms: Terms | None = None
) -> None:
    """Add the transient generator (module docstring); the restore callback appended to
    ``undo_actions`` captures its dispatch and removes it. ``terms`` defaults to the project's."""
    terms = project_terms() if terms is None else terms
    if terms is None:
        return
    check(n, terms)
    limit = cap(n, terms)
    peak = float(limit.max()) if len(limit) else 0.0
    if not peak > 0 or GENERATOR in n.generators.index:
        return
    added_carrier = DC_CURTAILMENT_CARRIER not in n.carriers.index
    PyPSAService.mark_transient("Generator", GENERATOR)
    try:
        if added_carrier:
            n.add("Carrier", DC_CURTAILMENT_CARRIER)
        n.add(
            "Generator",
            GENERATOR,
            bus=COOLING_BUS,
            p_nom=peak,
            p_max_pu=(limit / peak).clip(upper=1.0),
            marginal_cost=terms.price,
            carrier=DC_CURTAILMENT_CARRIER,
        )
    except Exception:
        PyPSAService.unmark_transient("Generator", GENERATOR)
        raise
    phase(
        f"Added the curtailable load {GENERATOR} on {COOLING_BUS} at {terms.price:g} EUR/MWh, up to "
        f"{terms.share:.0%} of the facility and never above cooling (a resource, NOT unserved energy)."
    )

    def _capture_and_remove():
        try:
            p = n.generators_t.p
            if GENERATOR not in getattr(p, "columns", []):
                raise KeyError(f"{GENERATOR} has no dispatch column")
            from services import period_utils

            series = p[GENERATOR].astype(float).copy()
            w = period_utils.snapshot_weights(n, "generators", sns=series.index)
            total = float(
                (series.clip(lower=0) * w.reindex(series.index).fillna(0.0)).sum()
            )
            captured[META_KEY] = {
                "p_t": series,
                "total_mwh": total,
                "capture_failed": False,
                **_terms_record(terms),
            }
        except Exception as exc:
            # Never silent: a lost capture is recorded as unknown, never as "no curtailment".
            msg = f"[CURTAIL] WARN: the curtailment capture failed ({exc}); curtailed_mwh is unknown."
            _log.warning(msg)
            phase(msg)
            captured[META_KEY] = {
                "p_t": None,
                "total_mwh": None,
                "capture_failed": True,
                **_terms_record(terms),
            }
        if GENERATOR in n.generators.index:
            n.remove("Generator", GENERATOR)
        PyPSAService.unmark_transient("Generator", GENERATOR)
        if added_carrier and DC_CURTAILMENT_CARRIER in n.carriers.index:
            n.remove("Carrier", DC_CURTAILMENT_CARRIER)

    undo_actions.append(("call", _capture_and_remove))


def _terms_record(terms: Terms) -> dict:
    return {
        "price_eur_per_mwh": terms.price,
        "share": terms.share,
        "max_duration_h": terms.max_duration_h,
    }


def clear(n) -> None:
    """At solve start: drop the previous run's record and series, so a failed run never keeps
    them beside an unsolved dispatch (GK A review, ask 2)."""
    n.meta.pop(META_KEY, None)
    if SERIES_ATTR in n.buses_t:
        n.buses_t[SERIES_ATTR] = pd.DataFrame(index=n.snapshots)


def commit(n, captured: dict) -> None:
    """After a successful solve: the run's record in ``n.meta`` (curtailed energy apart from
    unserved energy, the claim limit; C18, C19) and the curtailed MW in
    ``buses_t[SERIES_ATTR]``, or neither when curtailment was off. A failed capture is recorded
    with ``curtailed_mwh`` None and ``capture_failed``, and no series."""
    clear(n)
    rec = captured.get(META_KEY)
    if not rec:
        return
    from services.commercial.settlement_inputs import axis_hash

    p_t = rec.get("p_t")
    failed = bool(rec.get("capture_failed")) or p_t is None
    if not failed:
        frame = pd.DataFrame({COOLING_BUS: pd.Series(p_t, dtype=float)}).reindex(
            n.snapshots
        )
        n.buses_t[SERIES_ATTR] = frame
    n.meta[META_KEY] = {
        "generator": GENERATOR,
        "bus": COOLING_BUS,
        "curtailed_mwh": None if failed else rec["total_mwh"],
        "capture_failed": failed,
        "axis_hash": None if failed else axis_hash(n.snapshots),
        "price_eur_per_mwh": rec["price_eur_per_mwh"],
        "curtailable_share": rec["share"],
        "max_duration_h": rec["max_duration_h"],
        "claim_limit": CLAIM_LIMIT if rec["max_duration_h"] is not None else None,
        "counts_as_unserved_energy": False,
    }


def curtailment_series(n) -> tuple[pd.Series | None, list[str]]:
    """(the committed curtailed MW per snapshot on ``COOLING_BUS``; flags). ``None`` and
    ``curtailment_not_established`` when there is no record, the capture failed, the series is
    missing or incomplete, or it was made on another snapshot axis."""
    from services.commercial.settlement_inputs import axis_hash

    rec = (getattr(n, "meta", None) or {}).get(META_KEY)
    frame = n.buses_t.get(SERIES_ATTR) if hasattr(n.buses_t, "get") else None
    if (
        not rec
        or rec.get("capture_failed")
        or frame is None
        or COOLING_BUS not in frame.columns
        or rec.get("axis_hash") != axis_hash(n.snapshots)
    ):
        return None, [NOT_ESTABLISHED]
    out = frame[COOLING_BUS].reindex(n.snapshots).astype(float)
    if out.isna().any():
        return None, [NOT_ESTABLISHED]
    return out, []


def curtailed_by_load(n) -> tuple[dict[str, pd.Series], list[str]]:
    """What the post-solve surfaces take off a load's served power: ``{COOLING_LOAD: MW}``.
    The loads keep their full ``p_set`` after the transient is removed, so without this the
    curtailed MW would read as served demand with no supply behind it. ``({}, [])`` when the
    run had no curtailment; ``({}, [NOT_ESTABLISHED])`` when it had some but the series cannot
    be read (never a silent 0)."""
    if not (getattr(n, "meta", None) or {}).get(META_KEY):
        return {}, []
    series, flags = curtailment_series(n)
    if series is None or COOLING_LOAD not in n.loads.index:
        return {}, [NOT_ESTABLISHED]
    return {COOLING_LOAD: series}, flags


def refuse_rolling(terms: Terms | None = None) -> None:
    """A plain rolling run (PyPSA's loop, which continues past a failed window) with a duration
    is refused: a failed window could seed the next one's carry-in (C20)."""
    terms = project_terms() if terms is None else terms
    if terms is None or terms.max_duration_h is None:
        return
    raise DcCurtailmentRefused(
        ROLLING_WITHOUT_CARRY_IN,
        f"curtail_max_duration_h ({terms.max_duration_h:g} h) is not supported under a plain "
        "rolling solve: its windows continue past a failed one, so the duration rows cannot "
        "carry in safely. Use the full-horizon solve or the operational run (rolling on fixed "
        "sizes), or remove the duration.",
    )


def check_window(n, horizon: int, terms: Terms | None = None) -> None:
    """C21's operational half, before the first window: D plus one snapshot must fit, in
    weighted hours, in the first window of ``horizon`` snapshots. A shorter last window is
    fine: its runs carry in from the windows before it."""
    terms = project_terms() if terms is None else terms
    if terms is None or terms.max_duration_h is None:
        return
    sns = n.snapshots[: max(1, int(horizon))]
    d_h = float(terms.max_duration_h)
    if not fits(_hours(n, sns), d_h, sns):
        raise DcCurtailmentRefused(
            DURATION_EXCEEDS_WINDOW,
            f"Curtailable load: curtail_max_duration_h {d_h:g} h plus one snapshot does not fit "
            f"in the operational run's window ({float(_hours(n, sns).sum()):g} weighted hours "
            f"in {len(sns)} snapshots). Shorten the duration or lengthen the rolling horizon.",
        )


# ── the duration rows ────────────────────────────────────────────────────────


def add_rows(n, snapshots, terms: Terms, emit) -> tuple[int, int]:
    """The energy-proxy rows over the LP's ``snapshots`` (Q3); (rows, nonzeros), logged (C21).
    Inside an operational run's window the rows carry in (C20, :func:`_window_rows`)."""
    from services.solver import operational

    if terms.max_duration_h is None or GENERATOR not in n.generators.index:
        return 0, 0
    sns = pd.Index(snapshots) if not isinstance(snapshots, pd.Index) else snapshots
    d_h = float(terms.max_duration_h)
    window = operational.current_window(n)
    if window is not None:
        rows, carried = _window_rows(n, sns, terms, window, emit)
        found, nonzeros = _constrain(n, sns, rows)
        emit(
            f"window {window['index'] + 1}: {found} energy-proxy rows ({carried} carried in), "
            f"{nonzeros} nonzeros (D = {d_h:g} h, {CLAIM_LIMIT})."
        )
        return found, nonzeros
    if not fits(_hours(n, sns), d_h, sns):
        raise DcCurtailmentRefused(
            DURATION_EXCEEDS_HORIZON,
            f"Curtailable load: curtail_max_duration_h {d_h:g} h plus one snapshot does not fit in "
            f"the {float(_hours(n, sns).sum()):g} weighted hours this LP spans.",
        )
    f = facility(n).reindex(sns).to_numpy()
    rows = [
        (i, j, d_h * terms.share * float(np.max(f[i : j + 1])))
        for i, j in runs(_hours(n, sns), d_h, sns)
    ]
    found, nonzeros = _constrain(n, sns, rows)
    emit(
        f"{found} energy-proxy rows, {nonzeros} nonzeros (D = {d_h:g} h, {CLAIM_LIMIT})."
    )
    return found, nonzeros


def _constrain(n, sns, rows: list[tuple[int, int, float]]) -> tuple[int, int]:
    """Add ``sum(w * p) <= rhs`` over positions ``first..last`` of ``sns`` for each row."""
    import linopy
    import xarray as xr

    m = n.model
    labels = m.variables["Generator-p"].labels.sel(name=GENERATOR).to_numpy()
    if labels.shape[0] != len(sns):
        raise RuntimeError("Generator-p does not span the LP snapshots")
    w = _hours(n, sns).to_numpy()
    width = max(j - i + 1 for i, j, _ in rows)
    vars_ = np.full((len(rows), width), -1, dtype=np.int64)
    coeffs = np.zeros((len(rows), width))
    rhs = np.empty(len(rows))
    for r, (i, j, bound) in enumerate(rows):
        k = j - i + 1
        vars_[r, :k] = labels[i : j + 1]
        coeffs[r, :k] = w[i : j + 1]
        rhs[r] = bound
    data = xr.Dataset(
        {
            "coeffs": (("DcCurtailment-run", "_term"), coeffs),
            "vars": (("DcCurtailment-run", "_term"), vars_),
        }
    )
    lhs = linopy.LinearExpression(data, m)
    m.add_constraints(
        lhs <= xr.DataArray(rhs, dims="DcCurtailment-run"), name="DcCurtailment-energy"
    )
    return len(rows), int((vars_ >= 0).sum())


def _carried(n, full: pd.Index, first: int, start: int, window: dict) -> np.ndarray:
    """The final dispatch at ``full[first:start]``, the constants of a carry-in row (C20c)."""
    if start > window["written"]:
        raise DcCurtailmentRefused(
            CARRY_IN_UNAVAILABLE,
            f"Curtailable load: window {window['index'] + 1} would carry in dispatch the earlier "
            "windows of this run did not write.",
        )
    p = n.generators_t.p
    if GENERATOR not in getattr(p, "columns", []):
        raise DcCurtailmentRefused(
            CARRY_IN_UNAVAILABLE,
            f"Curtailable load: {GENERATOR} has no dispatch to carry into window "
            f"{window['index'] + 1}.",
        )
    values = p[GENERATOR].reindex(full[first:start]).to_numpy(dtype=float)
    if np.isnan(values).any():
        raise DcCurtailmentRefused(
            CARRY_IN_UNAVAILABLE,
            f"Curtailable load: the dispatch before window {window['index'] + 1} is not solved, "
            "so it cannot carry in.",
        )
    return values


def _window_rows(n, sns, terms: Terms, window: dict, emit) -> tuple[list, int]:
    """The rows of one operational window (C20): the full horizon's runs that reach into
    ``[start, stop)``, each with its whole run's bound (C20a). A run spanning the start bounds
    its window part by the bound less its final dispatch before the start; runs that end before
    the start are dropped (C20d); runs past the window's end are cut at it."""
    full = n.snapshots
    start, stop = int(window["start"]), int(window["stop"])
    if not sns.equals(full[start:stop]):
        raise RuntimeError("the LP snapshots are not the operational window's")
    d_h = float(terms.max_duration_h)
    hours = _hours(n, full).to_numpy()
    f = facility(n).reindex(full).to_numpy()
    rows: list[tuple[int, int, float]] = []
    carried = 0
    for pos in _groups(full):
        lo, hi = max(start, int(pos[0])), min(stop, int(pos[-1]) + 1)
        if lo >= hi:
            continue
        g0, w = int(pos[0]), hours[pos]
        s, e = lo - g0, hi - g0
        cum = np.concatenate([[0.0], np.cumsum(w)])
        back = int(
            np.searchsorted(cum, cum[s] - d_h - float(w.max()) - 1e-9, side="left")
        )
        for k, j in _scan(w, d_h, range(back, e)):
            if j < s:
                continue  # ended before the window (C20d)
            whole = d_h * terms.share * float(np.max(f[g0 + k : g0 + j + 1]))
            bound = whole
            if k < s:
                before = _carried(n, full, g0 + k, g0 + s, window)
                bound -= float((w[k:s] * before).sum())
                if bound < 0:
                    if bound < -1e-9 * whole:
                        raise DcCurtailmentRefused(
                            CARRY_IN_OVER_BOUND,
                            f"Curtailable load: the dispatch before window "
                            f"{window['index'] + 1} already exceeds a run's bound by "
                            f"{-bound:g} MWh.",
                        )
                    emit(
                        f"window {window['index'] + 1}: a carry-in bound of {bound:g} "
                        "MWh is clipped to 0."
                    )
                    bound = 0.0
                carried += 1
            rows.append((g0 + max(k, s) - start, g0 + min(j, e - 1) - start, bound))
    return rows, carried


def _wrap_with_dc_curtailment(network, user_fn, log_queue=None, terms=None):
    """Chain the duration rows after ``user_fn``; ``user_fn`` unchanged when curtailment is off or
    has no duration. Refuses (``DcCurtailmentRefused``) before the solve. ``terms`` defaults to
    the project's."""
    terms = project_terms() if terms is None else terms
    if terms is None:
        return user_fn
    check(network, terms)
    if terms.max_duration_h is None:
        return user_fn

    def emit(msg: str) -> None:
        _safe_log(log_queue, f"[CURTAIL] {msg}")

    def dc_curtailment_fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        add_rows(n, snapshots, terms, emit)

    return dc_curtailment_fn
