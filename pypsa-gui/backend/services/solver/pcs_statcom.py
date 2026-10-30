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

`_wrap_with_pcs_statcom` adds:

  * sx_cap, per store: S_x <= r x P. A PCS with no battery behind it (P = 0) carries no S_x, so its Mvar comes
    from Q_ss and is priced as a STATCOM;
  * abs_pos, abs_neg, per hour: a(t) >= p_tr(t) and a(t) >= -p_tr(t), with p_tr the Link's flow;
  * pq, per hour: Q_ss + sum_i c_i S_x,i - k_loss a(t) + tan(acos pf_req) p_tr(t) >= Q_load(t), with
    k_loss = x p_cap / (pf^2 S_n), the chord of the transformer's x (p / pf)^2 / S_n on |p| <= p_cap;
  * pcs_cost and statcom_cost: the capital costs of S_x and of Q_ss + Q_dyn in the objective.

c_i = sqrt(1 + 2 / r_i) is the chord of the PQ circle sqrt((P + S_x)^2 - P^2) on [0, r P]. The circle is
concave in S_x, so the chord is an inner bound: the LP credits PCS Q at rated P, the worst case, and never more
than the exact circle the forward check (`gridspine.static.campus_reactive`) uses. The PF allowance stays signed
on p_tr, so an export hour needs the loss and gets no allowance.

What the rows cannot hold is refused before the solve (`PcsStatcomRefusal`), never skipped: a skipped tag would
give a wrong size, not a no-op. Every limit is a generic engineering value marked "assumed" (SAGC-1).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from services.solver.runtime import _safe_log

if TYPE_CHECKING:
    import pypsa

#: Custom `Store` columns of a PCS oversize.
STORE_COLUMNS = ("pcs_r_max", "pcs_capital_cost")
#: Custom `Link` columns of the PQ cut and the STATCOM.
LINK_COLUMNS = ("pq_x_pu", "pq_pf", "pq_s_n_mva", "pq_p_cap_mw", "pq_pf_req", "statcom_capital_cost")
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
    return (None if stores is None else stores.reindex(columns=list(STORE_COLUMNS)),
            None if links is None else links.reindex(columns=list(LINK_COLUMNS)))


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

    def pcs_statcom_fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        stores, link = _check(n)
        _add_rows(n, snapshots, stores, link, emit)

    return pcs_statcom_fn


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
    k_loss = t["pq_x_pu"] * t["pq_p_cap_mw"] / (t["pq_pf"] ** 2 * t["pq_s_n_mva"])
    tan_req = math.tan(math.acos(t["pq_pf_req"]))
    p_tr = m.variables["Link-p"].sel(name=link)
    q_set = n.get_switchable_as_dense("Load", "q_set").loc[snapshots]
    q_load = q_set[n.loads.index[n.loads["bus"] == t["bus1"]]].sum(axis=1)

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
        cost = float(t["statcom_capital_cost"]) * (q_ss + q_dyn)
    if len(stores):
        idx = pd.Index(stores.index, name="Store")
        s_x = m.add_variables(lower=0, coords=[idx], name="PCS-s_x")
        r = stores["pcs_r_max"].astype(float)
        c = _as_array(r.map(credit), idx)
        lhs = lhs + (c * s_x).sum()
        if "pcs_cost" in ROWS:
            cost = cost + (_as_array(stores["pcs_capital_cost"].astype(float), idx) * s_x).sum()
        if "sx_cap" in ROWS:
            for name in stores.index:
                p = _p_nom(n, n.stores.at[name, "charger"])
                m.add_constraints(s_x.sel(Store=name) - float(r[name]) * p <= 0, name=f"PCS-sx_cap-{name}")
                rows += 1
    if "pq" in ROWS:
        m.add_constraints(lhs >= q_load.rename_axis("snapshot"), name="PQ-pq")
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
    m = n.model
    out = {"q_ss": float(m.variables["Statcom-q_ss"].solution), "q_dyn": float(m.variables["Statcom-q_dyn"].solution),
           "s_x": pd.Series(dtype=float)}
    if "PCS-s_x" in m.variables:
        out["s_x"] = m.variables["PCS-s_x"].solution.to_pandas()
    return out
