"""
The aggregated plant model (GC-01, TRGC 01/02).

TRGC 01/02 ask for a detailed and an aggregated plant model. The detailed
model is the campus description itself (``ingest/campus.py``). This module
derives the aggregated one from it, description to description, so that
``build_campus`` and every study that takes a ``Campus`` (load flow,
IEC 60909) accept it unchanged.

**Method.** The WECC single-machine equivalent (WECC Renewable Energy
Modeling Task Force, "Wind/PV Power Plant Dynamic Modeling Guide"): each
collector system collapses into one equivalent collector branch, one
equivalent unit transformer and one equivalent unit per converter kind.

* A collector system is everything behind a main transformer: the
  transformers at the PCC are the main transformers, kept as they are, and
  each LV bus they feed is a **root**. The tree is every bus and branch
  reachable from the root without going back through a main transformer.
* Collector impedance, by the power-weighted equal-loss rule: with ``P_i``
  the summed rated ``p_mw`` of the converter units downstream of branch
  ``i`` and ``P`` the total, ``Z_eq = sum(Z_i * P_i**2) / P**2``. ``Z_i`` is
  the branch's series impedance (per run divided by ``parallel``) referred
  to the root's nominal kV. The equivalent dissipates the detailed tree's
  series losses when every unit runs at the same fraction of its rating.
  For ``N`` identical units in parallel it is ``Z / N``.
* Charging is summed (referred to the root kV).
* The unit transformers follow the same equal-loss rule on their short
  circuit impedance (HV side ohms); the equivalent has the summed rating,
  so ``N`` equal transformers give the same ``vk_percent`` and
  ``vkr_percent`` on ``N`` times the MVA. No-load losses are summed; the
  magnetising current is rating-weighted.
* One unit per converter kind: ``p_mw``, ``s_mva`` and ``e_mwh`` summed;
  ``k_sc`` and ``rx_sc`` weighted by ``s_mva`` (the fault currents add).
* Every derived value is tagged with the least sure source of its inputs
  (``assumed`` < ``datasheet`` < ``measured``).
* The cables' zero sequence is aggregated the same way when every tree
  cable carries it; otherwise it is omitted and an assumption line names
  the cables without it.

Elements at a root bus itself (loads, compensation, units) stay as they
are, and so does a tree that holds no converter unit (an auxiliary supply,
say): it is not a plant collector. What the aggregated model leaves out (island data, relays on removed
branches, unequal taps) is listed in ``Aggregation.assumptions``.

The input is first checked as ``build_campus`` checks it (``ContractError``).
Then **refused** (``AggregationError``, naming the element): a converter
unit with zero ``p_mw`` or ``s_mva``; a ring in a tree (any branch that
reaches a bus already reached); a tree that
reaches the PCC another way, or holds two roots; a load, genset, UPS,
compensation, earthing transformer or series capacitor (GC-01b; the walk
follows it, so the buses behind it count) inside a tree (below its root): an
equivalent of them is not defined here; transformers cascaded in a tree, or
fed from their LV side; unit transformers with different rated voltages,
vector groups or earthing, or units at two voltage levels; a campus with no
tree holding a converter unit; an equivalent's name already taken.

``freeze`` (the model-set freeze, ``schema/model_set.py``) joins this
module next: it needs ``build_campus``, and ``schema/`` imports only
``schema/``.
"""

import copy
import dataclasses
import math
from collections import deque

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError

#: The unit kinds an equivalent is built for: the converter-interfaced ones.
CONVERTER_KINDS = ("bess", "pv", "wind", "fuel_cell")
#: The short name of each kind in an equivalent unit's name (12-character cap).
_KIND_TAG = {"bess": "BESS", "pv": "PV", "wind": "WT", "fuel_cell": "FC"}
#: Least sure first.
_SOURCE_RANK = ("assumed", "datasheet", "measured")
_SUMMED = ("p_mw", "s_mva", "e_mwh")
_WEIGHTED = ("k_sc", "rx_sc")


class AggregationError(ContractError):
    """The campus cannot be aggregated; the message names why and where."""


@dataclasses.dataclass
class Aggregation:
    """
    The aggregated campus description, ``unit_map`` (each unit of the
    aggregated campus -> the detailed units it stands for; untouched units
    map to themselves) and what the aggregation left out (one line each).
    """

    spec: dict
    unit_map: dict
    assumptions: list


def _v(field):
    return float(field["value"])


def _least_sure(*fields):
    return min((f["source"] for f in fields), key=_SOURCE_RANK.index)


def _tag(value, *fields):
    return {"value": value, "source": _least_sure(*fields)}


def _bus_kv(c, bus):
    return float(c["buses"][bus]["vn_kv"])


def _check_ratings(c):
    for uid, u in (c.get("units") or {}).items():
        if u.get("kind") not in CONVERTER_KINDS:
            continue
        for f in ("p_mw", "s_mva"):
            if f in u and isinstance(u[f], dict) and _num(u[f].get("value")) == 0.0:
                raise AggregationError(
                    f"aggregation: unit {uid} has zero {f}; an equivalent weights "
                    "by rating, so every converter unit needs a positive one"
                )


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tree(c, root, main, roots):
    """
    The buses and branches behind ``root``: ``(nodes, parent, edges)``,
    ``parent[bus] = (parent_bus, kind, name)``; refused on a ring or a second
    path to the PCC.
    """
    pcc = c["pcc"]["bus"]
    adj = {}
    for kind, table, a, b in (
        ("cable", "cables", "from_bus", "to_bus"),
        ("trafo", "transformers", "hv_bus", "lv_bus"),
        # followed so the walk sees the whole collector; _plan refuses one inside it
        ("series capacitor", "series_capacitors", "from_bus", "to_bus"),
    ):
        for name, br in (c.get(table) or {}).items():
            if name in main:
                continue
            adj.setdefault(br[a], []).append((br[b], kind, name))
            adj.setdefault(br[b], []).append((br[a], kind, name))
    nodes, parent, seen_edges, q = [root], {}, set(), deque([root])
    while q:
        u = q.popleft()
        for v, kind, name in adj.get(u, []):
            if name in seen_edges:
                continue
            seen_edges.add(name)
            if v == pcc:
                raise AggregationError(
                    f"aggregation: the collector behind {root} reaches the PCC again "
                    f"through {kind} {name}; it must be radial"
                )
            if v in parent:
                raise AggregationError(
                    f"aggregation: the collector behind {root} is not radial: "
                    f"{kind} {name} closes a ring at {v}"
                )
            if v in roots:
                raise AggregationError(
                    f"aggregation: the collector behind {root} reaches {v}, which a "
                    "main transformer also feeds: a second path to the PCC"
                )
            parent[v] = (u, kind, name)
            nodes.append(v)
            q.append(v)
    return nodes, parent


def _plan(c, root, main, roots):
    nodes, parent = _tree(c, root, main, roots)
    below = set(nodes) - {root}
    units = {uid: u for uid, u in c["units"].items() if u["bus"] in below}
    if not any(u["kind"] in CONVERTER_KINDS for u in units.values()):
        return None
    for uid, u in units.items():
        if u["kind"] not in CONVERTER_KINDS:
            raise AggregationError(
                f"aggregation: {u['kind']} {uid} sits inside the collector behind {root} "
                f"(at {u['bus']}); only converter units {list(CONVERTER_KINDS)} aggregate"
            )
    for comp in c.get("compensation") or []:
        if comp["bus"] in below:
            raise AggregationError(
                f"aggregation: compensation {comp['name']} sits inside the collector "
                f"behind {root} (at {comp['bus']})"
            )
    for name, sc in (c.get("series_capacitors") or {}).items():
        if below & {sc["from_bus"], sc["to_bus"]}:
            raise AggregationError(
                f"aggregation: series capacitor {name} sits inside the collector "
                f"behind {root} ({sc['from_bus']}-{sc['to_bus']})"
            )
    for name, et in (c.get("earthing_transformers") or {}).items():
        if et["bus"] in below:
            raise AggregationError(
                f"aggregation: earthing transformer {name} sits inside the collector "
                f"behind {root} (at {et['bus']})"
            )
    trafos = [(v, name) for v, (u, kind, name) in parent.items() if kind == "trafo"]
    for v, name in trafos:
        tr = c["transformers"][name]
        if tr["lv_bus"] != v:
            raise AggregationError(
                f"aggregation: transformer {name} is fed from its LV side; a unit "
                "transformer steps up towards the root"
            )
        up = tr["hv_bus"]
        while up != root:
            if parent[up][1] == "trafo":
                raise AggregationError(
                    f"aggregation: transformers {parent[up][2]} and {name} are cascaded "
                    f"behind {root}; only one unit transformer level aggregates"
                )
            up = parent[up][0]

    def behind(bus):
        while bus != root:
            if parent[bus][1] == "trafo":
                return parent[bus][2]
            bus = parent[bus][0]
        return None

    levels = {behind(u["bus"]) is not None for u in units.values()}
    if len(levels) > 1:
        raise AggregationError(
            f"aggregation: behind {root} some units sit behind unit transformers and "
            "some do not: units at two voltage levels"
        )
    return nodes, parent, units, [n for _, n in trafos]


def _subtree_p(parent, units, root):
    down = {}
    for u in units.values():
        bus = u["bus"]
        p = _v(u["p_mw"])
        while bus != root:
            down[bus] = down.get(bus, 0.0) + p
            bus = parent[bus][0]
    return down


def _collector(c, root, parent, units, down, p_tot, assumptions):
    """The equivalent cable (spec dict), or None when the tree has none."""
    v_root = _bus_kv(c, root)
    cables = [(v, name) for v, (u, kind, name) in parent.items() if kind == "cable"]
    if not cables:
        return None
    r = x = cap = 0.0
    r0 = x0 = c0 = 0.0
    fields, zero_fields, missing = [], [], []
    i_max = 0.0
    for v, name in cables:
        cb = c["cables"][name]
        n = float(cb.get("parallel", 1))
        ln = _v(cb["length_km"])
        refer = (v_root / _bus_kv(c, v)) ** 2
        w = (down.get(v, 0.0) / p_tot) ** 2
        r += _v(cb["r_ohm_per_km"]) * ln / n * refer * w
        x += _v(cb["x_ohm_per_km"]) * ln / n * refer * w
        cap += _v(cb["c_nf_per_km"]) * ln * n / refer
        fields += [
            cb["length_km"],
            cb["r_ohm_per_km"],
            cb["x_ohm_per_km"],
            cb["c_nf_per_km"],
        ]
        if _bus_kv(c, v) != v_root:
            # behind a unit transformer: its delta winding blocks the zero sequence
            pass
        elif all(f in cb for f in ("r0_ohm_per_km", "x0_ohm_per_km", "c0_nf_per_km")):
            r0 += _v(cb["r0_ohm_per_km"]) * ln / n * refer * w
            x0 += _v(cb["x0_ohm_per_km"]) * ln / n * refer * w
            c0 += _v(cb["c0_nf_per_km"]) * ln * n / refer
            zero_fields += [
                cb["r0_ohm_per_km"],
                cb["x0_ohm_per_km"],
                cb["c0_nf_per_km"],
            ]
        else:
            missing.append(name)
        if parent[v][0] == root:
            i_max += _v(cb["max_i_ka"]) * n
            fields.append(cb["max_i_ka"])
    if i_max == 0.0:
        # every cable is below a unit transformer: the LV runs' rating, referred to the root kV
        i_max = sum(
            _v(c["cables"][n]["max_i_ka"])
            * float(c["cables"][n].get("parallel", 1))
            * _bus_kv(c, v)
            / v_root
            for v, n in cables
        )
        fields += [c["cables"][n]["max_i_ka"] for _, n in cables]
    out = {
        "length_km": _tag(1.0, *fields),
        "r_ohm_per_km": _tag(r, *fields),
        "x_ohm_per_km": _tag(x, *fields),
        "c_nf_per_km": _tag(cap, *fields),
        "max_i_ka": _tag(i_max, *fields),
    }
    if missing or not zero_fields:
        assumptions.append(
            f"aggregation: the equivalent collector behind {root} has no zero sequence: "
            + (
                f"cable(s) {sorted(missing)} carry none"
                if missing
                else "no collector-side cable"
            )
        )
    else:
        out.update(
            r0_ohm_per_km=_tag(r0, *zero_fields),
            x0_ohm_per_km=_tag(x0, *zero_fields),
            c0_nf_per_km=_tag(c0, *zero_fields),
        )
    return out


_TRAFO_SAME = ("vn_hv_kv", "vn_lv_kv")
_TRAFO_FLAGS = ("vector_group", "earthing_hv", "earthing_lv")


def _unit_transformer(c, root, trafos, down, p_tot, assumptions):
    first = c["transformers"][trafos[0]]
    for name in trafos[1:]:
        tr = c["transformers"][name]
        for f in _TRAFO_SAME:
            if _v(tr[f]) != _v(first[f]):
                raise AggregationError(
                    f"aggregation: unit transformers {trafos[0]} and {name} behind {root} "
                    f"differ in rated voltage {f} ({_v(first[f])} vs {_v(tr[f])} kV)"
                )
        for f in _TRAFO_FLAGS:
            if tr.get(f) != first.get(f):
                raise AggregationError(
                    f"aggregation: unit transformers {trafos[0]} and {name} behind {root} "
                    f"differ in {f} ({first.get(f)!r} vs {tr.get(f)!r})"
                )
        for f in ("r_n_hv_ohm", "r_n_lv_ohm"):
            if f in first and _v(tr[f]) != _v(first[f]):
                raise AggregationError(
                    f"aggregation: unit transformers {trafos[0]} and {name} behind {root} "
                    f"differ in {f}"
                )
    lv_levels = {_bus_kv(c, c["transformers"][n]["lv_bus"]) for n in trafos}
    if len(lv_levels) > 1:
        raise AggregationError(
            f"aggregation: unit transformers behind {root} feed two LV levels "
            f"{sorted(lv_levels)} kV"
        )
    v_hv = _v(first["vn_hv_kv"])
    z_re = z_im = 0.0
    sn = pfe = i0w = 0.0
    fields = []
    for name in trafos:
        tr = c["transformers"][name]
        s = _v(tr["sn_mva"])
        zbase = v_hv**2 / s
        zk = _v(tr["vk_percent"]) / 100 * zbase
        rk = _v(tr["vkr_percent"]) / 100 * zbase
        xk = math.sqrt(max(zk**2 - rk**2, 0.0))
        w = (down[tr["lv_bus"]] / p_tot) ** 2
        z_re += rk * w
        z_im += xk * w
        sn += s
        pfe += _v(tr["pfe_kw"])
        i0w += _v(tr["i0_percent"]) * s
        fields += [
            tr[f]
            for f in (
                "sn_mva",
                "vk_percent",
                "vkr_percent",
                "pfe_kw",
                "i0_percent",
                *_TRAFO_SAME,
            )
        ]
    zbase_eq = v_hv**2 / sn
    out = {
        "sn_mva": _tag(sn, *fields),
        "vn_hv_kv": copy.deepcopy(first["vn_hv_kv"]),
        "vn_lv_kv": copy.deepcopy(first["vn_lv_kv"]),
        "vk_percent": _tag(math.hypot(z_re, z_im) / zbase_eq * 100, *fields),
        "vkr_percent": _tag(z_re / zbase_eq * 100, *fields),
        "pfe_kw": _tag(pfe, *fields),
        "i0_percent": _tag(i0w / sn, *fields),
    }
    for f in _TRAFO_FLAGS + ("r_n_hv_ohm", "r_n_lv_ohm"):
        if f in first:
            out[f] = copy.deepcopy(first[f])
    taps = {n: c["transformers"][n].get("tap") for n in trafos}
    for n, tap in taps.items():
        if tap != taps[trafos[0]]:
            raise AggregationError(
                f"aggregation: unit transformers {trafos[0]} and {n} behind {root} differ in "
                "their tap: the effective ratios differ"
            )
    if taps[trafos[0]] is not None:
        out["tap"] = copy.deepcopy(taps[trafos[0]])
    return out


def _equivalent_units(units, bus, prefix):
    out, umap = {}, {}
    for kind in CONVERTER_KINDS:
        members = sorted(uid for uid, u in units.items() if u["kind"] == kind)
        if not members:
            continue
        name = f"{prefix}_{_KIND_TAG[kind]}"
        eq = {"kind": kind, "bus": bus}
        s_tot = sum(_v(units[m]["s_mva"]) for m in members)
        for f in _SUMMED:
            fs = [units[m][f] for m in members if f in units[m]]
            if fs:
                eq[f] = _tag(sum(_v(x) for x in fs), *fs)
        for f in _WEIGHTED:
            fs = [units[m][f] for m in members if f in units[m]]
            if fs:
                ss = [units[m]["s_mva"] for m in members]
                eq[f] = _tag(
                    sum(_v(units[m][f]) * _v(units[m]["s_mva"]) for m in members)
                    / s_tot,
                    *fs,
                    *ss,
                )
        out[name] = eq
        umap[name] = members
    return out, umap


def aggregate(campus_spec) -> Aggregation:
    """
    The aggregated equivalent of ``campus_spec`` (see the module
    docstring). The input is not changed.
    """
    if not isinstance(campus_spec, dict) or not isinstance(
        campus_spec.get("campus"), dict
    ):
        raise ContractError("aggregation: expected a mapping with a 'campus' key")
    src = campus_spec["campus"]
    _check_ratings(src)
    build_campus(campus_spec)
    c = copy.deepcopy(src)
    pcc = c["pcc"]["bus"]
    trafos = c.get("transformers") or {}
    main = {n for n, tr in trafos.items() if pcc in (tr["hv_bus"], tr["lv_bus"])}
    roots = sorted(
        {
            tr["lv_bus"] if tr["hv_bus"] == pcc else tr["hv_bus"]
            for n, tr in trafos.items()
            if n in main
        }
    )
    assumptions = []
    unit_map = {}
    plans = []
    for root in roots:
        plan = _plan(c, root, main, set(roots) - {root})
        if plan is not None:
            plans.append((root, *plan))
    if not plans:
        raise AggregationError(
            "aggregation: no collector behind the main transformers holds a converter unit"
        )
    taken = {
        *c.get("buses", {}),
        pcc,
        *trafos,
        *(c.get("cables") or {}),
        *c["units"],
        *(x["name"] for x in c.get("compensation") or []),
        *(c.get("earthing_transformers") or {}),
        *(c.get("series_capacitors") or {}),
    }
    removed_buses, removed_branches = set(), set()
    for i, (root, nodes, parent, units, utr) in enumerate(plans):
        prefix = "EQ" if len(plans) == 1 else f"EQ{i + 1}"
        p_tot = sum(_v(u["p_mw"]) for u in units.values())
        down = _subtree_p(parent, units, root)
        cable = _collector(c, root, parent, units, down, p_tot, assumptions)
        tr = _unit_transformer(c, root, utr, down, p_tot, assumptions) if utr else None
        names = {
            "mv": f"{prefix}_MV",
            "lv": f"{prefix}_LV",
            "cable": f"{prefix}_COL",
            "trafo": f"{prefix}_TR",
        }
        below = set(nodes) - {root}
        branch_names = {name for (_, _, name) in parent.values()}
        made = {names["mv"], names["cable"]} if cable is not None else set()
        made |= {names["lv"], names["trafo"]} if tr is not None else set()
        clash = sorted(made & (taken - below - branch_names))
        if clash:
            raise AggregationError(
                f"aggregation: the equivalent name(s) {clash} are already taken"
            )
        lv_kv = _bus_kv(c, c["transformers"][utr[0]]["lv_bus"]) if utr else None
        # the tree goes before its equivalent comes in: a name may be reused
        for u in units:
            del c["units"][u]
        for b in below:
            del c["buses"][b]
        for name in branch_names:
            c.get("cables", {}).pop(name, None)
            c["transformers"].pop(name, None)
        mv_bus = root
        if cable is not None:
            c["buses"][names["mv"]] = {"vn_kv": _bus_kv(c, root)}
            c.setdefault("cables", {})[names["cable"]] = {
                "from_bus": root,
                "to_bus": names["mv"],
                **cable,
            }
            mv_bus = names["mv"]
        unit_bus = mv_bus
        if tr is not None:
            c["buses"][names["lv"]] = {"vn_kv": lv_kv}
            c["transformers"][names["trafo"]] = {
                "hv_bus": mv_bus,
                "lv_bus": names["lv"],
                **tr,
            }
            unit_bus = names["lv"]
        eq_units, umap = _equivalent_units(units, unit_bus, prefix)
        clash = sorted(set(eq_units) & (taken - set(units)))
        if clash:
            raise AggregationError(
                f"aggregation: the equivalent name(s) {clash} are already taken"
            )
        c["units"].update(eq_units)
        unit_map.update(umap)
        removed_buses |= below
        removed_branches |= branch_names
    unit_map.update({uid: [uid] for uid in c["units"] if uid not in unit_map})
    _drop_dangling(c, removed_buses, removed_branches, assumptions)
    return Aggregation(
        spec={"campus": c},
        unit_map=dict(sorted(unit_map.items())),
        assumptions=assumptions,
    )


def _drop_dangling(c, buses, branches, assumptions):
    relays = c.get("relays") or []
    kept = [
        r
        for r in relays
        if r.get("branch") not in branches and r.get("fault_bus") not in buses
    ]
    if len(kept) != len(relays):
        dropped = sorted(r["name"] for r in relays if r not in kept)
        assumptions.append(
            f"aggregation: relay(s) {dropped} protect collector branches the equivalent "
            "replaces; not carried"
        )
        c["relays"] = kept
    had_island = "island" in c or any("island" in u for u in c["units"].values())
    if had_island:
        c.pop("island", None)
        for u in c["units"].values():
            u.pop("island", None)
        assumptions.append(
            "aggregation: the island data is not carried; the aggregated model is the "
            "grid-connected plant (TRGC 01/02)"
        )


def aggregate_campus(campus_spec):
    """
    ``(Campus, Aggregation)``: the aggregated campus built, with the
    aggregation's assumptions added to its own.
    """
    agg = aggregate(campus_spec)
    campus = build_campus(agg.spec)
    campus.assumptions.extend(agg.assumptions)
    return campus, agg
