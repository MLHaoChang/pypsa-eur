"""PCS MVA and STATCOM coupling (AS-05).

The steady reactive need at the point of connection is met by two levers: PCS oversize S_x (MVA above the rated P
of a split storage, AS-02) and the steady slice Q_ss of a STATCOM. The STATCOM's dynamic slice Q_dyn is a separate
variable that no row here reads: AS-06 adds the Pst cut on it, and AS-04's Q_res uses its `q_dyn_mvar`. Keeping
Q_ss and Q_dyn apart means one Mvar is never counted in both the PQ and the Pst cut, the creep the prototype found
(`.scratch/datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md`, section 3).

Tags (custom columns, so the rows survive the project's .nc round-trip and enter the input fingerprint):

  * `Store`: `pcs_r_max` (r, NaN = no oversize) and `pcs_capital_cost` (per MVA of S_x per year), on a split
    storage only. P is its charger p_nom, the AC-side MW (AS-02).
  * one grid-connection `Link` (bus0 grid, bus1 campus): `pq_x_pu`, `pq_pf`, `pq_s_n_mva`, `pq_p_cap_mw`,
    `pq_pf_req` and `statcom_capital_cost` (per Mvar per year). Q_load(t) is the native `q_set` of the Loads on
    bus1.
  * optional on that Link: `pq_q_margin_mvar`, the sizing loop's PQ cut (AS-06), added to Q_load(t).

`_wrap_with_pcs_statcom` adds:

  * sx_cap, per store: S_x <= r x P. A PCS with no battery behind it (P = 0) carries no S_x, so its Mvar comes
    from Q_ss and is priced as a STATCOM;
  * abs_pos, abs_neg, per hour: a(t) >= p_tr(t) and a(t) >= -p_tr(t), with p_tr the Link's flow;
  * pq, per hour: Q_ss + sum_i c_i S_x,i - k_loss a(t) + tan(acos pf_req) p_tr(t) >= Q_load(t), with
    k_loss = x p_cap / (pf^2 S_n), the chord of the transformer's x (p / pf)^2 / S_n on |p| <= p_cap;
  * pcs_cost and statcom_cost: the capital costs of S_x and of Q_ss + Q_dyn in the objective, typed per year
    and charged x `fom_horizon_factor(n)` (years per modelled horizon), as the app charges typed annual costs.

c_i = sqrt(1 + 2 / r_i) is the chord of the PQ circle sqrt((P + S_x)^2 - P^2) on [0, r P]. The circle is
concave in S_x, so the chord is an inner bound: the LP credits PCS Q at rated P, the worst case, and never more
than the exact circle the forward check (`gridspine.static.campus_reactive`) uses. The PF allowance stays signed
on p_tr, so an export hour needs the loss and gets no allowance.

After a successful solve `persist_solution` copies S_x, Q_ss and Q_dyn to the result columns (`pcs_s_x_opt` on the
Store, `statcom_q_ss_opt` and `statcom_q_dyn_opt` on the Link), which `cost_terms` reads for `cost_breakdown`.
A rolling or myopic run rebuilds the rows per LP, so the wrap reads each LP's sizes and the persisted size is the
largest over the LPs, its cost counted once (AS-14, owner decision AS14-MAX-1).

What the rows cannot hold is refused before the solve (`PcsStatcomRefusal`), never skipped: a skipped tag would
give a wrong size, not a no-op. Every limit is a generic engineering value marked "assumed" (SAGC-1).
"""
from __future__ import annotations

import math
import weakref
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from services.solver.periodized_costs import fom_horizon_factor
from services.solver.runtime import _safe_log

if TYPE_CHECKING:
    import pypsa

#: Custom `Store` columns of a PCS oversize.
STORE_COLUMNS = ("pcs_r_max", "pcs_capital_cost")
#: Custom `Link` columns of the PQ cut and the STATCOM.
LINK_COLUMNS = ("pq_x_pu", "pq_pf", "pq_s_n_mva", "pq_p_cap_mw", "pq_pf_req", "statcom_capital_cost")
#: Optional `Link` columns, part of the tags only where set, so a network without them keeps its fingerprint:
#: `pq_q_margin_mvar`, the sizing loop's PQ cut (AS-06), Mvar added to Q_load(t) in the pq row.
OPTIONAL_LINK_COLUMNS = ("pq_q_margin_mvar",)
#: The solved sizes, persisted by `persist_solution`. Never tags: they must stay out of `pcs_statcom_tags` and so
#: out of the input fingerprint.
RESULT_STORE_COLUMNS = ("pcs_s_x_opt",)
RESULT_LINK_COLUMNS = ("statcom_q_ss_opt", "statcom_q_dyn_opt")
#: The rows `_add_rows` builds (tests drop one at a time to show each is needed).
ROWS = ("sx_cap", "abs_pos", "abs_neg", "pq", "pcs_cost", "statcom_cost")


class PcsStatcomRefusal(ValueError):
    """The PCS/STATCOM rows cannot be built as tagged."""


def credit(r: float) -> float:
    """Mvar per MVA of PCS oversize under S_x <= r P: the chord sqrt(1 + 2 / r) of the PQ circle."""
    return math.sqrt(1.0 + 2.0 / r)


def tag_pcs(n: "pypsa.Network", store: str, *, r: float = 0.2, capital_cost: float) -> None:
    """Let split storage `store` carry a PCS oversize S_x <= r x P at `capital_cost` per MVA per year."""
    _set(n.stores, store, {"pcs_r_max": float(r), "pcs_capital_cost": float(capital_cost)}, STORE_COLUMNS)


def tag_pq_link(n: "pypsa.Network", link: str, *, x_pu: float, pf: float, s_n_mva: float, p_cap_mw: float,
                pf_req: float, statcom_capital_cost: float) -> None:
    """Make `link` (bus0 grid, bus1 campus) the transformer the per-hour PQ cut reads."""
    _set(n.links, link, {"pq_x_pu": x_pu, "pq_pf": pf, "pq_s_n_mva": s_n_mva, "pq_p_cap_mw": p_cap_mw,
                         "pq_pf_req": pf_req, "statcom_capital_cost": statcom_capital_cost}, LINK_COLUMNS)


def _set(frame: pd.DataFrame, name: str, values: dict, columns: tuple) -> None:
    for col in columns:
        if col not in frame.columns:
            frame[col] = np.nan
    for col, value in values.items():
        frame.at[name, col] = float(value)


def _tagged(frame: pd.DataFrame | None, columns: tuple) -> pd.DataFrame | None:
    """The rows of `frame` with any of `columns` set, or None."""
    if frame is None:
        return None
    present = [c for c in columns if c in frame.columns]
    if not present:
        return None
    mask = frame[present].notna().any(axis=1)
    return frame[mask] if mask.any() else None


def pcs_statcom_tags(n: "pypsa.Network") -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """(tagged stores, tagged links), each with only the tag columns, or None where nothing is tagged."""
    stores = _tagged(getattr(n, "stores", None), STORE_COLUMNS)
    links = _tagged(getattr(n, "links", None), LINK_COLUMNS)
    if links is not None:
        links = links.reindex(columns=list(LINK_COLUMNS) + [c for c in OPTIONAL_LINK_COLUMNS
                                                             if c in links.columns and links[c].notna().any()])
    return (None if stores is None else stores.reindex(columns=list(STORE_COLUMNS)), links)


def _check(n: "pypsa.Network") -> tuple[pd.DataFrame, str] | None:
    """The tagged stores and the PQ Link, or None when nothing is tagged. Refuses what the rows cannot hold."""
    stores, links = pcs_statcom_tags(n)
    if stores is None and links is None:
        return None
    if links is None:
        raise PcsStatcomRefusal(f"store(s) {', '.join(stores.index)} carry a PCS oversize tag but no Link carries "
                                f"the PQ tags ({', '.join(LINK_COLUMNS)})")
    if len(links) > 1:
        raise PcsStatcomRefusal(f"links {', '.join(links.index)} all carry PQ tags; one grid connection is "
                                "supported")
    link = links.index[0]
    tags = links.loc[link]
    missing = [c for c in LINK_COLUMNS if not np.isfinite(tags[c])]
    if missing:
        raise PcsStatcomRefusal(f"link {link}: PQ tag(s) {', '.join(missing)} not set")
    bad = [c for c, ok in (("pq_x_pu", tags["pq_x_pu"] >= 0), ("pq_pf", 0 < tags["pq_pf"] <= 1),
                           ("pq_s_n_mva", tags["pq_s_n_mva"] > 0), ("pq_p_cap_mw", tags["pq_p_cap_mw"] > 0),
                           ("pq_pf_req", 0 < tags["pq_pf_req"] <= 1),
                           ("statcom_capital_cost", tags["statcom_capital_cost"] >= 0)) if not ok]
    margin = tags.get("pq_q_margin_mvar", np.nan)
    if not (np.isnan(margin) or (np.isfinite(margin) and margin >= 0)):
        bad.append("pq_q_margin_mvar")
    if bad:
        raise PcsStatcomRefusal(f"link {link}: {', '.join(f'{c} = {tags[c]:g}' for c in bad)} out of range")
    s = n.links.loc[link]
    bus1 = s["bus1"]
    if not (n.loads["bus"] == bus1).any():
        raise PcsStatcomRefusal(f"link {link}: bus1 {bus1} carries no Load; the PQ Link must run grid (bus0) -> "
                                "campus (bus1)")
    p_nom = float(s["p_nom_max"] if s["p_nom_extendable"] else s["p_nom"])
    pu = n.get_switchable_as_dense("Link", "p_max_pu")[link].abs().max()
    pu = max(pu, n.get_switchable_as_dense("Link", "p_min_pu")[link].abs().max())
    reach = p_nom * pu
    if not reach <= tags["pq_p_cap_mw"] * (1 + 1e-9):
        raise PcsStatcomRefusal(f"link {link}: the flow can reach {reach:g} MW, beyond pq_p_cap_mw = "
                                f"{tags['pq_p_cap_mw']:g} MW, where the loss chord is no longer an upper bound")
    if stores is None:
        stores = pd.DataFrame(columns=list(STORE_COLUMNS))
    split = n.stores["min_hours"] if "min_hours" in n.stores.columns else pd.Series(np.nan, n.stores.index)
    for name, t in stores.iterrows():
        if not np.isfinite(split.get(name, np.nan)):
            raise PcsStatcomRefusal(f"store {name}: a PCS oversize needs a split storage (AS-02), whose charger "
                                    "p_nom is the rated P")
        if not (np.isfinite(t["pcs_r_max"]) and t["pcs_r_max"] > 0):
            raise PcsStatcomRefusal(f"store {name}: pcs_r_max = {t['pcs_r_max']:g} must be > 0")
        if not (np.isfinite(t["pcs_capital_cost"]) and t["pcs_capital_cost"] >= 0):
            raise PcsStatcomRefusal(f"store {name}: pcs_capital_cost = {t['pcs_capital_cost']:g} must be >= 0")
        if n.stores.at[name, "charger"] not in n.links.index:
            raise PcsStatcomRefusal(f"store {name}: its charger link is missing")
    return stores, link


def _wrap_with_pcs_statcom(network: "pypsa.Network", user_fn, log_queue=None):
    """Chain the PCS/STATCOM rows after `user_fn`. Returns `user_fn` unchanged when nothing is tagged; refuses
    (`PcsStatcomRefusal`) what the rows cannot hold, before the solve."""
    checked = _check(network)
    if checked is None:
        return user_fn

    def emit(msg: str) -> None:
        _safe_log(log_queue, f"[PQ] {msg}")

    lps = _SolvedLps()
    network._pcs_statcom_lps = lps

    def pcs_statcom_fn(n, snapshots):
        lps.read(n.model)
        if user_fn is not None:
            user_fn(n, snapshots)
        stores, link = _check(n)
        _add_rows(n, snapshots, stores, link, emit)
        lps.built(n.model, _period_of(snapshots))

    return pcs_statcom_fn


def _period_of(snapshots):
    """The investment period an LP's snapshots belong to (`_add_rows` holds one), or None on flat snapshots."""
    if not isinstance(snapshots, pd.MultiIndex):
        return None
    period = snapshots.get_level_values(0)[0]
    return period.item() if hasattr(period, "item") else period


class _SolvedLps:
    """The sizes each LP of one run solved, as plain numbers, in order, with its period (AS-14). A rolling or
    myopic run builds a new model per window or period and keeps only the last, so the LP built last is read at
    the first line of the next call, before anything is built (and the final one by `persist_solution`). The
    model is dropped as soon as it is read, so at most the solved model and the one being built are alive.

    `skipped` lists, 1-based, the LPs built but with no solution (a build PyPSA abandoned and retried); they are
    left out of the max and named in the cost note. A failed window or period fails the whole solve before
    anything is persisted (`_RollingWindowFailureCatcher`, the myopic status check)."""

    def __init__(self):
        self.solved: list[tuple[object, dict]] = []
        self.skipped: list[int] = []
        self.built_count = 0
        self._pending = None            # (model, period) of the LP built last, not yet read
        self._last = None               # weakref to the model built last, to match it in `persist_solution`

    def built(self, model, period) -> None:
        self.built_count += 1
        self._pending = (model, period)     # held until read: PyPSA drops its own reference at the next build
        self._last = weakref.ref(model)

    def is_last(self, model) -> bool:
        return self._last is not None and self._last() is model

    def read(self, current, *, final: bool = False) -> None:
        """Fold the LP built last into the record. `current` is the model now being built, whose own pending
        entry is never read then; `persist_solution` passes the solved final model with `final=True`."""
        if self._pending is None:
            return
        model, period = self._pending
        if model is current and not final:
            return                      # this call's own model: it has no solution yet
        self._pending = None
        sol = _model_solution(model)
        del model
        if sol is None:
            self.skipped.append(self.built_count)
        else:
            self.solved.append((period, sol))


def _p_nom(n, link: str):
    """The charger's p_nom: its LP variable when extendable, else the fixed value."""
    if bool(n.links.at[link, "p_nom_extendable"]):
        return n.model.variables["Link-p_nom"].sel(name=link)
    return float(n.links.at[link, "p_nom"])


def _add_rows(n, snapshots, stores: pd.DataFrame, link: str, emit) -> None:
    if isinstance(snapshots, pd.MultiIndex) and snapshots.get_level_values(0).nunique() > 1:
        raise PcsStatcomRefusal("the PCS/STATCOM rows hold one investment period per solve; run the periods "
                                "myopically")
    m = n.model
    t = n.links.loc[link]
    # Both costs are typed per year, like every annual capital_cost the GUI asks for; the LP charges them per
    # modelled horizon, as the periodized-cost fill does for the typed component columns.
    years = fom_horizon_factor(n)
    k_loss = t["pq_x_pu"] * t["pq_p_cap_mw"] / (t["pq_pf"] ** 2 * t["pq_s_n_mva"])
    tan_req = math.tan(math.acos(t["pq_pf_req"]))
    p_tr = m.variables["Link-p"].sel(name=link)
    q_set = n.get_switchable_as_dense("Load", "q_set").loc[snapshots]
    q_load = q_set[n.loads.index[n.loads["bus"] == t["bus1"]]].sum(axis=1)
    margin = float(t.get("pq_q_margin_mvar", np.nan))
    if np.isfinite(margin) and margin > 0:
        q_load = q_load + margin

    q_ss = m.add_variables(lower=0, name="Statcom-q_ss")
    q_dyn = m.add_variables(lower=0, name="Statcom-q_dyn")
    abs_p = m.add_variables(lower=0, coords=[snapshots], name="PQ-abs_p")
    rows = 0
    if "abs_pos" in ROWS:
        m.add_constraints(abs_p - p_tr >= 0, name="PQ-abs_pos")
        rows += 1
    if "abs_neg" in ROWS:
        m.add_constraints(abs_p + p_tr >= 0, name="PQ-abs_neg")
        rows += 1
    lhs = q_ss - k_loss * abs_p + tan_req * p_tr
    cost = 0
    if "statcom_cost" in ROWS:
        cost = years * float(t["statcom_capital_cost"]) * (q_ss + q_dyn)
    if len(stores):
        idx = pd.Index(stores.index, name="Store")
        s_x = m.add_variables(lower=0, coords=[idx], name="PCS-s_x")
        r = stores["pcs_r_max"].astype(float)
        c = _as_array(r.map(credit), idx)
        lhs = lhs + (c * s_x).sum()
        if "pcs_cost" in ROWS:
            cost = cost + (_as_array(years * stores["pcs_capital_cost"].astype(float), idx) * s_x).sum()
        if "sx_cap" in ROWS:
            for name in stores.index:
                p = _p_nom(n, n.stores.at[name, "charger"])
                m.add_constraints(s_x.sel(Store=name) - float(r[name]) * p <= 0, name=f"PCS-sx_cap-{name}")
                rows += 1
    if "pq" in ROWS:
        # On the LP's own snapshot coordinate: a period-level (MultiIndex) snapshot index cannot be renamed
        # to the single name "snapshot" (myopic solves one period on such an index).
        rhs = abs_p.lower.copy(data=q_load.reindex(snapshots).to_numpy(dtype=float))
        m.add_constraints(lhs >= rhs, name="PQ-pq")
        rows += 1
    if not isinstance(cost, int):
        m.add_objective(m.objective.expression + cost, overwrite=True)
    emit(f"{rows} PCS/STATCOM rows on link {link} and {len(stores)} store(s)")


def _as_array(series: pd.Series, idx: pd.Index):
    """`series` as a DataArray on `idx`, for a per-store coefficient."""
    import xarray as xr
    return xr.DataArray(series.reindex(idx).to_numpy(), coords=[idx])


def solution(n: "pypsa.Network") -> dict:
    """The solved S_x per store (MVA), Q_ss and Q_dyn (Mvar), read from the model after the solve."""
    return _read_solution(n.model)


def _model_solution(m) -> dict | None:
    """`_read_solution(m)`, or None when `m` holds no finite solution (not solved, or the solve failed)."""
    try:
        sol = _read_solution(m)
    except Exception:  # noqa: BLE001 — linopy raises when the model was never optimized
        return None
    values = [sol["q_ss"], sol["q_dyn"], *sol["s_x"].tolist()]
    return sol if all(np.isfinite(v) for v in values) else None


def _read_solution(m) -> dict:
    out = {"q_ss": float(m.variables["Statcom-q_ss"].solution), "q_dyn": float(m.variables["Statcom-q_dyn"].solution),
           "s_x": pd.Series(dtype=float)}
    if "PCS-s_x" in m.variables:
        out["s_x"] = m.variables["PCS-s_x"].solution.to_pandas()
    return out


#: `n.meta` key naming what the persisted sizes cover: "horizon" (one LP sized the whole run) or
#: "max_over_solves" (rolling horizon or myopic: the largest size over the window or period LPs, AS-14).
#: "last_solve" is what a network persisted before AS-14 carries: the last LP's sizes only.
BASIS_KEY = "pcs_statcom_basis"
HORIZON_BASIS = "horizon"
MAX_BASIS = "max_over_solves"
LAST_SOLVE_BASIS = "last_solve"
#: `n.meta` keys: how many LPs the sizes are the max over; the LPs (1-based) built with no solution and left
#: out; and, on a network with investment periods, each cost's split {"statcom": [[period, share], ...],
#: "stores": {name: [[period, share], ...]}} (`_max_over`).
LPS_KEY = "pcs_statcom_lps"
SKIPPED_KEY = "pcs_statcom_skipped_lps"
PERIODS_KEY = "pcs_statcom_periods"
LAST_SOLVE_NOTE = "PCS/STATCOM sizes and cost are from the last solve only (rolling/myopic)"
#: A size at or below this (MVA or Mvar) counts as zero.
_ZERO_SIZE_TOL = 1e-6


def clear_solution(n: "pypsa.Network") -> None:
    """Blank the persisted sizes, so a solve that fails leaves none of the previous run's behind."""
    for frame, columns in ((n.stores, RESULT_STORE_COLUMNS), (n.links, RESULT_LINK_COLUMNS)):
        for col in columns:
            if col in frame.columns:
                frame[col] = np.nan
    for key in (BASIS_KEY, LPS_KEY, SKIPPED_KEY, PERIODS_KEY):
        n.meta.pop(key, None)


def _increments(values: list[tuple[object, float]]) -> list[list]:
    """[[period, share], ...]: the share of the largest value each period adds over the running max before it,
    Δ_k = max(0, v_k - max_{j<k} v_j), so the shares sum to 1 and the size is still costed once (GK A condition 3).
    Periods in LP order, repeated ones merged; a largest value of 0 books its (zero) cost in the first period."""
    top = max(v for _, v in values)
    if top <= _ZERO_SIZE_TOL:
        return [[values[0][0], 1.0]]
    out: dict = {}
    running = 0.0
    for period, v in values:
        delta = max(0.0, v - running)
        running = max(running, v)
        if delta > 0:
            out[period] = out.get(period, 0.0) + delta / top
    return [[p, share] for p, share in out.items()]


def _max_over(readings: list[tuple[object, dict]]) -> tuple[dict, dict]:
    """The largest S_x per store, Q_ss and Q_dyn over `readings` (period, solution), in LP order, and how each
    cost splits over the periods: one build at the largest size, each period paying what it adds over the
    running max before it (`_increments`). Q_ss and Q_dyn are maxed apart, so the pair can come from two LPs."""
    stores = pd.Index([]).append([sol["s_x"].index for _, sol in readings]).unique()
    s_x = pd.Series({s: max(float(sol["s_x"].get(s, 0.0)) for _, sol in readings) for s in stores}, dtype=float)
    out = {"q_ss": max(sol["q_ss"] for _, sol in readings), "q_dyn": max(sol["q_dyn"] for _, sol in readings),
           "s_x": s_x}
    # The STATCOM cost is on Q_ss + Q_dyn, each maxed apart: its increment in a period is the sum of theirs.
    statcom = {}
    for key in ("q_ss", "q_dyn"):
        for period, share in _increments([(p, sol[key]) for p, sol in readings]):
            statcom[period] = statcom.get(period, 0.0) + share * out[key]
    total = out["q_ss"] + out["q_dyn"]
    split = {"statcom": ([[p, v / total] for p, v in statcom.items() if v > 0] if total > _ZERO_SIZE_TOL
                         else [[readings[0][0], 1.0]]),
             "stores": {str(s): _increments([(p, float(sol["s_x"].get(s, 0.0))) for p, sol in readings])
                        for s in stores}}
    return out, split


def persist_solution(n: "pypsa.Network", basis: str = HORIZON_BASIS) -> list[str]:
    """Write the solved S_x (MVA, per tagged store) and Q_ss, Q_dyn (Mvar, on the PQ Link) to the result columns,
    so they outlive `n.model` and survive the project's .nc round-trip (AS-12). Called after a successful solve,
    with `basis` "max_over_solves" for a rolling or myopic one, which persists the largest size over its LPs
    (AS-14); clears the columns first, so a solve without the rows
    leaves no stale sizes. A network that never had the columns gains none unless the rows were built.

    A tagged store or Link whose variable is not in the model stays NaN, never 0; the returned warnings name it."""
    clear_solution(n)
    stores, links = pcs_statcom_tags(n)
    m = getattr(n, "model", None)
    if m is None or "Statcom-q_ss" not in m.variables:
        tagged = [*([] if stores is None else [f"store {s}" for s in stores.index]),
                  *([] if links is None else [f"link {k}" for k in links.index])]
        return [f"{t}: tagged for PCS/STATCOM but the solve built no rows for it; its size is left blank"
                for t in tagged]
    lps = getattr(n, "_pcs_statcom_lps", None)
    skipped: list[int] = []
    if lps is not None and lps.is_last(m):
        lps.read(m, final=True)
        readings, skipped = list(lps.solved), list(lps.skipped)
    else:
        readings = []
    warnings = []
    if not readings:  # rows built outside this wrap's record (by hand, or by an earlier wrap): this model alone
        readings = [(None, solution(n))]
        if basis == MAX_BASIS:
            warnings.append("the PCS/STATCOM sizes of the earlier windows or periods were not recorded; the "
                            "persisted sizes are the last LP's only")
    if basis != MAX_BASIS:
        readings, skipped = readings[-1:], []
    sol, split = _max_over(readings)
    link = links.index[0]
    _set(n.links, link, {"statcom_q_ss_opt": sol["q_ss"], "statcom_q_dyn_opt": sol["q_dyn"]}, RESULT_LINK_COLUMNS)
    for store, s_x in sol["s_x"].items():
        _set(n.stores, store, {"pcs_s_x_opt": s_x}, RESULT_STORE_COLUMNS)
    n.meta[BASIS_KEY] = basis
    n.meta[LPS_KEY] = len(readings)
    if skipped:
        n.meta[SKIPPED_KEY] = skipped
    if any(p is not None for p, _ in [*split["statcom"], *(x for v in split["stores"].values() for x in v)]):
        n.meta[PERIODS_KEY] = split
    missing = [] if stores is None else [s for s in stores.index if s not in sol["s_x"].index]
    return warnings + [f"store {s}: tagged for a PCS oversize but the solve built no S_x for it; its size is left "
                       "blank" for s in missing]


def cost_notes(n: "pypsa.Network") -> list[str]:
    """What `cost_terms` leaves out or how it is based: tagged rows with no persisted size, and the max-over-LPs
    basis of a rolling or myopic run (the last-solve basis of a network persisted before AS-14)."""
    stores, links = pcs_statcom_tags(n)
    notes = []
    for frame, tagged, cols, what in ((n.stores, stores, RESULT_STORE_COLUMNS, "store"),
                                      (n.links, links, RESULT_LINK_COLUMNS, "link")):
        if tagged is None:
            continue
        sized = frame.reindex(columns=list(cols)).loc[tagged.index].notna().all(axis=1)
        notes += [f"{what} {name}: tagged for PCS/STATCOM but no solved size; its cost is not counted"
                  for name in sized.index[~sized]]
    if n.meta.get(BASIS_KEY) == LAST_SOLVE_BASIS:
        notes.append(LAST_SOLVE_NOTE)
    if n.meta.get(BASIS_KEY) == MAX_BASIS:
        notes.append(f"PCS/STATCOM sizes are the largest over {n.meta.get(LPS_KEY, '?')} LPs (rolling windows or "
                     "myopic periods), Q_ss and Q_dyn each on its own, so the pair can come from two LPs; the "
                     "investment is counted once, at that size, each period paying what it adds over the periods "
                     "before it. Each LP charged the size it chose for its own window or period, so this total "
                     "differs from the sum of the window objectives")
        skipped = n.meta.get(SKIPPED_KEY) or []
        if len(skipped):
            notes.append(f"PCS/STATCOM: {len(skipped)} LP(s) built with no solution (LP "
                         f"{', '.join(str(int(i)) for i in skipped)}, a build the solver retried) are not in the max")
    return notes


def cost_items(n: "pypsa.Network") -> list[tuple[str, object, float]]:
    """The PCS and STATCOM cost of the persisted sizes, from the typed annual costs x `fom_horizon_factor(n)`, as
    `_add_rows` put them in the objective: (label, period, EUR) with label "PCS oversize" (per store) or
    "STATCOM", each only when its size is persisted. The period is the investment period the cost is booked in
    (None on flat snapshots); on a max-over-LPs basis a size that grew is split over the periods it grew in, and
    the parts sum to the largest size's cost, counted once (AS14-MAX-1)."""
    split = n.meta.get(PERIODS_KEY) or {}
    store_split = split.get("stores") or {}
    whole = [[None, 1.0]]
    items = []
    years = fom_horizon_factor(n)
    stores = n.stores
    if all(c in stores.columns for c in ("pcs_s_x_opt", "pcs_capital_cost")):
        for name, s_x in stores["pcs_s_x_opt"].dropna().items():
            eur = years * float(stores.at[name, "pcs_capital_cost"]) * float(s_x)
            items += [("PCS oversize", p, eur * float(share)) for p, share in store_split.get(str(name), whole)]
    links = n.links
    if all(c in links.columns for c in (*RESULT_LINK_COLUMNS, "statcom_capital_cost")):
        for name, q in links[list(RESULT_LINK_COLUMNS)].dropna().iterrows():
            eur = years * float(links.at[name, "statcom_capital_cost"]) * float(q.sum())
            items += [("STATCOM", p, eur * float(share)) for p, share in split.get("statcom") or whole]
    return items


def cost_terms(n: "pypsa.Network") -> dict:
    """The PCS and STATCOM cost of the persisted sizes (`cost_items` summed per label): {"PCS oversize": EUR,
    "STATCOM": EUR}, each only when its size is persisted."""
    out: dict[str, float] = {}
    for label, _period, eur in cost_items(n):
        out[label] = out.get(label, 0.0) + eur
    return out
