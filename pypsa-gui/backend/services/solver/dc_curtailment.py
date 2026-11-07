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
- The lifecycle is the DSR tier's (``services/solver/assumptions.py``): mark transient, add,
  capture into ``captured["dc_curtailment"]``, remove. The carrier and prefix are in
  ``slack.SLACK_CARRIERS`` / ``SLACK_NAME_PREFIXES`` and never in the involuntary tier, so the
  curtailed energy is never unserved energy, never in the ENS cap, DtC or adequacy (C18).

Off (no price, or a share of 0) nothing is read or written: the network, the LP, network.nc and
the fingerprint stay as they were (C15).
"""

from __future__ import annotations

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
CLAIM_LIMIT = (
    "energy proxy: curtailment can still be spread; consecutive hours are not enforced"
)

NO_COOLING_LOAD = "no_cooling_load"
DURATION_EXCEEDS_HORIZON = "duration_exceeds_horizon"


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


def runs(hours: pd.Series, d_h: float, sns) -> list[tuple[int, int]]:
    """(first, last) positions of every run: from each snapshot i, the fewest consecutive
    snapshots whose weighted hours reach D plus snapshot i's own (Q3). Near the horizon's end,
    where no full run starts, the stretch to the end is bounded too when it spans more than D
    weighted hours (with uneven weights it need not sit inside an earlier full run)."""
    w = hours.to_numpy()
    out: list[tuple[int, int]] = []
    for pos in _groups(sns):
        cum = np.concatenate([[0.0], np.cumsum(w[pos])])
        for k in range(len(pos)):
            need = cum[k] + d_h + w[pos[k]] - 1e-9
            j = int(np.searchsorted(cum, need, side="left"))  # cum[j] >= need
            if j <= len(pos):
                out.append((int(pos[k]), int(pos[j - 1])))
            elif cum[-1] - cum[k] > d_h + 1e-9:
                out.append((int(pos[k]), int(pos[-1])))
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
            if GENERATOR in getattr(p, "columns", []):
                from services import period_utils

                series = p[GENERATOR].astype(float).copy()
                w = period_utils.snapshot_weights(n, "generators", sns=series.index)
                captured[META_KEY] = {
                    "p_t": series,
                    "total_mwh": float(
                        (
                            series.clip(lower=0) * w.reindex(series.index).fillna(0.0)
                        ).sum()
                    ),
                    "price_eur_per_mwh": terms.price,
                    "share": terms.share,
                    "max_duration_h": terms.max_duration_h,
                }
        except Exception:
            pass
        if GENERATOR in n.generators.index:
            n.remove("Generator", GENERATOR)
        PyPSAService.unmark_transient("Generator", GENERATOR)
        if added_carrier and DC_CURTAILMENT_CARRIER in n.carriers.index:
            n.remove("Carrier", DC_CURTAILMENT_CARRIER)

    undo_actions.append(("call", _capture_and_remove))


def commit(n, captured: dict) -> None:
    """After a successful solve: the run's record in ``n.meta`` (curtailed energy apart from
    unserved energy, the claim limit; C18, C19), or none when curtailment was off."""
    rec = captured.get(META_KEY)
    if not rec:
        n.meta.pop(META_KEY, None)
        return
    n.meta[META_KEY] = {
        "generator": GENERATOR,
        "bus": COOLING_BUS,
        "curtailed_mwh": rec["total_mwh"],
        "price_eur_per_mwh": rec["price_eur_per_mwh"],
        "curtailable_share": rec["share"],
        "max_duration_h": rec["max_duration_h"],
        "claim_limit": CLAIM_LIMIT if rec["max_duration_h"] is not None else None,
        "counts_as_unserved_energy": False,
    }


# ── the duration rows ────────────────────────────────────────────────────────


def add_rows(n, snapshots, terms: Terms, emit) -> tuple[int, int]:
    """The energy-proxy rows over the LP's ``snapshots`` (Q3); (rows, nonzeros), logged (C21)."""
    import linopy
    import xarray as xr

    if terms.max_duration_h is None or GENERATOR not in n.generators.index:
        return 0, 0
    sns = pd.Index(snapshots) if not isinstance(snapshots, pd.Index) else snapshots
    d_h = float(terms.max_duration_h)
    if not fits(_hours(n, sns), d_h, sns):
        raise DcCurtailmentRefused(
            DURATION_EXCEEDS_HORIZON,
            f"Curtailable load: curtail_max_duration_h {d_h:g} h plus one snapshot does not fit in "
            f"the {float(_hours(n, sns).sum()):g} weighted hours this LP spans.",
        )
    found = runs(_hours(n, sns), d_h, sns)
    m = n.model
    labels = m.variables["Generator-p"].labels.sel(name=GENERATOR).to_numpy()
    if labels.shape[0] != len(sns):
        raise RuntimeError("Generator-p does not span the LP snapshots")
    w = _hours(n, sns).to_numpy()
    f = facility(n).reindex(sns).to_numpy()
    width = max(j - i + 1 for i, j in found)
    vars_ = np.full((len(found), width), -1, dtype=np.int64)
    coeffs = np.zeros((len(found), width))
    rhs = np.empty(len(found))
    for r, (i, j) in enumerate(found):
        k = j - i + 1
        vars_[r, :k] = labels[i : j + 1]
        coeffs[r, :k] = w[i : j + 1]
        rhs[r] = d_h * terms.share * float(np.max(f[i : j + 1]))
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
    nonzeros = int((vars_ >= 0).sum())
    emit(
        f"{len(found)} energy-proxy rows, {nonzeros} nonzeros (D = {d_h:g} h, {CLAIM_LIMIT})."
    )
    return len(found), nonzeros


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
