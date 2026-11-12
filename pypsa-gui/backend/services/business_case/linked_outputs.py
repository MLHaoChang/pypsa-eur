"""
Linked model outputs and their unit conversions (BC-ALL slice 04, spec §5, GK A LO-1..LO-10 and P1..P8).

A line whose quantity source is `linked:<id>@<version>` (`models.business_case`) reads its quantity from the
project's own solve. This module is the catalogue of what a project offers and the one reader of each output.

Post-solve and read-only (LO-9): nothing here enters the LP, the investment case, its hash or the adequacy
facts, and no engine imports this package.

Ids are a stored contract, versioned by `CATALOGUE_VERSION`. An id is `<kind>.<name>` (`capacity.<Component>.<name>`),
in the characters 02's `QUANTITY_SOURCE_PATTERN` allows; a component whose name falls outside them is not offered.

Every quantity is per year of an investment period: Σ x · snapshot_weights("generators") over the period ÷ the
period's years, by `finance_case._per_year`, the helper `ProductShortfall` uses. A flat network has the one
period key `None`. Sums are per year and never clipped per snapshot: a slack filling a store can exceed the
hour's load.

Units (G5, LO-1) convert here and nowhere else, from the constants the code already has: the bus's `mwh_per_t`
through `process_products.t_per_unit`, `models.commercial`'s `lhv_kwh_per_kg` or the line's stated LHV, and the
preset row's kW per GPU. The one new constant is `O2_KG_PER_KG_H2`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from models.business_case import LinkedOutputMissing, UnitNotAllowed

CATALOGUE_VERSION = "1"
#: Every catalogue version a stored line may carry; each has a reader for every kind (#1132 F1).
VERSIONS: tuple[str, ...] = (CATALOGUE_VERSION,)

#: kg of O2 co-produced per kg of H2 by water electrolysis: 2 H2O → 2 H2 + O2, so M(O2) / (2 · M(H2)) =
#: 31.998 / (2 · 2.016) = 7.94 (GK A; the physical constant, never a preset's assumed value).
O2_KG_PER_KG_H2 = 7.94

_KWH_PER_MWH = 1000.0
_KW_PER_MW = 1000.0
_KG_PER_T = 1000.0
_MONTHS_PER_YEAR = 12.0
_NAME = re.compile(r"[A-Za-z0-9_.\-]+")
_H2_CARRIER = "H2"
_CAPACITY_COMPONENTS = {
    "Generator": "generators",
    "Link": "links",
    "StorageUnit": "storage_units",
}
_PRODUCT_KINDS = ("served_product", "served_h2", "shipped")
#: The bill streams, each of which prices the site's grid import (never a served load): the premise of
#: `ledger_clash`'s served-load verdict (GK B ruling (a), C1). A test pins it to the finance report's
#: `ENERGY_BILL_STREAMS`.
GRID_IMPORT_BILL_STREAMS = (
    "certificates",
    "demand_charge",
    "energy_import",
    "network_capacity",
    "network_energy",
    "retail_fixed",
    "tax",
)


@dataclass(frozen=True, eq=False)
class LinkedSolve:
    """The run's own solve, as the readers need it (LO-7). Build it with `bind(ctx)`; `fresh` is that check's
    verdict, never the network's own stamp."""

    network: Any
    fresh: bool
    #: The solve's `last_lost_load` record: the VoLL-slack and lost-production captures.
    lost_load: dict | None = None
    #: The solve's VoLL. Only an explicit 0 shows that the LP added no transient slack.
    voll: float | None = None
    #: The commercial config's `export_link` (the poc→grid Link).
    export_link: str | None = None
    #: `(DataCentreLoadSpec, timezone)` on a data-centre project.
    dc: tuple | None = None


def bind(ctx, *, dc: tuple | None = None) -> LinkedSolve:
    """The live solve of a project context. Call under the network lock.

    `fresh` is `harness.calculations.solve_matches_inputs`: the solve completed optimal, the live frames are
    the LP's (no AC power flow or LOPF overlay) and both recorded fingerprints equal the current inputs,
    re-hashed now."""
    from harness.calculations import solve_matches_inputs

    state = ctx.solver_state
    cfg = state.get("solver_config")
    voll = _field(cfg, "voll")
    return LinkedSolve(
        network=ctx.network,
        fresh=bool(solve_matches_inputs(ctx)),
        lost_load=state.get("last_lost_load"),
        voll=None if voll is None else float(voll),
        export_link=_field(_field(cfg, "commercial"), "export_link"),
        dc=dc,
    )


def _field(obj, name):
    """A field of a config that is a mapping (as saved) or an object (`SolverConfig`, as held live)."""
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


@dataclass(frozen=True)
class OutputSpec:
    id: str
    label: str
    #: The line units this output can be read in (`models.business_case.Unit`).
    units: tuple[str, ...]
    #: Not a metered quantity of the model: computed from one (L3).
    derived: bool = False

    @property
    def kind(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.id.split(".", 1)[1] if "." in self.id else ""


@dataclass(frozen=True)
class Series:
    """A linked quantity per year, by investment period (`None` on a flat network)."""

    output_id: str
    unit: str
    per_period: dict
    #: Years each period stands for; empty on a flat network.
    years: dict
    derived: bool = False

    def for_year(self, year: int) -> float | None:
        """The quantity of calendar `year`: its period's, or the flat network's one value; None outside
        every period."""
        if None in self.per_period:
            return self.per_period[None]
        started = [p for p in self.per_period if p <= year]
        if not started:
            return None
        p = max(started)
        return self.per_period[p] if year < p + self.years.get(p, 1.0) else None


@dataclass(frozen=True)
class NotEvaluated:
    """The output cannot be read from this solve; never 0 (LO-7)."""

    output_id: str
    reason: str


class _Unread(Exception):
    """A read that has no value; `read` turns it into `NotEvaluated`."""


# ── the network, read ────────────────────────────────────────────────────────


def _periods(n) -> list:
    from services.results.finance_case import _periods as periods

    return periods(n)


def _per_period(n, x: pd.Series) -> dict:
    from services.results.finance_case import _per_year

    return {p: _per_year(n, x, p) for p in _periods(n)}


def _offerable(name) -> bool:
    return bool(_NAME.fullmatch(str(name)))


def _bus_carrier(n, bus) -> str:
    return (
        str(n.buses.at[bus, "carrier"])
        if bus in n.buses.index and "carrier" in n.buses.columns
        else ""
    )


def _loads_on(n, bus) -> list[str]:
    return [str(load) for load in n.loads.index[n.loads["bus"].astype(str) == str(bus)]]


def _capture_frame(n, solve: LinkedSolve) -> pd.DataFrame:
    """The solve's VoLL-slack dispatch per Load (P3). The slacks are removed after the solve, so an absent
    capture is not "nothing unserved": only a solve whose VoLL is an explicit 0 added none."""
    frame = (
        (solve.lost_load or {}).get("lost_load_t")
        if isinstance(solve.lost_load, dict)
        else None
    )
    if frame is None:
        if solve.voll == 0:
            return pd.DataFrame(index=n.snapshots)
        raise _Unread("no_lost_load_capture")
    if not isinstance(frame, pd.DataFrame):
        raise _Unread("lost_load_capture_other_solve")
    known = set(map(str, n.loads.index)) | set(map(str, n.buses.index))
    if not frame.index.equals(n.snapshots) or not set(map(str, frame.columns)) <= known:
        raise _Unread("lost_load_capture_other_solve")
    return frame


def _unserved(n, solve: LinkedSolve, bus: str) -> pd.DataFrame:
    """The unserved MW of each Load on `bus`, per snapshot. Each slack MW counts once (P4):

    - a Load's own VoLL slack, from the capture, or still a Generator row when the capture has no such column;
    - an involuntary slack of the bus itself (`slack.involuntary_slack_mask`), split across the bus's Loads
      pro rata by p_set(t), as the DC Q4 rule does on `dc_mv` (LO-3);
    - the bus's lost-production slack, `process_products.shortfalls`, split the same way (LO-2). Any bus with
      a `lost_production_value` gets one (`services.solver.lost_production`), not only a product bus.

    Curtailment, demand response and buy-in are not unserved. In an hour where the bus's Loads sum to zero
    (a slack filling a store), the bus slack is split by each Load's share of the period's Σ p_set · w, so
    none is lost and a Load that draws nothing takes none; with no Load drawing in the whole period it is
    not evaluated. A missing value in a capture column this read uses is not evaluated, never 0."""
    from services.adequacy.slack import involuntary_slack_mask, strip_slack_prefix
    from services.process_products import generator_role, shortfalls

    loads = _loads_on(n, bus)
    out = pd.DataFrame(0.0, index=n.snapshots, columns=loads)
    shared = pd.Series(0.0, index=n.snapshots)
    capture = _capture_frame(n, solve)
    seen: set[str] = set()
    for col in capture.columns:
        key = str(col)
        seen.add(key)
        used = key in loads or (key == str(bus) and key not in n.loads.index)
        if used and capture[col].isna().any():
            raise _Unread("lost_load_capture_not_finite")
        if key in loads:
            out[key] += capture[col]
        elif used:
            shared += capture[col]
    gens = n.generators
    p = n.generators_t.p
    for g in gens.index[
        involuntary_slack_mask(gens) & (gens["bus"].astype(str) == str(bus))
    ]:
        key = strip_slack_prefix(str(g))
        if key in seen or g not in p.columns or generator_role(n, g)[0]:
            continue  # a product-role Generator is read by `shortfalls`, below
        if key in loads:
            out[key] += p[g].reindex(n.snapshots).fillna(0.0)
        else:
            shared += p[g].reindex(n.snapshots).fillna(0.0)
    for s in shortfalls(n, solve.lost_load):
        if s.bus != str(bus):
            continue
        if s.p is None or (s.flag or "").startswith("lost_production_capture"):
            raise _Unread(s.flag or "lost_production_not_read")
        shared += s.p.reindex(n.snapshots).fillna(0.0)
    return out + _split(n, loads, shared) if loads else out


def _split(n, loads: list[str], shared: pd.Series) -> pd.DataFrame:
    """A bus slack `shared` (MW per snapshot) split across the bus's `loads` pro rata by p_set(t) (LO-3, the
    DC Q4 rule). In an hour where the Loads sum to zero (a slack filling a store), by each Load's share of the
    period's Σ p_set · w, so none is lost; with no Load drawing in the whole period it is not evaluated."""
    # A share is of what the Loads draw: a negative (feeding) or missing p_set takes none.
    p_set = (
        n.get_switchable_as_dense("Load", "p_set")[loads].fillna(0.0).clip(lower=0.0)
    )
    total = p_set.sum(axis=1)
    share = p_set.div(total.where(total != 0), axis=0)
    idle = total == 0
    if idle.any():
        period = (
            n.snapshots.get_level_values(0)
            if isinstance(n.snapshots, pd.MultiIndex)
            else pd.Index([0] * len(n.snapshots))
        )
        weighted = p_set.mul(n.snapshot_weightings.generators, axis=0)
        annual = weighted.groupby(period.to_numpy()).transform("sum")
        annual_total = annual.sum(axis=1)
        by_period = annual.div(annual_total.where(annual_total != 0), axis=0)
        share.loc[idle] = by_period.loc[idle]
        if share[shared != 0].isna().any().any():
            raise _Unread("unserved_no_load_to_split")
    return share.fillna(0.0).mul(shared, axis=0)


def _served(n, solve: LinkedSolve, loads: list[str], bus: str) -> dict:
    """Σ (p_set − unserved) per year of each period, over `loads` of one bus.

    The identity (P4, F7): served + ProductShortfall's quantity on the bus + the Loads' own VoLL slack =
    contracted (Σ p_set · w ÷ years). A VoLL slack on a product Load lowers served, but `ProductShortfall`
    does not count it."""
    p_set = n.get_switchable_as_dense("Load", "p_set")[loads].sum(axis=1)
    return _per_period(n, p_set - _unserved(n, solve, bus)[loads].sum(axis=1))


def _lhv(stated: float | None) -> float:
    """The kWh per kg of H2: the line's stated LHV, else `models.commercial`'s default (LO-1)."""
    if stated is not None:
        if not (math.isfinite(stated) and stated > 0):
            raise _Unread("lhv_not_positive")
        return float(stated)
    from models.commercial import ReferencePurchase

    return float(ReferencePurchase.model_fields["lhv_kwh_per_kg"].default)


def _electrolysers(n) -> list[str]:
    """The electrolyser fleet (`run_gates._electrolysers`: by `eh_role`, else by carrier) feeding an H2 bus."""
    from services.run_gates import _electrolysers as fleet

    return [
        link
        for link in fleet(n)
        if _bus_carrier(n, n.links.at[link, "bus1"]) == _H2_CARRIER
    ]


# ── the readers: (n, solve, name, unit, params) → {period: quantity per year} ────────────


def _read_served_product(n, solve, bus, unit, params) -> dict:
    from services.process_products import bus_unit, t_per_unit

    served = _served(n, solve, _loads_on(n, bus), bus)
    if unit == "t" and bus_unit(n, bus) != "t":
        factor = t_per_unit(n, bus)
        return {p: v * factor for p, v in served.items()}
    return served


def _read_served_h2(n, solve, load, unit, params) -> dict:
    served = _served(n, solve, [load], str(n.loads.at[load, "bus"]))
    if unit == "kg":
        per_mwh = _KWH_PER_MWH / _lhv(params.get("lhv_kwh_per_kg"))
        return {p: v * per_mwh for p, v in served.items()}
    return served


def _read_shipped(n, solve, load, unit, params) -> dict:
    return _served(n, solve, [load], str(n.loads.at[load, "bus"]))


def _read_derived_o2(n, solve, link, unit, params) -> dict:
    p1 = n.links_t.p1
    if link not in p1.columns:
        raise _Unread("link_not_dispatched")
    t_per_mwh = (
        _KWH_PER_MWH / _lhv(params.get("lhv_kwh_per_kg")) * O2_KG_PER_KG_H2 / _KG_PER_T
    )
    return {p: v * t_per_mwh for p, v in _per_period(n, -p1[link]).items()}


def _read_served_load(n, solve, load, unit, params) -> dict:
    return _served(n, solve, [load], str(n.loads.at[load, "bus"]))


def _read_export(n, solve, link, unit, params) -> dict:
    """Energy delivered at the grid side (bus1) of the export Link, after its losses, positive when exported
    (LO-5): Σ −p1. The billed export MWh elsewhere is p0, before losses."""
    p1 = n.links_t.p1
    if link not in p1.columns:
        raise _Unread("link_not_dispatched")
    return _per_period(n, -p1[link])


def _read_capacity(n, solve, name, unit, params) -> dict:
    """Capacity in service in each period (LO-6), by the adequacy engines' rule
    (`activity.ActivityContext.capacity_by_period`: `get_active_assets` per period, and a vintage-expanded
    asset's builds from their own years). An extendable asset reads `p_nom_opt`; a non-finite one is not
    evaluated, never `p_nom`."""
    from services.adequacy.activity import ActivityContext, period_blocks

    comp, asset = name.split(".", 1)
    row = getattr(n.components, comp).static.loc[asset]
    if bool(row.get("p_nom_extendable", False)) and not math.isfinite(
        float(row.get("p_nom_opt", float("nan")))
    ):
        raise _Unread("capacity_not_solved")
    factor = {
        "MW_year": 1.0,
        "kW_year": _KW_PER_MW,
        "kW_month": _KW_PER_MW * _MONTHS_PER_YEAR,
    }[unit]
    context = ActivityContext(n, _CAPACITY_COMPONENTS[comp], period_blocks(n.snapshots))
    by_label = dict(zip(context.labels, context.capacity_by_period(asset, row)))
    return {p: by_label["ALL" if p is None else p] * factor for p in _periods(n)}


def _facility_unserved(n, solve) -> pd.Series:
    """The involuntary unserved facility MW, by DC 03's Q4 rule (`dc_it_energy.unserved_facility_mw`: the
    slack on `it_bus` plus the cooling share of the slack on `dc_mv`), read from the solve's capture as
    well as the Generator rows: the per-Load VoLL slacks are gone from the network after the solve (P3)."""
    from services.dc_it_energy import COOLING_LOAD, IT_BUS, MV_BUS

    on_it = _unserved(n, solve, IT_BUS).sum(axis=1)
    on_mv = _unserved(n, solve, MV_BUS)
    return on_it + (on_mv[COOLING_LOAD] if COOLING_LOAD in on_mv.columns else 0.0)


def _it_periods(n, solve) -> pd.DataFrame:
    """DC 03's read, the only IT reader (LO-10), on the facility's unserved MW of this solve."""
    from services.dc_it_energy import it_energy

    spec, timezone = solve.dc
    periods = it_energy(
        spec,
        n.snapshots,
        n.snapshot_weightings.generators,
        _facility_unserved(n, solve),
        timezone=timezone,
    ).periods
    keys = _periods(n)
    if len(periods) != len(keys):
        raise _Unread("it_energy_periods_mismatch")
    if keys == [None]:
        # A flat network: DC 03 keys its one period by the snapshots' year.
        return periods.set_axis(keys)
    if len(pd.Index(keys).difference(periods.index)):
        raise _Unread("it_energy_periods_mismatch")
    return periods.reindex(keys)


def _read_it_energy(n, solve, name, unit, params) -> dict:
    """Billable IT energy: served, so the unserved IT MWh is never a quantity a line can price."""
    mwh = _it_periods(n, solve)["billable_it_mwh"]
    if unit == "GPU_h":
        kw = params.get("kw_per_gpu")
        if kw is None or not (math.isfinite(kw) and kw > 0):
            raise _Unread("kw_per_gpu_missing")
        return {p: float(v) * _KWH_PER_MWH / kw for p, v in mwh.items()}
    return {p: float(v) for p, v in mwh.items()}


def _read_contracted_it_kw(n, solve, name, unit, params) -> dict:
    return {
        p: float(v) * _MONTHS_PER_YEAR
        for p, v in _it_periods(n, solve)["contracted_it_kw"].items()
    }


_Reader = Callable[..., dict]
_READERS_V1: dict[str, _Reader] = {
    "served_product": _read_served_product,
    "served_h2": _read_served_h2,
    "shipped": _read_shipped,
    "derived_o2": _read_derived_o2,
    "served_load": _read_served_load,
    "export": _read_export,
    "capacity": _read_capacity,
    "it_energy": _read_it_energy,
    "contracted_it_kw": _read_contracted_it_kw,
}
#: The reader of each kind, per catalogue version.
READERS: dict[str, dict[str, _Reader]] = {CATALOGUE_VERSION: _READERS_V1}


# ── the catalogue ───────────────────────────────────────────────────────────


def catalogue(n, solve: LinkedSolve | None = None) -> list[OutputSpec]:
    """The outputs this project offers, from its network (and, for the export Link and the IT outputs, the
    solve's config)."""
    from services.adequacy.site_peak import electric_loads
    from services.adequacy.slack import slack_generator_mask
    from services.process_products import (
        bus_unit,
        generator_role,
        is_product_bus,
        t_per_unit,
    )

    out: list[OutputSpec] = []
    for bus in map(str, n.buses.index):
        if not (_offerable(bus) and is_product_bus(n, bus) and _loads_on(n, bus)):
            continue
        if bus_unit(n, bus) == "t":
            units: tuple[str, ...] = ("t",)
        else:
            units = ("MWh", "t") if t_per_unit(n, bus) is not None else ("MWh",)
        out.append(
            OutputSpec(f"served_product.{bus}", f"Product served on {bus}", units)
        )
    electric = set(map(str, electric_loads(n)))
    for load in map(str, n.loads.index):
        if not _offerable(load):
            continue
        bus = str(n.loads.at[load, "bus"])
        if is_product_bus(n, bus):
            if bus_unit(n, bus) == "t":
                out.append(
                    OutputSpec(f"shipped.{load}", f"Product shipped by {load}", ("t",))
                )
        elif _bus_carrier(n, bus) == _H2_CARRIER:
            out.append(
                OutputSpec(
                    f"served_h2.{load}",
                    f"Hydrogen delivered to {load}",
                    ("MWh_H2", "kg"),
                )
            )
        elif load in electric:
            out.append(
                OutputSpec(
                    f"served_load.{load}", f"Electric load served at {load}", ("MWh",)
                )
            )
    for link in _electrolysers(n):
        if _offerable(link):
            out.append(
                OutputSpec(
                    f"derived_o2.{link}",
                    f"Oxygen co-produced by {link}",
                    ("t",),
                    derived=True,
                )
            )
    export = None if solve is None else solve.export_link
    if export is not None and export in n.links.index and _offerable(export):
        out.append(
            OutputSpec(
                f"export.{export}",
                f"Energy delivered at the grid side of {export}, after link losses",
                ("MWh",),
            )
        )
    for comp in _CAPACITY_COMPONENTS:
        static = getattr(n.components, comp).static
        hidden = (
            slack_generator_mask(static)
            if comp == "Generator"
            else pd.Series(False, index=static.index)
        )
        for asset in static.index:
            if (
                hidden[asset]
                or not _offerable(asset)
                or (comp == "Generator" and generator_role(n, asset)[0])
            ):
                continue
            out.append(
                OutputSpec(
                    f"capacity.{comp}.{asset}",
                    f"Capacity in service: {asset}",
                    ("MW_year", "kW_year", "kW_month"),
                )
            )
    if solve is not None and solve.dc is not None:
        out.append(OutputSpec("it_energy", "IT energy served", ("MWh", "GPU_h")))
        out.append(
            OutputSpec("contracted_it_kw", "Contracted IT capacity", ("kW_month",))
        )
    return out


def read(
    n,
    solve: LinkedSolve,
    output_id: str,
    unit: str,
    *,
    version: str = CATALOGUE_VERSION,
    lhv_kwh_per_kg: float | None = None,
    kw_per_gpu: float | None = None,
) -> Series | NotEvaluated:
    """One output of the run's own solve, per year, in `unit`.

    Raises `LinkedOutputMissing` for an id or version this project's catalogue does not offer, and
    `UnitNotAllowed` for a unit the output does not have. A stale, unsolved or unreadable solve is
    `NotEvaluated`, never 0 (LO-7)."""
    readers = READERS.get(version)
    spec = (
        next((s for s in catalogue(n, solve) if s.id == output_id), None)
        if readers
        else None
    )
    if spec is None or spec.kind not in readers:
        raise LinkedOutputMissing(output_id)
    if unit not in spec.units:
        raise UnitNotAllowed(output_id)
    if solve.network is not n or not solve.fresh:
        return NotEvaluated(output_id, "not_own_solve")
    from services.period_utils import period_years_map

    params = {"lhv_kwh_per_kg": lhv_kwh_per_kg, "kw_per_gpu": kw_per_gpu}
    try:
        per_period = readers[spec.kind](n, solve, spec.name, unit, params)
    except _Unread as exc:
        return NotEvaluated(output_id, str(exc))
    if any(v is None or not math.isfinite(v) for v in per_period.values()):
        return NotEvaluated(output_id, "not_finite")
    return Series(output_id, unit, per_period, dict(period_years_map(n)), spec.derived)


def resolve(line, n, solve: LinkedSolve, **params) -> Series | NotEvaluated:
    """The series a `linked:<id>@<version>` line reads, in the line's unit."""
    output_id, _, version = line.quantity_source.split(":", 1)[1].rpartition("@")
    return read(n, solve, output_id, line.unit, version=version, **params)


# ── LO-4 / spec G2, G3: what the IC ledger already values ────────────────────────


def ledger_clash(output_id: str, valued_streams) -> tuple[str, str] | None:
    """Whether a line linked to `output_id` meets a stream the IC ledger already values.

    `("refuse", stream)`: the line is refused `double_count:<stream>` (G2: an export quantity against the
    export price or a PPA/CfD settlement). `("drop", "lost_production")`: the line stands and the business
    case drops that bus's `lost_production` (G3), so a shortfall counts once, as lower served revenue.

    A served electric load (L4) meets no bill stream: every bill stream prices the site's grid import
    (`GRID_IMPORT_BILL_STREAMS`, pinned against the finance report's bill streams), which is no catalogue
    output, and a served load is what the plant sells to its customers (a microgrid's tariff revenue). It
    meets a valued `eaas_fee`, which prices the energy delivered to the customer: refused (GK B ruling (a),
    fail closed). The ruling holds only while every bill stream prices grid import: a future bill item that
    prices the load joins `served_load`'s clash set here, and the pin fails until it does."""
    valued = set(valued_streams)
    kind = output_id.split(".", 1)[0]
    if kind == "export":
        streams: tuple[str, ...] = ("energy_export", "ppa_settlement", "cfd_settlement")
    elif kind == "served_load":
        streams = ("eaas_fee",)
    elif kind in _PRODUCT_KINDS:
        return ("drop", "lost_production") if "lost_production" in valued else None
    else:
        return None
    hit = next((s for s in streams if s in valued), None)
    return None if hit is None else ("refuse", hit)


# ── the lost-production map (slice 06, G3) ───────────────────────────────────


@dataclass(frozen=True)
class LostProductionMap:
    """What the business case needs to drop a product bus's `lost_production` (G3) when a revenue line reads its
    served quantity.

    - `slack_bus`: each `lost_production` line's slack and its bus.
    - `load_bus`: each Load on those buses.
    - `share[slack][load]`: the part of the slack's lost product the LO-3 split gives `load` (the split L1 and L2
      subtract from p_set, so the drop is exactly what the line no longer earns): Σ w · split(p)[load] ÷ Σ w · p
      within each period, 0 in a period with no shortfall (nothing to drop).
    - `unread`: each bus the solve cannot give a share for, with the reason: a slack the ledger and
      `process_products.shortfalls` do not both name, an unreadable capture, a shortfall no Load draws in a
      period, or a stale solve. Never 0 (LO-7): a revenue line on such a bus is refused."""

    slack_bus: dict
    load_bus: dict
    share: dict
    unread: dict


def lost_production(n, solve: LinkedSolve, ic_case) -> LostProductionMap:
    """The `LostProductionMap` of the IC case's `lost_production` lines on the run's own solve. A slack maps to
    its bus through `finance_case._lost_production_slacks` (GK A G3-1: the one helper, a captured slack with no
    Generator too), fed the case's template lines as its ledger; its dispatch is `process_products.shortfalls`'s
    (A2: one slack set). Post-solve only: it reads the network and changes nothing (LO-9)."""
    from services.period_utils import period_years_map
    from services.process_products import shortfalls
    from services.results.finance_case import _lost_production_slacks

    ledger = {"": {ln.key: ln for t in ic_case.templates for ln in t.lines}}
    slack_bus = dict(sorted(_lost_production_slacks(n, None, ledger).items()))
    own = solve.network is n and solve.fresh
    found = {s.name: s for s in shortfalls(n, solve.lost_load)} if own else {}
    unread: dict = {}
    for s in found.values():
        if s.name not in slack_bus:
            unread.setdefault(s.bus, "lost_production_not_in_ledger")
    years = dict(period_years_map(n))
    share: dict = {}
    load_bus: dict = {}
    for slack, bus in slack_bus.items():
        loads = _loads_on(n, bus)
        load_bus.update({ld: bus for ld in loads})
        s = found.get(slack)
        try:
            if not own:
                raise _Unread("not_own_solve")
            if s is None or s.bus != bus:
                raise _Unread("lost_production_not_read")
            if s.p is None or (s.flag or "").startswith("lost_production_capture"):
                raise _Unread(s.flag or "lost_production_not_read")
            p = s.p.reindex(n.snapshots).fillna(0.0)
            split = _split(n, loads, p)
            total = _per_period(n, p)
            share[slack] = {}
            for ld in loads:
                part = _per_period(n, split[ld])
                ratio = {k: (part[k] / v if v else 0.0) for k, v in total.items()}
                share[slack][ld] = Series(
                    f"lost_production.{slack}", "share", ratio, years
                )
        except _Unread as exc:
            unread.setdefault(bus, str(exc))
    return LostProductionMap(
        slack_bus=slack_bus, load_bus=load_bus, share=share, unread=unread
    )
