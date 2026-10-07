"""Draft a campus description from a solved hub project (plan C1, stage B).

The capacity-expansion model gives the campus's AC buses and their nominal
voltages, the Links between them, and every asset with its optimised
capacity. It gives no impedances, no inverter ratings and no grid fault
level. ``draft_campus`` writes the campus YAML (``gridspine.ingest.campus``)
from what the model gives. Everything it has to supply itself is a typical
value tagged ``assumed``, for the engineer to replace.

The draft is the same file a user-supplied single line would be. "Generate,
then edit" and "supply your own" are therefore one path.

Translation, in the order the draft is built:

* **PCC.** The bus marked ``eh_poc``. Its fault level is the bus's
  ``eh_sk_mva`` if the project has one, else ``TYPICAL_SK_MVA`` for its
  voltage class. ``sk_min`` is taken equal to ``sk_max``, because no
  separate minimum is known. Generators on the PCC bus with a grid carrier
  (``GRID_CARRIERS``) *are* the grid and become no unit.
* **Buses.** Every AC bus keeps its ``v_nom``. Buses of other carriers (H2,
  heat) are not electrical and are left out.
* **Links between AC buses.**
  - At different voltages, a link becomes a **transformer**. It is rated at
    its optimised capacity over the sizing power factor ``SIZING_PF``,
    rounded **up** to the next size in ``STANDARD_MVA``, with typical
    impedance and losses for its size class (``_TRAFO_CLASS``).
  - At the same voltage, a link becomes a short **bus tie**.
* **A Link from an AC bus to another carrier** (an electrolyser, a heat
  pump) is power drawn from that bus, so it becomes a **load** at its
  optimised capacity. A Link from another carrier into an AC bus (a fuel
  cell) is refused for now: its converter data cannot be guessed honestly.
* **Lines** become cables with their own r, x and b.
* **Generators** are typed by carrier (``CARRIER_KIND``). An unknown carrier
  is refused with its name, so the user can add it rather than have it
  silently become something else.
* **StorageUnits** become ``bess``, with ``e_mwh = p_nom_opt * max_hours``.
* **Loads** are rated at their peak hour.
* An asset the expansion built at 0 MW is skipped and listed in
  ``CampusDraft.skipped``.
* **Names** become canonical 12-character ids (``short_names``). Each
  element keeps its project name as ``pypsa_name``, which is how the hourly
  results find it (plan C2).

Duck-typed on the network object: only ``buses``, ``generators``,
``storage_units``, ``loads``, ``links``, ``lines`` and ``loads_t.p_set`` are
read, and pypsa is never imported here. ``producers/pypsa_nodal.py`` stays
the only module that loads a network file.
"""
import dataclasses
import math
import re

import numpy as np
import pandas as pd

from gridspine.schema.campus import STANDARD_MVA, validate_hourly, validate_pcc
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import CONVERTER_KINDS
from gridspine.schema.network import MAX_NAME_LEN

#: Power factor a transformer is sized at, from the MW the expansion chose.
SIZING_PF = 0.95
#: Power factor an inverter is rated to deliver its MW at.
INVERTER_PF = 0.95
#: Rated power factor of a synchronous genset.
GENSET_PF = 0.8
#: Power factor of every load.
LOAD_PF = 0.98

#: Typical transformer data by size class. Each row is ``(upper MVA,
#: vk %, vkr %)``. vk follows the IEC 60076-5 Table 1 minimum short-circuit
#: impedances and was not checked against the text. vkr is a typical copper
#: loss for the class. No-load losses are ``PFE_KW_PER_MVA`` and
#: ``I0_PERCENT``. All are tagged ``assumed``.
_TRAFO_CLASS = (
    (0.63, 4.0, 1.2), (1.25, 5.0, 1.0), (2.5, 6.0, 0.9), (6.3, 7.0, 0.8),
    (25.0, 8.0, 0.6), (40.0, 10.0, 0.5), (63.0, 11.0, 0.4), (100.0, 12.5, 0.35),
    (math.inf, 12.5, 0.3),
)
PFE_KW_PER_MVA = 1.0
I0_PERCENT = 0.1

#: The grid's short-circuit power at the PCC when the project gives none, by
#: voltage class as ``(minimum kV, MVA, R/X)``.
TYPICAL_SK_MVA = ((220.0, 10000.0, 0.1), (60.0, 3000.0, 0.1), (10.0, 300.0, 0.3), (0.0, 50.0, 0.5))

#: Inverter and genset short-circuit data.
INVERTER_K_SC, INVERTER_RX_SC = 1.2, 0.1
GENSET_XD_PP, GENSET_RX_SC = 0.15, 0.07

#: A same-voltage bus tie: a short cable of typical MV data.
TIE_KM, TIE_R, TIE_X, TIE_C = 0.01, 0.0754, 0.11, 0.0

#: The island block a drafted converter or genset gets when the hub sidecar
#: does not name it (plan I1). Every value is tagged ``assumed``. The genset
#: ``load_step_max_pct`` stands in for the ISO 8528-5 class, which the user
#: enters from the standard; the panel asks for it.
_QU_DEFAULT = [[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]]
DEFAULT_ISLAND = {
    "bess": {"control": "gfm_droop", "pf_rated": 0.95, "droop_pct": 4.0, "q_droop_pct": 4.0,
             "tau_f_s": 0.1, "i_max_pu": 1.2, "k_gfm_min": 1.0},
    "pv": {"control": "gfl_qu", "pf_rated": 0.95, "qu_points": _QU_DEFAULT},
    "wind": {"control": "gfl_qu", "pf_rated": 0.95, "qu_points": _QU_DEFAULT},
    "genset": {"governor": "droop", "droop_pct": 4.0, "q_droop_pct": 4.0, "H_s": 1.5, "T_g_s": 0.5,
               "ramp_pu_per_s": 0.2, "start_s": 10.0, "sync_s": 5.0, "p_min_pu": 0.3,
               "load_step_max_pct": 50.0, "cos_phi_r": GENSET_PF,
               "xd_sat": 2.0, "excitation": "avr_pmg", "neutral_earthing": "solid"},
}
#: Untagged fields of an island block: enumerations, flags and names.
_UNTAGGED = ("control", "governor", "excitation", "neutral_earthing", "spinning", "unit_transformer",
             "transfers_on_islanding", "critical")

GRID_CARRIERS = frozenset({"grid", "import", "grid_import", "electricity_import"})
CARRIER_KIND = {
    "solar": "pv", "pv": "pv", "solar rooftop": "pv", "solar-hsat": "pv",
    "wind": "wind", "onwind": "wind", "offwind": "wind", "offwind-ac": "wind", "offwind-dc": "wind",
    "gas": "genset", "diesel": "genset", "oil": "genset", "ocgt": "genset", "ccgt": "genset",
    "chp": "genset", "biomass": "genset", "biogas": "genset", "hydrogen engine": "genset",
}
_AC = frozenset({"AC", ""})


@dataclasses.dataclass
class CampusDraft:
    """``spec`` is the campus mapping, ready for ``build_campus`` or for
    writing as YAML. ``skipped`` holds a sentence per project element that
    the draft left out, and why."""
    spec: dict
    skipped: list


def short_names(names, taken=()):
    """Canonical, unique ids of at most ``MAX_NAME_LEN`` characters, in input
    order. A collision keeps the stem and appends ``_2``, ``_3`` and so on."""
    used, out = set(taken), []
    for raw in names:
        base = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw)).upper() or "X"
        cand, i = base[:MAX_NAME_LEN].rstrip("_-") or base[:MAX_NAME_LEN], 2
        while cand in used:
            suffix = f"_{i}"
            cand = base[: MAX_NAME_LEN - len(suffix)].rstrip("_-") + suffix
            i += 1
        used.add(cand)
        out.append(cand)
    return out


def _t(value):
    return {"value": float(value), "source": "assumed"}


def _marked(flag) -> bool:
    """True only for an explicit truthy tag. A bus added after the tag column
    exists carries NaN there, and ``bool(NaN)`` is True."""
    return bool(pd.notna(flag) and flag)


def _rating(df, name, col="p_nom"):
    """The optimised capacity of an extendable asset, else its fixed capacity
    (``col`` is ``p_nom`` or, for lines, ``s_nom``)."""
    opt = f"{col}_opt"
    extendable = f"{col}_extendable"
    if extendable in df.columns and bool(df.at[name, extendable]) and opt in df.columns \
            and pd.notna(df.at[name, opt]):
        return float(df.at[name, opt])
    return float(df.at[name, col])


def _standard_mva(s):
    for size in STANDARD_MVA:
        if size >= s - 1e-9:
            return size
    raise ContractError(f"a transformer of {s:.0f} MVA is above the largest standard size {STANDARD_MVA[-1]} MVA")


def _trafo_class(sn):
    for upper, vk, vkr in _TRAFO_CLASS:
        if sn <= upper:
            return vk, vkr


def _typical_sk(kv):
    for min_kv, sk, rx in TYPICAL_SK_MVA:
        if kv >= min_kv:
            return sk, rx


def _default_block(kind):
    return {f: v if f in _UNTAGGED else _t(v) if not isinstance(v, list) else
            {"value": [list(p) for p in v], "source": "assumed"}
            for f, v in DEFAULT_ISLAND[kind].items()}


def _retag(block, drop=()):
    """A validated island block (``schema/island.py``) back in the tagged
    file form, without the fields in ``drop``."""
    out = {f: {"value": v, "source": block["sources"][f]} for f, v in block["values"].items() if f not in drop}
    out.update({f: block[f] for f in _UNTAGGED if f in block})
    return out


def _attach_island(n, spec, island, pypsa_ids, skipped):
    """Copy the validated hub sidecar ``island`` into the drafted ``spec``
    (plan I1). ``pypsa_ids`` maps each drafted PyPSA name to its campus id."""
    units = spec["units"]
    known = set()
    for tbl in ("generators", "storage_units", "loads", "links"):
        known |= set(getattr(n, tbl, pd.DataFrame()).index)
    for name, block in island["units"].items():
        if name not in known:
            raise ContractError(f"island_config.units.{name}: the project has no component of that name")
        if name not in pypsa_ids:
            skipped.append(f"island data for {name}: the unit is not in the draft, so its island block is left out")
            continue
        uid = pypsa_ids[name]
        u = units[uid]
        if block["kind"] != u["kind"]:
            raise ContractError(
                f"island_config.units.{name} says kind {block['kind']!r}, but the project drafts it as {u['kind']!r}")
        out = _retag(block, drop=("unit_mw", "xd_pp"))
        if "xd_pp" in block["values"]:
            u["xd_pp"] = {"value": block["values"]["xd_pp"], "source": block["sources"]["xd_pp"]}
        if block["kind"] == "ups":
            out["it_load"] = _translate(block["it_load"], pypsa_ids, f"the UPS {name}'s it_load", skipped)
            if not out["it_load"]:
                raise ContractError(f"island_config.units.{name}: none of the loads it protects is in the draft")
        u["island"] = out
    for uid, u in units.items():
        if "island" not in u and u["kind"] in DEFAULT_ISLAND:
            u["island"] = _default_block(u["kind"])
    req = island["requirements"]
    out = _retag(req)
    out.update(scenarios=list(req["scenarios"]), linearised_uc=req["linearised_uc"],
               gfl_min_case=req["gfl_min_case"],
               frequency_limits={k: _retag(v) for k, v in req["frequency_limits"].items()})
    if req["ride_through_storage"]:
        rts = _translate(req["ride_through_storage"], pypsa_ids, "ride_through_storage", skipped)
        if rts:
            out["ride_through_storage"] = rts
    spec["island"] = out


def _translate(names, pypsa_ids, what, skipped):
    out = []
    for x in names:
        if x in pypsa_ids:
            out.append(pypsa_ids[x])
        else:
            skipped.append(f"{what}: {x} is not in the draft, so it is left out")
    return out


def draft_campus(n, island=None) -> CampusDraft:
    """The campus description for a solved hub network ``n`` (see the module
    docstring for what becomes what). ``island`` is the hub sidecar as
    ``schema.island.validate_island_config`` returns it; with it, the draft
    carries the island data (``_attach_island``)."""
    buses = n.buses
    carrier = buses["carrier"].fillna("AC") if "carrier" in buses.columns else pd.Series("AC", index=buses.index)
    ac = [b for b in buses.index if carrier[b] in _AC]
    poc = [b for b in ac if "eh_poc" in buses.columns and _marked(buses.at[b, "eh_poc"])]
    if len(poc) != 1:
        raise ContractError(
            f"the project must mark exactly one AC bus as the PCC (eh_poc); found {poc or 'none'}"
        )
    pcc = poc[0]
    skipped = []

    bus_ids = dict(zip(ac, short_names(ac)))
    pcc_kv = float(buses.at[pcc, "v_nom"])
    sk_given = buses.at[pcc, "eh_sk_mva"] if "eh_sk_mva" in buses.columns else math.nan
    sk_typ, rx_typ = _typical_sk(pcc_kv)
    sk = float(sk_given) if pd.notna(sk_given) and float(sk_given) > 0 else sk_typ
    spec = {
        "name": short_names([getattr(n, "name", "") or "CAMPUS"])[0],
        "f_hz": 50,
        "pcc": {"bus": bus_ids[pcc], "vn_kv": pcc_kv, "pypsa_name": pcc, "vm_pu": _t(1.0),
                "sk_max_mva": _t(sk), "sk_min_mva": _t(sk), "rx_max": _t(rx_typ), "rx_min": _t(rx_typ)},
        "buses": {bus_ids[b]: {"vn_kv": float(buses.at[b, "v_nom"]), "pypsa_name": b} for b in ac if b != pcc},
        "transformers": {}, "cables": {}, "units": {},
    }
    taken = {"GRID"}
    pypsa_ids = {}

    def ids(names):
        out = short_names(names, taken)
        taken.update(out)
        return out

    # Links: transformers, bus ties, and power drawn into other carriers
    links = n.links
    for lname in links.index:
        b0, b1 = links.at[lname, "bus0"], links.at[lname, "bus1"]
        p = _rating(links, lname)
        in0, in1 = b0 in bus_ids, b1 in bus_ids
        if p <= 0:
            skipped.append(f"link {lname}: built at 0 MW")
            continue
        if in0 and in1:
            kv0, kv1 = float(buses.at[b0, "v_nom"]), float(buses.at[b1, "v_nom"])
            (eid,) = ids([lname])
            if kv0 == kv1:
                i_ka = p / SIZING_PF / (math.sqrt(3) * kv0)
                spec["cables"][eid] = {"from_bus": bus_ids[b0], "to_bus": bus_ids[b1], "pypsa_name": lname,
                                       "length_km": _t(TIE_KM), "r_ohm_per_km": _t(TIE_R),
                                       "x_ohm_per_km": _t(TIE_X), "c_nf_per_km": _t(TIE_C),
                                       "max_i_ka": _t(i_ka)}
                continue
            hv, lv = (b0, b1) if kv0 > kv1 else (b1, b0)
            sn = _standard_mva(p / SIZING_PF)
            vk, vkr = _trafo_class(sn)
            spec["transformers"][eid] = {
                "hv_bus": bus_ids[hv], "lv_bus": bus_ids[lv], "pypsa_name": lname,
                "sn_mva": _t(sn), "vn_hv_kv": _t(max(kv0, kv1)), "vn_lv_kv": _t(min(kv0, kv1)),
                "vk_percent": _t(vk), "vkr_percent": _t(vkr),
                "pfe_kw": _t(PFE_KW_PER_MVA * sn), "i0_percent": _t(I0_PERCENT),
            }
        elif in0:
            (eid,) = ids([lname])
            spec["units"][eid] = {"kind": "load", "bus": bus_ids[b0], "pypsa_name": lname,
                                  "p_mw": _t(p), "pf": _t(LOAD_PF)}
        elif in1:
            raise ContractError(
                f"link {lname} feeds AC bus {b1} from carrier bus {b0}: a converter source (a fuel "
                "cell, for example) cannot be drafted yet; add it to the campus file as a unit"
            )
        else:
            skipped.append(f"link {lname}: between non-electrical buses")

    for lname in getattr(n, "lines", pd.DataFrame()).index:
        ln = n.lines
        b0, b1 = ln.at[lname, "bus0"], ln.at[lname, "bus1"]
        length = float(ln.at[lname, "length"]) if float(ln.at[lname, "length"]) > 0 else 1.0
        kv, s_nom = float(buses.at[b0, "v_nom"]), _rating(ln, lname, col="s_nom")
        if s_nom <= 0:
            raise ContractError(f"line {lname} has no rating (s_nom); a cable needs one")
        (eid,) = ids([lname])
        spec["cables"][eid] = {
            "from_bus": bus_ids[b0], "to_bus": bus_ids[b1], "pypsa_name": lname,
            "length_km": _t(length), "r_ohm_per_km": _t(float(ln.at[lname, "r"]) / length),
            "x_ohm_per_km": _t(float(ln.at[lname, "x"]) / length),
            "c_nf_per_km": _t(float(ln.at[lname, "b"]) / (2 * math.pi * 50) / length * 1e9),
            "max_i_ka": _t(s_nom / (math.sqrt(3) * kv)),
        }

    gens = n.generators
    for gname in gens.index:
        bus, car = gens.at[gname, "bus"], str(gens.at[gname, "carrier"])
        if bus == pcc and car.lower() in GRID_CARRIERS:
            continue                                   # the grid itself
        if bus not in bus_ids:
            skipped.append(f"generator {gname}: on non-electrical bus {bus}")
            continue
        kind = CARRIER_KIND.get(car.lower())
        if kind is None:
            raise ContractError(
                f"generator {gname} has carrier {car!r}, which the draft cannot type; known carriers "
                f"are {sorted(CARRIER_KIND)} (add it there, or add the unit to the campus file)"
            )
        p = _rating(gens, gname)
        if p <= 0:
            skipped.append(f"generator {gname}: built at 0 MW")
            continue
        (eid,) = ids([gname])
        pypsa_ids[gname] = eid
        u = {"kind": kind, "bus": bus_ids[bus], "pypsa_name": gname, "p_mw": _t(p)}
        if kind == "genset":
            u.update(s_mva=_t(p / GENSET_PF), xd_pp=_t(GENSET_XD_PP), rx_sc=_t(GENSET_RX_SC))
        else:
            u.update(s_mva=_t(p / INVERTER_PF), k_sc=_t(INVERTER_K_SC), rx_sc=_t(INVERTER_RX_SC))
        spec["units"][eid] = u

    su = n.storage_units
    for sname in su.index:
        bus = su.at[sname, "bus"]
        p = _rating(su, sname)
        if bus not in bus_ids:
            skipped.append(f"storage unit {sname}: on non-electrical bus {bus}")
            continue
        if p <= 0:
            skipped.append(f"storage unit {sname}: built at 0 MW")
            continue
        (eid,) = ids([sname])
        pypsa_ids[sname] = eid
        u = {"kind": "bess", "bus": bus_ids[bus], "pypsa_name": sname, "p_mw": _t(p),
             "e_mwh": _t(p * float(su.at[sname, "max_hours"])), "s_mva": _t(p / INVERTER_PF)}
        if island and island["units"].get(sname, {}).get("kind") == "ups":
            u["kind"] = "ups"                        # the sidecar says so; a UPS feeds no fault
        else:
            u.update(k_sc=_t(INVERTER_K_SC), rx_sc=_t(INVERTER_RX_SC))
        spec["units"][eid] = u

    p_set_t = getattr(getattr(n, "loads_t", None), "p_set", pd.DataFrame())
    loads = n.loads
    for lname in loads.index:
        bus = loads.at[lname, "bus"]
        if bus not in bus_ids:
            skipped.append(f"load {lname}: on non-electrical bus {bus}")
            continue
        peak = float(p_set_t[lname].max()) if lname in p_set_t.columns else float(loads.at[lname, "p_set"])
        if peak <= 0:
            skipped.append(f"load {lname}: never draws power")
            continue
        (eid,) = ids([lname])
        pypsa_ids[lname] = eid
        spec["units"][eid] = {"kind": "load", "bus": bus_ids[bus], "pypsa_name": lname,
                              "p_mw": _t(peak), "pf": _t(LOAD_PF)}

    if not spec["cables"]:
        del spec["cables"]
    if island is not None:
        _attach_island(n, spec, island, pypsa_ids, skipped)
    return CampusDraft(spec={"campus": spec}, skipped=skipped)


# ── the hourly results, per investment period (plan C2) ────────────────────

_SERIES = (
    # (network table, time-series attribute, campus kinds it may hold)
    ("generators", "generators_t", ("pv", "wind", "genset")),
    ("storage_units", "storage_units_t", ("bess", "ups")),
    ("loads", "loads_t", ("load",)),
    ("links", "links_t", ("load",)),
)
_COMPONENT = {"generators": "Generator", "storage_units": "StorageUnit", "loads": "Load", "links": "Link"}


def _periods(n):
    """``[(period label, snapshots of that period)]``. A multi-period network
    has one entry per investment period. A single-period network has one,
    labelled with the year of its first snapshot (0 for a non-datetime
    index)."""
    sns = n.snapshots
    if isinstance(sns, pd.MultiIndex):
        return [(int(p), sns[sns.get_level_values(0) == p]) for p in sns.get_level_values(0).unique()]
    first = sns[0]
    return [(int(first.year) if hasattr(first, "year") else 0, sns)]


def _find(n, pypsa_name, kind, uid):
    hits = [(tbl, ts) for tbl, ts, kinds in _SERIES
            if pypsa_name in getattr(n, tbl).index and kind in kinds]
    if not hits:
        raise ContractError(
            f"unit {uid}: pypsa_name {pypsa_name!r} is not a {kind} in the project "
            "(no generator, storage unit, load or link of that name can be one)"
        )
    if len(hits) > 1:
        raise ContractError(f"unit {uid}: pypsa_name {pypsa_name!r} is ambiguous in the project ({[h[0] for h in hits]})")
    return hits[0]


def is_solved(n) -> bool:
    """A network carries dispatch results if any dispatch frame has a column."""
    frames = (("generators_t", "p"), ("storage_units_t", "p"), ("links_t", "p0"), ("loads_t", "p"))
    return any(len(getattr(getattr(n, ts, None), attr, pd.DataFrame()).columns) for ts, attr in frames)


def _injection(n, tbl, ts, name, uid, solved):
    """The unit's injected power over every snapshot (a load is negative).

    A saved network drops a time series that stays at its default, so in a
    solved network an asset with no column never left 0 MW. A network with
    no dispatch results at all is unsolved, and is refused."""
    series = getattr(n, ts)
    if tbl == "loads":
        for attr in ("p", "p_set"):
            frame = getattr(series, attr, pd.DataFrame())
            if name in frame.columns:
                return -frame[name].astype(float)
        return pd.Series(-float(n.loads.at[name, "p_set"]), index=n.snapshots)
    attr = "p0" if tbl == "links" else "p"
    frame = getattr(series, attr, pd.DataFrame())
    if name not in frame.columns:
        if solved:
            return pd.Series(0.0, index=n.snapshots)
        raise ContractError(f"unit {uid}: {name} has no hourly power in the project; is the project solved?")
    p = frame[name].astype(float)
    return -p if tbl == "links" else p


def _active(n, tbl, name, period, multi):
    if not multi or "build_year" not in getattr(n, tbl).columns:
        return True
    return bool(n.get_active_assets(_COMPONENT[tbl], period)[name])


def campus_hourly(n, campus):
    """``(hourly, pcc)`` for a solved hub network ``n`` and its campus
    description ``campus`` (the mapping), per investment period. See
    ``gridspine.schema.campus`` for the two tables."""
    c = campus["campus"]
    periods = _periods(n)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    solved = is_solved(n)
    rows = []
    for uid, u in c["units"].items():
        pname = u.get("pypsa_name")
        if not pname:
            raise ContractError(f"unit {uid} has no pypsa_name, so the project cannot give its hourly power")
        tbl, ts = _find(n, pname, u["kind"], uid)
        inj = _injection(n, tbl, ts, pname, uid, solved)
        for period, sns in periods:
            on = _active(n, tbl, pname, period, multi)
            vals = inj.loc[sns].to_numpy() if on else np.zeros(len(sns))
            rows.append(pd.DataFrame({"unit_id": uid, "period": period, "hour": np.arange(len(sns)),
                                      "p_mw": vals, "status": int(on)}))
    hourly = validate_hourly(pd.concat(rows, ignore_index=True))

    pcc_bus = c["pcc"].get("pypsa_name")
    if not pcc_bus:
        raise ContractError("the campus PCC has no pypsa_name, so the project cannot give its import")
    links = n.links
    flow = pd.Series(0.0, index=n.snapshots)
    touching = 0
    for lname in links.index:
        if links.at[lname, "bus0"] == pcc_bus:
            flow = flow + n.links_t.p0[lname].astype(float)
            touching += 1
        elif links.at[lname, "bus1"] == pcc_bus:
            flow = flow + n.links_t.p1[lname].astype(float)
            touching += 1
    if not touching:
        grid = [g for g in n.generators.index if n.generators.at[g, "bus"] == pcc_bus
                and str(n.generators.at[g, "carrier"]).lower() in GRID_CARRIERS]
        if not grid:
            raise ContractError(f"nothing in the project connects the PCC bus {pcc_bus!r} to the campus")
        flow = n.generators_t.p[grid].astype(float).sum(axis=1)
    weight = n.snapshot_weightings["objective"]
    pcc = pd.concat([
        pd.DataFrame({"period": period, "hour": np.arange(len(sns)),
                      "weight": weight.loc[sns].to_numpy(), "import_mw": flow.loc[sns].to_numpy()})
        for period, sns in periods
    ], ignore_index=True)
    return hourly, validate_pcc(pcc)
