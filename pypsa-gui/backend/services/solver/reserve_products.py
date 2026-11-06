"""Reserve products on backup storage (DC grid readiness 06).

A data-centre project's backup storage (the DC-03 strings ``backup_bess_*`` and
their spares, and any other storage its island sidecar lists as ride-through
storage) may commit headroom to reserve products (``models.reserve_products``):
generic FCR / aFRR / mFRR-style shapes, priced per MW per hour committed from a
user Library series. The critical-backup energy is never committed.

New LP variables r[k, b, t] >= 0 (MW of product k on storage b), and per
storage b, with U = the up and symmetric products on b, D = the down and
symmetric ones, d_k = ``sustain_h``, eh = ``snapshot_weightings.stores``:

  1. up power      p_dis - p_store + sum_U r <= p_nom * avail_up(t)
  2. down power    p_store - p_dis + sum_D r <= p_nom * (-p_min_pu(t))
  3. backup floor  soc(t) - sum_U r * d_k / eta_dis(t) >= floor_b, and the same on soc(t-1)
  4. down energy   soc(t) + sum_D r * d_k * eta_store(t) <= max_hours * p_nom, and on soc(t-1)
  5. product cap   sum_b r[k, b, t] <= max_commit_mw_k
  objective       -= sum_t hours(t) * period_w(t) * price_k(t) * sum_b r[k, b, t]

``avail_up`` is ``p_max_pu(t)``, except on a DC-03 string, whose time series
is the DC-03 dispatch hold (no scheduled discharge outside an outage), not
its availability: there the static ``p_max_pu`` bounds the reserve (GK A R1).
The strings are named from the stored spec's sizing (``dc_backup.string_names``),
never by a name pattern; a string whose series holds anything but 0 is refused.
An activated up-reserve draws energy only above the floor, through (3). soc(t-1)
is written through PyPSA's own storage balance, so the cyclic, per-period
and initial-SoC paths are PyPSA's:

  soc(t-1) = (soc(t) + eh p_dis / eta_dis - eh eta_store p_store + eh spill - eh inflow) / (1 - standing_loss)^eh

The floor has one source, the stored DC-03 spec (``derive_floors``), read at
LP build: the DC-03 strings keep their sized usable backup energy (spares
included, so N+1 holds while a reserve is committed); another ride-through
storage keeps P_crit x ride_through_s / 3600 / eta_dis, P_crit the critical
load's peak the DC-03 sizing reads. A storage whose floor cannot be read is
not eligible; its floor is never 0.

Capacity payments only: activation energy, its price, cycling wear and
non-delivery penalties are not modelled; nor is a minimum bid (it needs
integers). On loss of grid the obligations lapse, so the DtC outage
re-dispatch carries no reserve rows (``without_reserves``).

The solved commitments are kept on the network as ``storage_units_t``
frame ``ic_reserve_mw`` (columns ``<storage>|<product>``), with
``meta["ic_reserve"]`` naming the products and the snapshot axis.

Pure service: imports neither routers nor ``solver_service``.
"""
from __future__ import annotations

import dataclasses
import json
import math
import pathlib
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import xarray as xr

from services.solver.runtime import _safe_log

if TYPE_CHECKING:
    import pypsa

    from models.reserve_products import ReserveProduct

#: The bound price frame (``buses_t``) and its column per product.
PRICE_FRAME = "ic_reserve_price"
PRICE_PREFIX = "ic:reserve:"
#: The solved commitments (``storage_units_t``), columns ``<storage>|<product>``.
OUT_FRAME = "ic_reserve_mw"
META_KEY = "ic_reserve"
#: Each bound price's currency, {product id: ISO code or None} (binding, for the preflight).
CURRENCY_META = "ic_reserve_price_currency"
SEP = "|"
TOL = 1e-6

#: Refusal kinds (tool-error-kinds.d/claude-dc06-flex-revenue.json; `POST /run` answers them).
NOT_ELIGIBLE = "reserve_storage_not_eligible"
FLOOR_EXCEEDS = "reserve_floor_exceeds_energy"
PRICE_MISSING = "reserve_price_missing"
PRICE_STALE = "reserve_price_stale"
INITIAL_SOC = "reserve_initial_soc_below_floor"
STRING_AVAIL = "reserve_string_availability_edited"
KINDS = (NOT_ELIGIBLE, FLOOR_EXCEEDS, PRICE_MISSING, PRICE_STALE, INITIAL_SOC, STRING_AVAIL)

_UNSET = object()


class ReserveRefusal(ValueError):
    """The reserve rows cannot be built as asked; raised before the solve."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ── inputs ───────────────────────────────────────────────────────────────────


def products_of(cfg) -> list["ReserveProduct"]:
    """The config's reserve products (``SolverConfig.commercial.reserve_products``)."""
    from models.reserve_products import ReserveProduct

    raw = _raw_products(getattr(cfg, "commercial", None))
    return [p if isinstance(p, ReserveProduct) else ReserveProduct.model_validate(p) for p in raw]


def without_reserves(cfg):
    """``cfg`` with no reserve products: an outage re-dispatch, where the grid the
    obligations are owed to is gone. ``cfg`` itself is unchanged."""
    commercial = getattr(cfg, "commercial", None)
    if not _raw_products(commercial):
        return cfg
    if isinstance(commercial, dict):
        # The config as it reads without products: the key gone, and no config at all
        # when the products were all it held.
        rest = {k: v for k, v in commercial.items() if k != "reserve_products"}
        return dataclasses.replace(cfg, commercial=rest or None)
    return dataclasses.replace(cfg, commercial=commercial.model_copy(update={"reserve_products": []}))


def _raw_products(commercial) -> list:
    """The products of a commercial config: the solve's plain dict or the typed model."""
    if isinstance(commercial, dict):
        return list(commercial.get("reserve_products") or [])
    return list(getattr(commercial, "reserve_products", None) or [])


def price_column(product_id: str) -> str:
    return f"{PRICE_PREFIX}{product_id}"


def reserve_weights(n, sns=None) -> tuple[pd.Series, pd.Series]:
    """(hours, period weight) per snapshot: ``snapshot_weightings.objective``
    and ``investment_period_weightings.objective`` of its period (1 on a
    single-period network). The LP weights revenue by their product, as PyPSA
    weights marginal cost; a settlement line per period by the hours alone
    (the investment case applies the years and the discounting)."""
    sns = n.snapshots if sns is None else sns
    hours = n.snapshot_weightings.objective.loc[sns].astype(float)
    if isinstance(sns, pd.MultiIndex) and getattr(n, "_multi_invest", False):
        ipw = n.investment_period_weightings.objective
        period = pd.Series(ipw.loc[sns.get_level_values(0)].to_numpy(dtype=float), index=sns)
    else:
        period = pd.Series(1.0, index=sns)
    return hours, period


def bound_price(n, product) -> tuple[pd.Series | None, list[str]]:
    """The product's bound price per snapshot, or None + why (missing, bound
    from another ref version, another snapshot axis, a gap)."""
    from services.commercial import settlement_inputs as SI

    s, flags = SI._read(n, PRICE_FRAME, price_column(product.id), product.capacity_price,
                        PRICE_MISSING)
    return s, flags


def inputs_digest(products: list["ReserveProduct"], floors: dict[str, float]) -> str:
    """What a reserve solve read: the products (their price refs included) and
    the floors of the storages they may use. A settlement on a solve whose
    digest is not the current one is stale (GK A R6, GK B L5)."""
    import hashlib

    used = {s for p in products for s in (p.storages or floors)}
    payload = {"products": [p.model_dump(mode="json") for p in products],
               "floors": {s: repr(float(floors[s])) for s in sorted(used) if s in floors}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# ── the floor (one source: the stored DC-03 spec) ────────────────────────────


def derive_backup(n, project_dir) -> tuple[dict[str, float], frozenset[str]]:
    """({storage: floor MWh} for every eligible storage, the DC-03 strings) of the
    project's saved spec, read as the DC-03 sizing and proof read it. A project
    without a DC spec or a readable sidecar has none (nothing eligible), never
    floor 0."""
    from project_templates import eh_templates as tpl
    from services.adequacy import dc_backup
    from services.adequacy.site_peak import named_load

    none: tuple[dict[str, float], frozenset[str]] = ({}, frozenset())
    if project_dir is None:
        return none
    d = pathlib.Path(project_dir)
    try:
        tid, spec, planning = dc_backup.project_spec(d, tpl)
        island = json.loads((d / "island_config.json").read_text())
    except (dc_backup.DcBackupError, OSError, ValueError):
        return none
    if not isinstance(island, dict):
        return none
    backup = spec.backup
    critical = named_load(n, dc_backup.critical_loads(island))
    out: dict[str, float] = {}
    strings: list[str] = []
    if backup.mode != "genset":
        plan = planning or tpl.NATIVE_PLANNING[tid]
        try:
            sizing = dc_backup.size_bess(critical, firm_genset_mw=_firm(n, island), backup=backup,
                                         redundancy=spec.redundancy, years=plan.analysis_years)
        except dc_backup.DcBackupError:
            sizing = None
        if sizing is not None:
            strings = [s for s in dc_backup.string_names(sizing) if s in n.storage_units.index]
            e = sizing.get("e_backup_usable_mwh_per_string", sizing.get("e_usable_mwh_per_string"))
            if e is not None and math.isfinite(float(e)) and float(e) >= 0:
                out.update({s: float(e) for s in strings})
    # No critical load in the network: P_crit is unknown, so no UPS floor (never 0).
    named = [x for x in dc_backup.critical_loads(island) if x in n.loads.index]
    peak = float(np.max(np.asarray(critical, dtype=float))) if named and np.size(critical) else math.nan
    ride = float(backup.ride_through_s)
    for s in (island.get("requirements") or {}).get("ride_through_storage") or []:
        if s in out or s not in n.storage_units.index or s in strings:
            continue
        eta = float(n.storage_units.at[s, "efficiency_dispatch"])
        floor = peak * ride / 3600.0 / eta if eta > 0 else math.nan
        if math.isfinite(floor) and floor >= 0:
            out[str(s)] = floor
    return out, frozenset(strings)


def _firm(n, island) -> float:
    from services.adequacy.site_peak import firm_genset_mw

    g, _ = firm_genset_mw(n, island)
    return g


def project_backup(n) -> tuple[dict[str, float], frozenset[str]]:
    """``derive_backup`` for the project being solved (none when unsaved)."""
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.solve_target_context()
    storage = getattr(ctx, "storage_dir", None) if ctx is not None else None
    return derive_backup(n, storage) if storage else ({}, frozenset())


# ── the plan (every refusal before the solve) ────────────────────────────────


def _static(n, name: str, attr: str, default: float) -> float:
    df = n.storage_units
    v = df.at[name, attr] if attr in df.columns else default
    return default if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def plan(n, products: list["ReserveProduct"], floors: dict[str, float],
         strings: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Check the products against ``n`` and the floors; return what the rows need.
    Raises ``ReserveRefusal`` (typed) on anything the rows cannot hold."""
    from models.reserve_products import check_unique

    check_unique(products)
    eligible = {s: f for s, f in floors.items()
                if s in n.storage_units.index and isinstance(f, (int, float)) and math.isfinite(f) and f >= 0}
    on: dict[str, list[str]] = {}
    prices: dict[str, pd.Series] = {}
    for p in products:
        named = list(p.storages) or sorted(eligible)
        bad = [s for s in named if s not in eligible]
        if bad or not named:
            raise ReserveRefusal(NOT_ELIGIBLE, (
                f"reserve product {p.id!r}: {bad or 'no storage'} cannot carry reserves. Only backup "
                "storage with a readable backup floor can: the DC-03 strings of a bess or hybrid "
                "project, or a storage its island sidecar lists as ride-through storage"))
        price, flags = bound_price(n, p)
        if price is None:
            kind = PRICE_STALE if "reference_changed_since_binding" in flags else PRICE_MISSING
            raise ReserveRefusal(kind, (
                f"reserve product {p.id!r}: its capacity price series is not bound to this network "
                f"({', '.join(flags)}); save the commercial config again to bind it"))
        if not np.isfinite(price.to_numpy(dtype=float)).all():
            raise ReserveRefusal(PRICE_MISSING, f"reserve product {p.id!r}: its price has a gap")
        on[p.id] = named
        prices[p.id] = price
    used = sorted({s for names in on.values() for s in names})
    for s in used:
        floor = eligible[s]
        ext = bool(n.storage_units.at[s, "p_nom_extendable"])
        p_cap = _static(n, s, "p_nom_max", math.inf) if ext else _static(n, s, "p_nom", 0.0)
        e_cap = _static(n, s, "max_hours", 0.0) * p_cap
        if floor > e_cap + TOL:
            raise ReserveRefusal(FLOOR_EXCEEDS, (
                f"storage {s!r} holds at most {e_cap:.3f} MWh but its backup floor is {floor:.3f} MWh: "
                "it cannot keep its backup energy, let alone commit a reserve"))
        cyclic = bool(n.storage_units.at[s, "cyclic_state_of_charge"])
        per_period = bool(n.storage_units.at[s, "cyclic_state_of_charge_per_period"]) \
            if "cyclic_state_of_charge_per_period" in n.storage_units.columns else False
        if not (cyclic or per_period):
            init = _static(n, s, "state_of_charge_initial", 0.0)
            if init < floor - TOL:
                raise ReserveRefusal(INITIAL_SOC, (
                    f"storage {s!r} starts at {init:.3f} MWh, below its backup floor of {floor:.3f} MWh: "
                    "raise state_of_charge_initial or make it cyclic"))
        series = n.storage_units_t.p_max_pu.get(s) if s in strings else None
        if s in strings and (series is None or (series.to_numpy(dtype=float) != 0.0).any()):
            raise ReserveRefusal(STRING_AVAIL, (
                f"backup string {s!r}: its p_max_pu series is not DC-03's dispatch hold (0 in every "
                "snapshot), so its availability for a reserve is unknown; restore the hold"))
    return {"products": products, "on": on, "prices": prices,
            "floors": {s: eligible[s] for s in used}, "storages": used,
            "strings": frozenset(s for s in used if s in strings)}


# ── the rows ─────────────────────────────────────────────────────────────────


def _dense(n, attr: str, sns, names) -> pd.DataFrame:
    return n.get_switchable_as_dense("StorageUnit", attr, sns)[names]


def _da(frame: pd.DataFrame | pd.Series, coord) -> xr.DataArray:
    if isinstance(frame, pd.Series):
        return xr.DataArray(frame.to_numpy(dtype=float), coords=[coord], dims=["snapshot"])
    return xr.DataArray(frame.to_numpy(dtype=float), coords=[coord, list(frame.columns)],
                        dims=["snapshot", "name"])


def _p_nom(m, n, s: str):
    if bool(n.storage_units.at[s, "p_nom_extendable"]):
        return m["StorageUnit-p_nom"].sel(name=s)
    return float(n.storage_units.at[s, "p_nom"])


def _initial_at(n, s: str, sns) -> pd.Series:
    """1.0 where PyPSA's balance starts ``s`` from ``state_of_charge_initial``
    (its ``include_previous_soc`` is False), else 0.0, as
    ``pypsa.optimization.constraints.define_storage_unit_constraints`` decides:
    a non-cyclic storage's first snapshot; with investment periods, each
    period's first when it resets per period and does not cycle per period."""
    df = n.storage_units
    flag = lambda col: bool(df.at[s, col]) if col in df.columns else False  # noqa: E731
    out = pd.Series(0.0, index=sns)
    multi = getattr(n, "_multi_invest", False) and isinstance(sns, pd.MultiIndex)
    if multi and (flag("cyclic_state_of_charge_per_period") or flag("state_of_charge_initial_per_period")):
        if flag("cyclic_state_of_charge_per_period"):
            return out
        firsts = ~pd.Series(sns.get_level_values(0), index=sns).duplicated()
        out[firsts.to_numpy()] = 1.0
        return out
    if not flag("cyclic_state_of_charge") and len(out):
        out.iloc[0] = 1.0
    return out


def add_rows(n, sns, the_plan: dict, emit=lambda _m: None) -> int:
    m = n.model
    soc_all = m["StorageUnit-state_of_charge"]
    coord = soc_all.indexes["snapshot"]
    sns = coord
    names = the_plan["storages"]
    pairs = [(k.id, s) for k in the_plan["products"] for s in the_plan["on"][k.id]]
    cols = pd.Index([f"{s}{SEP}{k}" for k, s in pairs], name="reserve")
    r = m.add_variables(lower=0.0, coords=[coord, cols], name="Reserve-r")
    by = {k.id: k for k in the_plan["products"]}
    eh = n.snapshot_weightings.stores.loc[sns].astype(float)
    eh_da = _da(eh, coord)
    eff_d = _dense(n, "efficiency_dispatch", sns, names)
    eff_s = _dense(n, "efficiency_store", sns, names)
    p_max = _dense(n, "p_max_pu", sns, names)
    p_min = _dense(n, "p_min_pu", sns, names)
    inflow = _dense(n, "inflow", sns, names)
    stand = _dense(n, "standing_loss", sns, names)
    has_spill = "StorageUnit-spill" in m.variables
    spill_names = set(m["StorageUnit-spill"].indexes["name"]) if has_spill else set()
    added = 0
    for s in names:
        up = [f"{s}{SEP}{k}" for k, b in pairs if b == s and by[k].up]
        dn = [f"{s}{SEP}{k}" for k, b in pairs if b == s and by[k].down]
        d_up = xr.DataArray([by[c.split(SEP)[1]].sustain_h for c in up], coords=[pd.Index(up, name="reserve")])
        d_dn = xr.DataArray([by[c.split(SEP)[1]].sustain_h for c in dn], coords=[pd.Index(dn, name="reserve")])
        p_nom = _p_nom(m, n, s)
        dis, sto = m["StorageUnit-p_dispatch"].sel(name=s), m["StorageUnit-p_store"].sel(name=s)
        soc = soc_all.sel(name=s)
        ed, es = _da(eff_d[s], coord), _da(eff_s[s], coord)
        # soc(t-1) through PyPSA's own balance (module docstring). Where PyPSA
        # starts from state_of_charge_initial, it applies no standing loss to it.
        keep = ((1.0 - _da(stand[s], coord)) ** eh_da).where(~_da(_initial_at(n, s, sns), coord).astype(bool), 1.0)
        prev = (soc + eh_da / ed * dis - eh_da * es * sto - eh_da * _da(inflow[s], coord)
                + (eh_da * m["StorageUnit-spill"].sel(name=s) if s in spill_names else 0)) / keep
        e_max = float(n.storage_units.at[s, "max_hours"]) * p_nom
        floor = the_plan["floors"][s]
        need = 0
        if up:
            r_up = r.sel(reserve=up)
            avail = (pd.Series(_static(n, s, "p_max_pu", 1.0), index=sns)
                     if s in the_plan["strings"] else p_max[s])
            m.add_constraints(dis - sto + r_up.sum("reserve") - p_nom * _da(avail, coord) <= 0,
                              name=f"Reserve-up-power-{s}")
            need = (r_up * d_up).sum("reserve") / ed
            added += 1
        # The floor holds on every committing storage, a down-only one too.
        m.add_constraints(soc - need >= floor, name=f"Reserve-floor-{s}")
        m.add_constraints(prev - need >= floor, name=f"Reserve-floor-prev-{s}")
        added += 2
        if dn:
            r_dn = r.sel(reserve=dn)
            m.add_constraints(sto - dis + r_dn.sum("reserve") + p_nom * _da(p_min[s], coord) <= 0,
                              name=f"Reserve-down-power-{s}")
            room = (r_dn * d_dn).sum("reserve") * es
            m.add_constraints(soc + room - e_max <= 0, name=f"Reserve-down-energy-{s}")
            m.add_constraints(prev + room - e_max <= 0, name=f"Reserve-down-energy-prev-{s}")
            added += 3
    hours, period = reserve_weights(n, sns)
    w = hours * period
    revenue = 0
    for k in the_plan["products"]:
        cols_k = [f"{s}{SEP}{k.id}" for s in the_plan["on"][k.id]]
        total = r.sel(reserve=cols_k).sum("reserve")
        if k.max_commit_mw is not None:
            m.add_constraints(total <= k.max_commit_mw, name=f"Reserve-cap-{k.id}")
            added += 1
        coef = _da(the_plan["prices"][k.id].reindex(sns) * w, coord)
        revenue = revenue + (total * coef).sum()
    m.objective -= revenue
    emit(f"{len(pairs)} commitment(s) on {len(names)} storage(s), {added} row(s)")
    return added


# ── the solved commitments ───────────────────────────────────────────────────


class _Record:
    """The commitments of every LP the wrap built (rolling and myopic build one per
    window or period), read from each model before PyPSA drops it."""

    def __init__(self, products: list[str], digest: str | None = None):
        self.products = products
        self.digest = digest
        self.frames: list[pd.DataFrame] = []
        self._pending = None

    def read(self) -> None:
        if self._pending is None:
            return
        model, self._pending = self._pending, None
        try:
            sol = model.variables["Reserve-r"].solution.to_pandas()
        except Exception:  # noqa: BLE001 — never solved, or the solve failed
            return
        if np.isfinite(sol.to_numpy(dtype=float)).all():
            self.frames.append(sol)

    def hold(self, model) -> None:
        self.read()
        self._pending = model


RECORD_ATTR = "_ic_reserve_record"


def clear_solution(n) -> None:
    """Drop a previous run's commitments (before a solve). The record this run's
    wrap set at LP-chain build stays: the chain is built before this runs."""
    frame = n.storage_units_t.get(OUT_FRAME) if hasattr(n.storage_units_t, "get") else None
    if frame is not None:
        del n.storage_units_t[OUT_FRAME]
    if isinstance(getattr(n, "meta", None), dict):
        n.meta.pop(META_KEY, None)


def persist_solution(n) -> bool:
    """After a successful solve: the commitments as ``storage_units_t[OUT_FRAME]``
    and ``meta[META_KEY]``. False when this run had no reserve rows."""
    from services.commercial.settlement_inputs import axis_hash

    rec = getattr(n, RECORD_ATTR, None)
    if rec is None:
        return False
    rec.read()
    delattr(n, RECORD_ATTR)
    if not rec.frames:
        return False
    frame = pd.concat(rec.frames)
    frame = frame[~frame.index.duplicated(keep="last")].reindex(n.snapshots)
    frame.columns = [str(c) for c in frame.columns]
    n.storage_units_t[OUT_FRAME] = frame.astype(float)
    n.meta[META_KEY] = {"products": rec.products, "axis_hash": axis_hash(n.snapshots),
                        "inputs": rec.digest}
    return True


def solved(n, digest: str | None = None) -> tuple[pd.DataFrame | None, list[str]]:
    """The persisted commitments (MW, columns ``<storage>|<product>``), or None + why.
    ``digest`` (``inputs_digest`` of the current products and floors): a solve
    that read other inputs is stale, never read."""
    from services.commercial.settlement_inputs import axis_hash

    meta = (getattr(n, "meta", None) or {}).get(META_KEY)
    frame = n.storage_units_t.get(OUT_FRAME) if hasattr(n.storage_units_t, "get") else None
    if not meta or frame is None or frame.empty:
        return None, ["reserve_not_solved"]
    if meta.get("axis_hash") != axis_hash(n.snapshots):
        return None, ["reserve_not_solved"]
    if digest is not None and meta.get("inputs") != digest:
        return None, ["reserve_not_solved", "reserve_inputs_changed_since_solve"]
    return frame, []


def objective_revenue(n) -> float:
    """The revenue the LP's objective subtracted for the persisted commitments,
    Σ hours x period_w x price x r (0 without a reserve solve): the objective
    decomposition's named term, so it never reads as an unexplained gap."""
    frame, _ = solved(n)
    meta = (getattr(n, "meta", None) or {}).get(META_KEY) or {}
    prices = n.buses_t.get(PRICE_FRAME) if hasattr(n.buses_t, "get") else None
    if frame is None or prices is None:
        return 0.0
    hours, period = reserve_weights(n)
    w = (hours * period).to_numpy(dtype=float)
    total = 0.0
    for pid in meta.get("products") or []:
        cols = [c for c in frame.columns if c.rpartition(SEP)[2] == pid]
        if not cols or price_column(pid) not in prices.columns:
            continue
        pr = prices[price_column(pid)].reindex(n.snapshots).to_numpy(dtype=float)
        total += float(np.nansum(w * pr * frame[cols].sum(axis=1).to_numpy(dtype=float)))
    return total


# ── the wrap ─────────────────────────────────────────────────────────────────


def _wrap_with_reserve_products(network: "pypsa.Network", user_fn, cfg, backup=_UNSET, log_queue=None):
    """Chain the reserve rows after ``user_fn``; ``user_fn`` unchanged when the config
    has no reserve products. Raises ``ReserveRefusal`` before the solve. ``backup``
    defaults to the solving project's ``(floors, strings)``."""
    products = products_of(cfg)
    if hasattr(network, RECORD_ATTR):
        delattr(network, RECORD_ATTR)  # a failed run's record never reaches this run's persist
    if not products:
        return user_fn
    floors, strings = project_backup(network) if backup is _UNSET else backup
    the_plan = plan(network, products, floors, strings)
    record = _Record([p.id for p in products], inputs_digest(products, floors))
    setattr(network, RECORD_ATTR, record)

    def emit(msg: str) -> None:
        _safe_log(log_queue, f"[RESERVE] {msg}")

    def reserve_products_fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        add_rows(n, snapshots, the_plan, emit)
        record.hold(n.model)

    return reserve_products_fn
