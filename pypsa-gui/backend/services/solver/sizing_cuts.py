"""The sizing loop's cuts as LP rows (AS-06).

The loop controller (`services.adequacy.sizing_loop`) keeps a cuts table; the runner writes it onto the network
copy it solves as tags, and `_wrap_with_sizing_cuts`, chained after the PCS/STATCOM rows (AS-05), reads them.
Cuts are never removed and only tighten, so each round's LP is the previous one's with a smaller feasible set.

Tags (custom columns, so the rows survive the project's .nc round-trip and enter the input fingerprint):

  * one grid-connection `Link` (bus0 grid, bus1 campus): `cut_import_cap_mw`, `cut_gfm_mva_min`,
    `cut_pst_rhs_mva`, and the Pst row's inputs `pst_swing_mw` (S + A, the envelope's worst phase),
    `pst_tan_phi_swing` (the load's reactive swing per MW, assumed until vendor data) and `pst_xr` (PoC X/R);
  * `Store`: `gfm_capable` (1 = a grid-forming split storage, AS-02).

`_wrap_with_sizing_cuts` adds, each only when its cut is set:

  * import_cap, per snapshot: p_tr(t) <= cut_import_cap_mw. It bounds the import flow at the PoC, never the
    transformer's p_nom, so a transformer the loop froze at a library size cannot contradict it;
  * gfm_floor: sum over `gfm_capable` stores k of P_k + S_x,k >= cut_gfm_mva_min, P_k the charger p_nom (AC
    MW) and S_x,k its PCS oversize (AS-05) when tagged: the ESCR cut, (S_sc + 1.2 GFM MVA) / P_n >= ESCR_min
    solved for the GFM MVA;
  * pst: P_res >= S + A - sum P_sm, Q_res >= tan phi_swing (S + A) - Q_dyn, P_res, Q_res >= 0 and
    cos psi P_res + sin psi Q_res <= cut_pst_rhs_mva, psi = atan(X/R). These are the AS-04 quantities of
    `services.adequacy.flicker_inputs` (`residual_swing`, `voltage_form_mva`); P_sm is AS-03's smoothing slice
    and Q_dyn AS-05's dynamic STATCOM slice, each 0 where its rows are not built. PCS Mvar and Q_ss are never
    in this row.

The PQ cut is not a row here: it is AS-05's pq row with `pq_q_margin_mvar` added to Q_load(t). The smoothing
floors are `SolverConfig.envelope_floors` (AS-03). No row here carries a cost, so `pcs_statcom.cost_terms`
stays the whole PCS/STATCOM cost.

The Pst right-hand side (`pst_rhs_mva`) starts at d_lim x S_sc,min: d_lim the voltage change at which
gridspine's analytic Pst (`gridspine.static.flicker.pst`) meets the limit at the envelope's r changes per
minute, and S_sc,min = S_k'' / c_max, the IEC 60909 c factor (`gridspine.static.island_sc_seq.c_factor`). When
the forward pass still finds the voltage change d above d_lim, `tightened_pst_rhs` lowers it by the shortfall.

What the rows cannot hold is refused before the solve (`SizingCutsRefusal`). Every limit is a generic
engineering value marked "assumed" (SAGC-1).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from services.solver.pcs_statcom import LINK_COLUMNS as PQ_LINK_COLUMNS
from services.solver.pcs_statcom import _p_nom
from services.solver.runtime import _safe_log

if TYPE_CHECKING:
    import pypsa

#: Custom `Link` columns: the cuts and the Pst row's inputs.
LINK_COLUMNS = ("cut_import_cap_mw", "cut_gfm_mva_min", "cut_pst_rhs_mva", "pst_swing_mw", "pst_tan_phi_swing",
                "pst_xr")
#: Custom `Store` column: a grid-forming split storage.
STORE_COLUMNS = ("gfm_capable",)
#: The cuts table key each Link column holds (`services.adequacy.sizing_loop.CUT_DIRECTIONS`).
CUT_COLUMNS = {"import_cap_mw": "cut_import_cap_mw", "gfm_mva_min": "cut_gfm_mva_min",
               "pst_rhs_mva": "cut_pst_rhs_mva", "pq_margin_mvar": "pq_q_margin_mvar"}
#: The rows `_add_rows` builds (tests drop one at a time to show each is needed).
ROWS = ("import_cap", "gfm_floor", "pst")


class SizingCutsRefusal(ValueError):
    """The sizing cuts cannot be built as tagged."""


def pst_rhs_mva(pst_limit: float, changes_per_min: float, sk_mva: float, c_max: float | None = None) -> float:
    """d_lim x S_sc,min in MVA: d_lim (pu) the voltage change whose analytic Pst at `changes_per_min` equals
    `pst_limit`, S_sc,min = `sk_mva` / c_max. `c_max` defaults to gridspine's IEC 60909 c factor."""
    try:
        from gridspine.static import flicker as fl
        from gridspine.static.island_sc_seq import c_factor
    except ImportError as exc:  # pragma: no cover - build-shape branch
        raise SizingCutsRefusal(f"the Pst cut needs gridspine ({exc})") from exc

    c = c_factor(None, "max") if c_max is None else float(c_max)
    d_lim_pct = float(pst_limit) / fl.pst(1.0, float(changes_per_min))     # Pst is linear in d
    return d_lim_pct / 100.0 * float(sk_mva) / c


def tightened_pst_rhs(rhs: float | None, *, step_lp_mva: float, d_pct: float, d_lim_pct: float,
                      sk_min_mva: float) -> float:
    """rhs' = min(rhs, step_lp - (d - d_lim) / 100 x S_sc,min): the LP's own cos psi P_res + sin psi Q_res,
    less the MVA by which the forward pass found the voltage change d (%) over d_lim (%). Never looser."""
    new = float(step_lp_mva) - (float(d_pct) - float(d_lim_pct)) / 100.0 * float(sk_min_mva)
    return new if rhs is None else min(float(rhs), new)


def _set(frame: pd.DataFrame, name: str, values: dict) -> None:
    for col, v in values.items():
        if col not in frame.columns:
            frame[col] = np.nan
        frame.at[name, col] = float(v)


def tag_cuts(n: "pypsa.Network", link: str, cuts: dict, *, swing_mw: float | None = None,
             tan_phi_swing: float | None = None, xr: float | None = None) -> None:
    """Write the cuts table's LP keys (`CUT_COLUMNS`) and the Pst row's inputs onto `link`."""
    unknown = sorted(set(cuts) - set(CUT_COLUMNS))
    if unknown:
        raise ValueError(f"unknown LP cut(s) {', '.join(unknown)}; the floors go in SolverConfig.envelope_floors")
    values = {CUT_COLUMNS[k]: v for k, v in cuts.items()}
    for col, v in (("pst_swing_mw", swing_mw), ("pst_tan_phi_swing", tan_phi_swing), ("pst_xr", xr)):
        if v is not None:
            values[col] = v
    _set(n.links, link, values)


def tag_gfm(n: "pypsa.Network", store: str, on: bool = True) -> None:
    """Flag split storage `store` as grid-forming (its P and S_x count toward the GFM floor)."""
    _set(n.stores, store, {"gfm_capable": 1.0 if on else 0.0})


def _tagged(frame: pd.DataFrame | None, columns: tuple) -> pd.DataFrame | None:
    if frame is None:
        return None
    present = [c for c in columns if c in frame.columns]
    if not present:
        return None
    mask = frame[present].notna().any(axis=1)
    return frame.loc[mask].reindex(columns=list(columns)) if mask.any() else None


def sizing_cuts_tags(n: "pypsa.Network") -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """(tagged stores, tagged links), each with only the tag columns, or None where nothing is tagged."""
    return _tagged(getattr(n, "stores", None), STORE_COLUMNS), _tagged(getattr(n, "links", None), LINK_COLUMNS)


def _gfm_stores(n) -> list[str]:
    if "gfm_capable" not in n.stores.columns:
        return []
    on = pd.to_numeric(n.stores["gfm_capable"], errors="coerce").fillna(0.0) > 0
    return [str(s) for s in n.stores.index[on]]


def _check(n: "pypsa.Network") -> tuple[str, dict] | None:
    """The cut Link and its finite tags, or None when nothing asks for a row. Refuses what the rows cannot hold."""
    stores, links = sizing_cuts_tags(n)
    if "pq_q_margin_mvar" in n.links.columns:
        pq = n.links[[c for c in PQ_LINK_COLUMNS if c in n.links.columns]].notna().any(axis=1) \
            if any(c in n.links.columns for c in PQ_LINK_COLUMNS) else pd.Series(False, n.links.index)
        orphan = [str(k) for k, v in n.links["pq_q_margin_mvar"].items() if pd.notna(v) and not pq.get(k, False)]
        if orphan:
            raise SizingCutsRefusal(f"link(s) {', '.join(orphan)} carry pq_q_margin_mvar but not the AS-05 PQ tags "
                                    f"({', '.join(PQ_LINK_COLUMNS)}) the margin adds to")
    if links is None:
        return None
    if len(links) > 1:
        raise SizingCutsRefusal(f"links {', '.join(links.index)} all carry sizing cut tags; one grid connection is "
                                "supported")
    link = str(links.index[0])
    tags = {c: float(v) for c, v in links.loc[link].items() if pd.notna(v)}
    bad = [c for c, v in tags.items()
           if not math.isfinite(v) or v < 0 or (c == "pst_xr" and v <= 0)]
    if bad:
        raise SizingCutsRefusal(f"link {link}: {', '.join(f'{c} = {tags[c]:g}' for c in bad)} out of range")
    if "cut_pst_rhs_mva" in tags:
        missing = [c for c in ("pst_swing_mw", "pst_xr") if c not in tags]
        if missing:
            raise SizingCutsRefusal(f"link {link}: the Pst cut needs {' and '.join(missing)}")
    if "cut_gfm_mva_min" in tags and tags["cut_gfm_mva_min"] > 0:
        gfm = _gfm_stores(n)
        if not gfm:
            raise SizingCutsRefusal(f"link {link}: cut_gfm_mva_min is set but no Store is flagged gfm_capable")
        for s in gfm:
            if "charger" not in n.stores.columns or n.stores.at[s, "charger"] not in n.links.index:
                raise SizingCutsRefusal(f"store {s}: gfm_capable needs a split storage (AS-02) with a charger Link")
    return link, tags


def _wrap_with_sizing_cuts(network: "pypsa.Network", user_fn, log_queue=None):
    """Chain the sizing-cut rows after `user_fn`; `user_fn` unchanged when no cut is set. Refuses
    (`SizingCutsRefusal`) what the rows cannot hold, before the solve."""
    checked = _check(network)
    if checked is None or not any(c.startswith("cut_") for c in checked[1]):
        return user_fn

    def emit(msg: str) -> None:
        _safe_log(log_queue, f"[SIZING] {msg}")

    def sizing_cuts_fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        link, tags = _check(n)
        _add_rows(n, snapshots, link, tags, emit)

    return sizing_cuts_fn


def _add_rows(n, snapshots, link: str, tags: dict, emit) -> None:
    m = n.model
    rows = 0
    if "import_cap" in ROWS and "cut_import_cap_mw" in tags:
        p_tr = m.variables["Link-p"].sel(name=link)
        m.add_constraints(p_tr <= tags["cut_import_cap_mw"], name="Sizing-import_cap")
        rows += 1
    if "gfm_floor" in ROWS and tags.get("cut_gfm_mva_min", 0.0) > 0:
        lhs = 0
        s_x = m.variables["PCS-s_x"] if "PCS-s_x" in m.variables else None
        for s in _gfm_stores(n):
            lhs = lhs + _p_nom(n, n.stores.at[s, "charger"])
            if s_x is not None and s in s_x.indexes["Store"]:
                lhs = lhs + s_x.sel(Store=s)
        if isinstance(lhs, (int, float)):
            if lhs < tags["cut_gfm_mva_min"]:
                raise SizingCutsRefusal(f"the GFM stores are fixed at {lhs:g} MVA, below cut_gfm_mva_min = "
                                        f"{tags['cut_gfm_mva_min']:g}")
        else:
            m.add_constraints(lhs >= tags["cut_gfm_mva_min"], name="Sizing-gfm_floor")
            rows += 1
    if "pst" in ROWS and "cut_pst_rhs_mva" in tags:
        swing = tags["pst_swing_mw"]
        psi = math.atan(tags["pst_xr"])
        p_res = m.add_variables(lower=0, name="Sizing-p_res")
        q_res = m.add_variables(lower=0, name="Sizing-q_res")
        p_sm = m.variables["EnvelopeFloor-p_smooth"].sum() if "EnvelopeFloor-p_smooth" in m.variables else 0
        q_dyn = m.variables["Statcom-q_dyn"] if "Statcom-q_dyn" in m.variables else 0
        m.add_constraints(p_res + p_sm >= swing, name="Sizing-pst_p_res")
        m.add_constraints(q_res + q_dyn >= tags.get("pst_tan_phi_swing", 0.0) * swing, name="Sizing-pst_q_res")
        m.add_constraints(math.cos(psi) * p_res + math.sin(psi) * q_res <= tags["cut_pst_rhs_mva"],
                          name="Sizing-pst")
        rows += 3
    emit(f"{rows} sizing-cut rows on link {link}")
