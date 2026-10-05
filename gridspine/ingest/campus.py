"""A campus electrical network from one description file (plan C1).

The capacity-expansion model sizes a campus's assets: data-centre load,
BESS, PV, gensets. It does not model the electrical plant between them and
the grid. This module turns a campus description into a pandapower net, so
the AC load flow, transformer loading and IEC 60909 stages can run on it.

The description is one YAML mapping under ``campus``:

``pcc``
    The point of common coupling. It names the bus and its nominal kV and
    carries the grid operator's figures: ``sk_max_mva`` and ``sk_min_mva``
    (Sk''), ``rx_max`` and ``rx_min`` (R/X), and the set-point ``vm_pu``.
    It becomes an ``ext_grid`` named ``GRID``.

``buses``
    The other buses, ``{name: {vn_kv}}``. Nominal voltage is a definition,
    not a measurement, so it is not tagged.

``transformers``
    ``{name: {hv_bus, lv_bus, sn_mva, vn_hv_kv, vn_lv_kv, vk_percent,
    vkr_percent, pfe_kw, i0_percent}}``.

``cables``
    ``{name: {from_bus, to_bus, length_km, r_ohm_per_km, x_ohm_per_km,
    c_nf_per_km, max_i_ka}}``.

``units``
    ``{name: {kind, bus, ...}}``, with ``kind`` one of ``UNIT_KINDS``:

    * ``load``: ``p_mw`` (the rating) and ``pf`` (lagging).
    * ``bess``, ``pv``, ``wind``: ``p_mw``, the inverter ``s_mva``, and
      ``k_sc`` and ``rx_sc`` for IEC 60909. A ``bess`` also needs ``e_mwh``.
    * ``genset``: ``p_mw``, ``s_mva``, ``xd_pp`` and ``rx_sc``.

**Every number is ``{value, source}``**, with ``source`` one of
``measured``, ``datasheet`` or ``assumed``. This follows the unit templates:
a sizing report must be able to say how sure each input is, so an untagged
number is refused rather than defaulted.

What becomes what:

* Loads become pandapower ``load`` rows at their rating and power factor.
* Every generating unit becomes an ``sgen``, a PQ injection, at its rating.
  A ``bess`` starts idle (``p_mw = 0``). The hour's dispatch sets the real
  values later (plan C2).
* A ``genset`` is also an ``sgen``. On a campus it runs in power-factor
  control, not voltage control, which is why it is not a pandapower
  ``gen``. For IEC 60909 it is screened as a current source of ``1/x''d``
  times its rated current. That is a screening approximation of a
  synchronous machine, and it is ledgered in ``CAMPUS_LEDGER``.
* The net's base is 1 MVA. Results are in physical units, so the base only
  sets the solver's numerical scale, and 1 MVA suits a campus.

Refused at load, each with a message naming the field:

* an unknown key or field;
* an unknown kind;
* a missing or untagged value;
* a bus that does not exist, or that cannot be reached from the PCC;
* a transformer winding more than 10 % off its bus voltage;
* a cable between two nominal voltages;
* an inverter whose MVA is below its MW;
* ``sk_min`` above ``sk_max``;
* duplicate names;
* a name outside the canonical charset or 12-character cap
  (``schema/network.py``).

Any element may carry ``pypsa_name``, the name it has in the capacity-
expansion project. It is a plain string and not a value, so it is untagged.
It is how the project's hourly results find the element (plan C2).

yaml/pandas/pandapower only, like the rest of ``ingest/``; never the unsafe
full ``yaml.load``.
"""
import dataclasses
import math
from collections import deque
from pathlib import Path

import pandapower as pp
import pandas as pd
import yaml

from gridspine.schema.contracts import ContractError
from gridspine.schema.network import validate_canonical
from gridspine.static.shortcircuit import LINE_ENDTEMP_DEGREE

SOURCES = frozenset({"measured", "datasheet", "assumed"})
UNIT_KINDS = ("load", "bess", "pv", "wind", "genset")
GRID_NAME = "GRID"
NET_BASE_MVA = 1.0

#: How each unit kind appears in the registry. ``load`` is absent: loads
#: travel in loads.csv, by bus.
REGISTRY_KIND = {"bess": "storage", "pv": "res", "wind": "res", "genset": "genset"}

#: A transformer winding may differ from its bus's nominal voltage, because
#: tap and design voltages are routinely a few percent off (110/21 kV on a
#: 20 kV busbar). More than this is a wiring mistake.
WINDING_TOLERANCE = 0.10

_TOP = frozenset({"name", "f_hz", "pcc", "buses", "transformers", "cables", "units"})
_PCC_FIELDS = ("vm_pu", "sk_max_mva", "sk_min_mva", "rx_max", "rx_min")
_TRAFO_FIELDS = ("sn_mva", "vn_hv_kv", "vn_lv_kv", "vk_percent", "vkr_percent", "pfe_kw", "i0_percent")
_CABLE_FIELDS = ("length_km", "r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka")
_UNIT_FIELDS = {
    "load": ("p_mw", "pf"),
    "bess": ("p_mw", "e_mwh", "s_mva", "k_sc", "rx_sc"),
    "pv": ("p_mw", "s_mva", "k_sc", "rx_sc"),
    "wind": ("p_mw", "s_mva", "k_sc", "rx_sc"),
    "genset": ("p_mw", "s_mva", "xd_pp", "rx_sc"),
}
#: Fields that may be zero. Every other tagged field must be strictly positive.
_MAY_BE_ZERO = frozenset({"pfe_kw", "i0_percent", "c_nf_per_km", "rx_sc", "rx_max", "rx_min"})

CAMPUS_LEDGER = (
    "campus: every generating unit is a PQ injection (sgen) at its dispatched "
    "P; a genset runs in power-factor control on a campus, not voltage control",
    "campus: a genset is screened for IEC 60909 as a current source of "
    "1/x''d times its rated current (k = 1/xd_pp): an approximation of a "
    "synchronous machine for switchgear-duty screening (assumed)",
    "campus: the grid at the PCC is the grid operator's Sk''max/min and R/X "
    "(ext_grid); nothing is derived from the machines on the campus",
    "campus: cable end-of-fault conductor temperature for the IEC 60909 "
    "minimum case is the study-wide 80 degC (static/shortcircuit.py) (assumed)",
)


@dataclasses.dataclass
class Campus:
    """The built campus: the pandapower ``net``, the ``units`` table (index
    ``unit_id``: ``kind``, ``bus``, and the ratings), every tagged value in
    long form (``params``: ``element``, ``param``, ``value``, ``source``), and
    the unit ``registry`` (index ``unit_id``: ``bus``, ``kind``)."""
    name: str
    net: object
    units: pd.DataFrame
    params: pd.DataFrame
    registry: pd.DataFrame


def _tagged(where, field, spec, rows):
    """``spec[field]`` as a float, after checking its ``{value, source}`` form.
    The value and tag are appended to ``rows``."""
    if field not in spec:
        raise ContractError(f"{where}: missing {field}")
    entry = spec[field]
    if not isinstance(entry, dict) or "value" not in entry or "source" not in entry:
        raise ContractError(f"{where}.{field} must be a mapping with 'value' and 'source', got {entry!r}")
    if entry["source"] not in SOURCES:
        raise ContractError(f"{where}.{field} has unknown source {entry['source']!r}; allowed {sorted(SOURCES)}")
    v = entry["value"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ContractError(f"{where}.{field} value must be a finite number, got {v!r}")
    if v < 0 or (v == 0 and field not in _MAY_BE_ZERO):
        raise ContractError(f"{where}.{field} must be {'non-negative' if field in _MAY_BE_ZERO else 'positive'}, got {v}")
    rows.append({"element": where.split(".")[-1], "param": field, "value": float(v), "source": entry["source"]})
    return float(v)


def _fields(where, spec, required, structural=()):
    if not isinstance(spec, dict):
        raise ContractError(f"{where}: expected a mapping, got {type(spec).__name__}")
    unknown = sorted(set(spec) - set(required) - set(structural))
    if unknown:
        raise ContractError(f"{where}: unknown field(s) {unknown}; allowed {sorted(set(required) | set(structural))}")


def _bus_kv(where, buses, name):
    if name not in buses:
        raise ContractError(f"{where}: unknown bus {name!r}; the campus buses are {sorted(buses)}")
    return buses[name]


def _connected_from(start, edges, buses):
    adj = {b: set() for b in buses}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, todo = {start}, deque([start])
    while todo:
        for nxt in adj[todo.popleft()] - seen:
            seen.add(nxt)
            todo.append(nxt)
    return seen


def build_campus(spec) -> Campus:
    """Validate a campus description (the mapping, see the module docstring)
    and build its pandapower net."""
    if not isinstance(spec, dict) or not isinstance(spec.get("campus"), dict):
        raise ContractError("a campus description needs a top-level 'campus' mapping")
    c = spec["campus"]
    unknown = sorted(set(c) - _TOP)
    if unknown:
        raise ContractError(f"campus: unknown key(s) {unknown}; allowed {sorted(_TOP)}")
    for key in ("pcc", "units"):
        if key not in c:
            raise ContractError(f"campus: missing {key!r}")
    name = str(c.get("name") or "CAMPUS")
    f_hz = c.get("f_hz", 50)
    if f_hz not in (50, 60):
        raise ContractError(f"campus.f_hz must be 50 or 60, got {f_hz!r}")
    rows = []

    # buses: the PCC first, then the rest in file order
    pcc = c["pcc"]
    _fields("pcc", pcc, _PCC_FIELDS, ("bus", "vn_kv", "pypsa_name"))
    if "bus" not in pcc or "vn_kv" not in pcc:
        raise ContractError("pcc: needs 'bus' and 'vn_kv'")
    buses = {str(pcc["bus"]): float(pcc["vn_kv"])}
    for b, bspec in (c.get("buses") or {}).items():
        _fields(f"buses.{b}", bspec, (), ("vn_kv", "pypsa_name"))
        if str(b) in buses:
            raise ContractError(f"buses: duplicate bus {b!r}")
        try:
            vn = float(bspec["vn_kv"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"buses.{b}: needs a numeric vn_kv") from exc
        if not vn > 0:
            raise ContractError(f"buses.{b}: vn_kv must be positive, got {vn}")
        buses[str(b)] = vn

    trafos, cables, units = c.get("transformers") or {}, c.get("cables") or {}, c["units"] or {}
    element_names = [GRID_NAME, *map(str, trafos), *map(str, cables), *map(str, units)]
    dup = sorted({n for n in element_names if element_names.count(n) > 1})
    if dup:
        raise ContractError(f"campus: duplicate element name(s) {dup}; transformer, cable and unit names share one namespace")
    validate_canonical(pd.Series(list(buses)), pd.Series(element_names))

    pcc_bus = str(pcc["bus"])
    pv = {f: _tagged(f"pcc.{pcc_bus}", f, pcc, rows) for f in _PCC_FIELDS}
    if pv["sk_min_mva"] > pv["sk_max_mva"]:
        raise ContractError(f"pcc: sk_min_mva ({pv['sk_min_mva']}) is above sk_max_mva ({pv['sk_max_mva']})")

    net = pp.create_empty_network(sn_mva=NET_BASE_MVA, f_hz=float(f_hz), name=name)
    idx = {b: pp.create_bus(net, vn_kv=kv, name=b) for b, kv in buses.items()}
    pp.create_ext_grid(net, idx[pcc_bus], vm_pu=pv["vm_pu"], s_sc_max_mva=pv["sk_max_mva"],
                       s_sc_min_mva=pv["sk_min_mva"], rx_max=pv["rx_max"], rx_min=pv["rx_min"],
                       name=GRID_NAME)

    edges = []
    for tname, ts in trafos.items():
        where = f"transformers.{tname}"
        _fields(where, ts, _TRAFO_FIELDS, ("hv_bus", "lv_bus", "pypsa_name"))
        hv, lv = str(ts.get("hv_bus")), str(ts.get("lv_bus"))
        if hv == lv:
            raise ContractError(f"{where}: hv_bus and lv_bus are the same bus {hv!r}")
        hv_kv, lv_kv = _bus_kv(where, buses, hv), _bus_kv(where, buses, lv)
        v = {f: _tagged(where, f, ts, rows) for f in _TRAFO_FIELDS}
        for field, bus_kv in (("vn_hv_kv", hv_kv), ("vn_lv_kv", lv_kv)):
            if abs(v[field] - bus_kv) > WINDING_TOLERANCE * bus_kv:
                raise ContractError(
                    f"{where}.{field} = {v[field]} kV is more than {WINDING_TOLERANCE:.0%} off its bus's {bus_kv} kV"
                )
        if not v["vn_hv_kv"] > v["vn_lv_kv"]:
            raise ContractError(f"{where}: vn_hv_kv must be above vn_lv_kv")
        if not v["vkr_percent"] < v["vk_percent"]:
            raise ContractError(f"{where}: vkr_percent ({v['vkr_percent']}) must be below vk_percent ({v['vk_percent']})")
        pp.create_transformer_from_parameters(
            net, idx[hv], idx[lv], sn_mva=v["sn_mva"], vn_hv_kv=v["vn_hv_kv"], vn_lv_kv=v["vn_lv_kv"],
            vkr_percent=v["vkr_percent"], vk_percent=v["vk_percent"], pfe_kw=v["pfe_kw"],
            i0_percent=v["i0_percent"], name=str(tname))
        edges.append((hv, lv))

    for cname, cs in cables.items():
        where = f"cables.{cname}"
        _fields(where, cs, _CABLE_FIELDS, ("from_bus", "to_bus", "pypsa_name"))
        a, b = str(cs.get("from_bus")), str(cs.get("to_bus"))
        if a == b:
            raise ContractError(f"{where}: from_bus and to_bus are the same bus {a!r}")
        if _bus_kv(where, buses, a) != _bus_kv(where, buses, b):
            raise ContractError(f"{where}: connects buses of different nominal voltages ({buses[a]} and {buses[b]} kV)")
        v = {f: _tagged(where, f, cs, rows) for f in _CABLE_FIELDS}
        pp.create_line_from_parameters(
            net, idx[a], idx[b], length_km=v["length_km"], r_ohm_per_km=v["r_ohm_per_km"],
            x_ohm_per_km=v["x_ohm_per_km"], c_nf_per_km=v["c_nf_per_km"], max_i_ka=v["max_i_ka"],
            endtemp_degree=LINE_ENDTEMP_DEGREE, name=str(cname))
        edges.append((a, b))

    reachable = _connected_from(pcc_bus, edges, buses)
    stranded = sorted(set(buses) - reachable)
    if stranded:
        raise ContractError(f"campus: bus(es) {stranded} are not connected to the PCC {pcc_bus!r}")

    unit_rows, reg_rows = [], [{"unit_id": GRID_NAME, "bus": pcc_bus, "kind": "ext_grid"}]
    for uname, us in units.items():
        where = f"units.{uname}"
        if not isinstance(us, dict):
            raise ContractError(f"{where}: expected a mapping, got {type(us).__name__}")
        kind = us.get("kind")
        if kind not in UNIT_KINDS:
            raise ContractError(f"{where}: unknown kind {kind!r}; allowed {list(UNIT_KINDS)}")
        required = _UNIT_FIELDS[kind]
        _fields(where, us, required, ("kind", "bus", "pypsa_name"))
        bus = str(us.get("bus"))
        _bus_kv(where, buses, bus)
        v = {f: _tagged(where, f, us, rows) for f in required}
        uname = str(uname)
        row = {"unit_id": uname, "kind": kind, "bus": bus, "p_mw": v["p_mw"],
               "s_mva": v.get("s_mva", math.nan), "e_mwh": v.get("e_mwh", math.nan),
               "pf": v.get("pf", math.nan), "pypsa_name": us.get("pypsa_name")}
        if kind == "load":
            if not 0 < v["pf"] <= 1:
                raise ContractError(f"{where}.pf must be in (0, 1], got {v['pf']}")
            pp.create_load(net, idx[bus], p_mw=v["p_mw"], q_mvar=v["p_mw"] * math.tan(math.acos(v["pf"])), name=uname)
        else:
            if v["s_mva"] < v["p_mw"]:
                raise ContractError(f"{where}: s_mva ({v['s_mva']}) is below p_mw ({v['p_mw']}); the inverter or machine cannot carry its own rating")
            k = 1.0 / v["xd_pp"] if kind == "genset" else v["k_sc"]
            pp.create_sgen(net, idx[bus], p_mw=0.0 if kind == "bess" else v["p_mw"], q_mvar=0.0,
                           sn_mva=v["s_mva"], name=uname, k=k, rx=v["rx_sc"],
                           generator_type="current_source")
            reg_rows.append({"unit_id": uname, "bus": bus, "kind": REGISTRY_KIND[kind]})
        unit_rows.append(row)

    units_df = pd.DataFrame(unit_rows).set_index("unit_id")
    registry = pd.DataFrame(reg_rows).set_index("unit_id")
    params = pd.DataFrame(rows, columns=["element", "param", "value", "source"])
    return Campus(name=name, net=net, units=units_df, params=params, registry=registry)


def load_campus(path) -> Campus:
    """``build_campus`` from a YAML file. A missing file is an error."""
    path = Path(path)
    if not path.is_file():
        raise ContractError(f"campus file not found: {path}")
    return build_campus(yaml.safe_load(path.read_text()))
