"""
PSS/E case import: a ``.raw`` file becomes a ``pypsa.Network`` (owner decision IMPORTS-1).

``read_psse(paths, options)`` reads one RAW file with ``psse_raw_parse`` and maps
its records onto PyPSA components. This module is the only one in the PSS/E
import that imports ``pypsa`` (engine cage, owner ruling 2026-11-17).

**Mapping.** Values are converted by ``psse_raw_parse`` (see its docstring for
the PSS/E conventions and their source).

- Bus: ``v_nom`` = BASKV, ``v_mag_pu_set`` = VM, or VS of a generator that holds
  it. The bus is named by its RAW NAME when that is unique and not blank,
  otherwise ``"<NAME> #<number>"`` or ``"Bus <number>"``. The PSS/E number, area,
  zone, owner and bus type stay as columns.
- Load: P and Q at 1.0 pu voltage (constant power + current + admittance parts);
  the parts stay as columns and a load with voltage-dependent parts is listed in
  ``assumed``.
- Fixed shunt: a ``ShuntImpedance`` in siemens. Switched shunt: a
  ``ShuntImpedance`` held at its initial setting BINIT (listed in ``assumed``);
  its blocks and control band stay as columns.
- Generator: ``p_set`` = PG, ``q_set`` = QG, ``p_nom`` = PT (MBASE when PT is the
  9999 "no limit" default, listed in ``assumed``). Control: the first in-service
  machine on the swing bus is ``Slack``; a machine on a generator or swing bus
  with QT > QB is ``PV``; any other is ``PQ``. Q limits, MBASE, VS, the regulated
  bus and the source impedance stay as columns. A machine regulating a remote
  bus is modelled holding its own terminal (``assumed``).
- Branch: a ``Line`` in ohms and siemens on the from-bus base. The line-end
  shunts GI/BI/GJ/BJ become ``ShuntImpedance`` rows at each end, so they stay
  exact. A branch joining buses of different base kV becomes a 1:1 transformer
  (``warnings``). A branch with R = X = 0 gets X = THRSHZ (0.0001 pu).
- Two-winding transformer: a ``Transformer`` from the winding-1 bus, ``r``/``x``
  on ``s_nom``, ``tap_ratio`` the ideal ratio at bus0, ``phase_shift`` = ANG1.
  The magnetising admittance is an exact ``ShuntImpedance`` at the winding-1 bus,
  never split across both ends (GK A ruling, 2026-11-17).
- Three-winding transformer: a star bus (``v_nom`` = winding-1 bus kV, start
  voltage VMSTAR) and one ``Transformer`` per winding from its bus to the star.
- Areas, zones and owners: bus columns, and their tables in ``psse_case.json``.
- Everything else in the file is listed in ``unmapped`` by the reader.

``s_nom`` is the PSS/E rating RATEA (v33) or RATE1 (v35/v36). A line without a
rating keeps ``s_nom`` = 0 and a transformer without one takes its impedance
base SBASE1-2 as ``s_nom``; both are listed in ``assumed``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pypsa

from gridspine.ingest.import_result import (
    Assumed,
    ImportFormatError,
    ImportReport,
    ImportResult,
    Unmapped,
)
from gridspine.ingest.psse_raw_parse import (
    RawCase,
    branch_physical,
    load_at_nominal,
    read_raw_text,
    shunt_siemens,
    transformer_windings,
)
from gridspine.schema.contracts import ContractError

FORMAT_ID = "psse"
THRSHZ = 1e-4  # PSS/E's zero-impedance threshold, pu
_NO_LIMIT = 9999.0
_BUS_TYPE = {1: "load bus", 2: "generator bus", 3: "swing bus", 4: "isolated"}


def read_psse(paths: Sequence[Path], options: Mapping[str, Any]) -> ImportResult:
    paths = [Path(p) for p in paths]
    raws = [p for p in paths if p.suffix.lower() == ".raw"]
    if len(raws) != 1:
        raise ImportFormatError(
            f"a PSS/E import needs exactly one .raw file, got {len(raws)}: "
            f"{', '.join(p.name for p in paths) or 'none'}"
        )
    raw = raws[0]
    try:
        case = read_raw_text(
            raw.read_text(encoding="latin-1"), version=options.get("version")
        )
    except ContractError as exc:
        raise ImportFormatError(f"{raw.name}: {exc}") from exc
    except OSError as exc:
        raise ImportFormatError(f"{raw.name}: cannot be read ({exc.strerror})") from exc
    try:
        net, counts, assumed, warnings = build_network(case)
    except ContractError as exc:
        raise ImportFormatError(f"{raw.name}: {exc}") from exc
    unmapped = [Unmapped(item, reason) for item, reason in case.unmapped]
    for p in paths:
        if p is not raw:
            unmapped.append(
                Unmapped(
                    p.name,
                    "only the .raw case is read so far; "
                    "dynamic data (.dyr) import is not available yet",
                )
            )
    assumed = [Assumed(*a) for a in case.assumed] + assumed
    report = ImportReport(
        source_format=FORMAT_ID,
        source_files=[p.name for p in paths],
        counts=counts,
        unmapped=unmapped,
        assumed=assumed,
        warnings=case.warnings + warnings,
    )
    return ImportResult(
        network=net, report=report, sidecars={"psse_case.json": case_summary(case)}
    )


def case_summary(case: RawCase) -> dict:
    """The case header and the area, zone and owner tables, JSON-ready."""

    def rows(name, cols):
        t = case.tables.get(name)
        if t is None or not len(t):
            return []
        return [
            {
                c: (
                    int(r[c])
                    if isinstance(r[c], int | float) and c in ("I", "ISW")
                    else r[c]
                )
                for c in cols
            }
            for r in t.to_dict("records")
        ]

    out = {
        "psse_version": case.version,
        "system_base_mva": case.sbase,
        "base_frequency_hz": case.basfrq,
        "title": case.title,
        "areas": rows("area", ["I", "ARNAME", "ISW", "PDES", "PTOL"]),
        "zones": rows("zone", ["I", "ZONAME"]),
        "owners": rows("owner", ["I", "OWNAME"]),
    }
    json.dumps(out, allow_nan=False)
    return out


def _names(t, num_key, name_key, fallback):
    """Unique display names: the RAW name when unique and not blank."""
    names = [str(n).strip() for n in t[name_key]] if name_key in t else [""] * len(t)
    seen = pd.Series(names).value_counts()
    out = []
    for num, nm in zip(t[num_key], names):
        if nm and seen[nm] == 1:
            out.append(nm)
        elif nm:
            out.append(f"{nm} #{num}")
        else:
            out.append(f"{fallback} {num}")
    return out


def build_network(case: RawCase):
    """``(network, counts, assumed, warnings)`` from a read case."""
    n = pypsa.Network()
    n.name = case.title or "PSS/E import"
    sb = case.sbase
    assumed: list[Assumed] = []
    warnings: list[str] = []
    t = case.tables

    # ---------------------------------------------------------- buses
    bus = t["bus"]
    names = _names(bus, "I", "NAME", "Bus")
    bname = dict(zip(bus["I"], names))
    kv = dict(zip(bus["I"], bus["BASKV"]))
    ide = dict(zip(bus["I"], bus["IDE"]))
    area_name = _lookup(t.get("area"), "ARNAME")
    zone_name = _lookup(t.get("zone"), "ZONAME")
    owner_name = _lookup(t.get("owner"), "OWNAME")
    n.add(
        "Bus",
        names,
        v_nom=bus["BASKV"].values,
        v_mag_pu_set=bus["VM"].values,
        carrier="AC",
    )
    n.buses.loc[names, "psse_number"] = bus["I"].values
    n.buses.loc[names, "bus_type"] = [_BUS_TYPE.get(i, f"type {i}") for i in bus["IDE"]]
    n.buses.loc[names, "area"] = [area_name.get(a, str(a)) for a in bus["AREA"]]
    n.buses.loc[names, "zone"] = [zone_name.get(z, str(z)) for z in bus["ZONE"]]
    n.buses.loc[names, "owner"] = [owner_name.get(o, str(o)) for o in bus["OWNER"]]
    n.buses.loc[names, "v_ang_start_deg"] = bus["VA"].values
    n.buses["psse_number"] = n.buses["psse_number"].astype(int)
    isolated = {b for b, i in ide.items() if i == 4}
    if isolated:
        warnings.append(
            f"{len(isolated)} bus(es) marked isolated in the file; everything "
            "connected to them is imported switched off"
        )

    def live(bus_no, status):
        return bool(status) and bus_no not in isolated

    # ---------------------------------------------------------- loads
    ld = t["load"]
    if len(ld):
        lnames = [f"Load {bname[b]} {i}" for b, i in zip(ld["I"], ld["ID"])]
        p, q = load_at_nominal(ld)
        n.add(
            "Load",
            lnames,
            bus=[bname[b] for b in ld["I"]],
            p_set=p.values,
            q_set=q.values,
            active=[live(b, s) for b, s in zip(ld["I"], ld["STATUS"])],
        )
        for col, src in (
            ("p_constant_power_mw", "PL"),
            ("q_constant_power_mvar", "QL"),
            ("p_constant_current_mw", "IP"),
            ("q_constant_current_mvar", "IQ"),
            ("p_constant_admittance_mw", "YP"),
            ("q_constant_admittance_mvar", "YQ"),
        ):
            n.loads.loc[lnames, col] = ld[src].values
        n.loads.loc[lnames, "psse_id"] = ld["ID"].values
        for nm, r in zip(lnames, ld.to_dict("records")):
            if any(r[k] for k in ("IP", "IQ", "YP", "YQ")):
                assumed.append(
                    Assumed(
                        nm,
                        "voltage dependence",
                        "taken at 1.0 pu",
                        "the load has constant-current or constant-admittance parts; "
                        "they are added to its demand at nominal voltage",
                    )
                )

    # ---------------------------------------------------------- shunts
    shunts = []  # (name, bus_no, g_S, b_S, active, kind)
    for r in t["fixed_shunt"].to_dict("records"):
        g, b = shunt_siemens(kv[r["I"]], r["GL"], r["BL"])
        shunts.append(
            (
                f"Shunt {bname[r['I']]} {r['ID']}",
                r["I"],
                g,
                b,
                live(r["I"], r["STATUS"]),
                "fixed",
            )
        )
    for k, r in enumerate(t["switched_shunt"].to_dict("records")):
        sid = r.get("ID", str(k + 1))
        nm = f"Switched shunt {bname[r['I']]} {sid}"
        g, b = shunt_siemens(kv[r["I"]], 0.0, r["BINIT"])
        shunts.append((nm, r["I"], g, b, live(r["I"], r["STAT"]), "switched"))
        assumed.append(
            Assumed(
                nm,
                "susceptance setting",
                float(r["BINIT"]),
                "held at its initial setting BINIT (Mvar at 1.0 pu); "
                "voltage-controlled switching is not modelled",
            )
        )

    # ---------------------------------------------------------- generators
    gen = t["generator"]
    if len(gen):
        gnames = [f"Gen {bname[b]} {i}" for b, i in zip(gen["I"], gen["ID"])]
        controls, p_nom, p_min = [], [], []
        slack_taken = set()
        for nm, r in zip(gnames, gen.to_dict("records")):
            on = live(r["I"], r["STAT"])
            typ = ide[r["I"]]
            if typ == 3 and on and r["I"] not in slack_taken:
                controls.append("Slack")
                slack_taken.add(r["I"])
            elif typ in (2, 3) and r["QT"] > r["QB"]:
                controls.append("PV")
            else:
                controls.append("PQ")
            if 0 < r["PT"] < _NO_LIMIT:
                pn = r["PT"]
            else:
                pn = r["MBASE"]
                assumed.append(
                    Assumed(
                        nm,
                        "rated power (MW)",
                        float(pn),
                        f"maximum output PT is {r['PT']:g} in the file; the machine "
                        "base MBASE is used",
                    )
                )
            p_nom.append(pn)
            p_min.append(r["PB"] / pn if (pn > 0 and r["PB"] > -_NO_LIMIT) else 0.0)
            reg = r["IREG"]
            if controls[-1] != "PQ" and reg not in (0, r["I"]):
                assumed.append(
                    Assumed(
                        nm,
                        "regulated bus",
                        bname[r["I"]],
                        f"the file regulates remote bus {reg}; the machine holds "
                        "its own terminal at VS",
                    )
                )
        n.add(
            "Generator",
            gnames,
            bus=[bname[b] for b in gen["I"]],
            control=controls,
            p_nom=p_nom,
            p_set=gen["PG"].values,
            q_set=gen["QG"].values,
            p_min_pu=p_min,
            active=[live(b, s) for b, s in zip(gen["I"], gen["STAT"])],
        )
        for col, src in (
            ("q_max_mvar", "QT"),
            ("q_min_mvar", "QB"),
            ("v_set_pu", "VS"),
            ("machine_base_mva", "MBASE"),
            ("source_r_pu", "ZR"),
            ("source_x_pu", "ZX"),
            ("p_max_mw", "PT"),
            ("p_min_mw", "PB"),
            ("regulated_bus", "IREG"),
            ("q_share_percent", "RMPCT"),
            ("wind_control_mode", "WMOD"),
        ):
            n.generators.loc[gnames, col] = gen[src].values
        n.generators.loc[gnames, "psse_id"] = gen["ID"].values
        for nm, ctl, r in zip(gnames, controls, gen.to_dict("records")):
            if ctl != "PQ" and live(r["I"], r["STAT"]):
                n.buses.at[bname[r["I"]], "v_mag_pu_set"] = r["VS"]

    # ---------------------------------------------------------- branches
    br = t["branch"]
    rate1 = "RATE1" if case.version >= 35 else "RATEA"
    n_lines = n_branch_tr = 0
    if len(br):
        branch_names = _branch_names(br, bname, case.version)
        for nm, r in zip(branch_names, br.to_dict("records")):
            i, j = r["I"], r["J"]
            on = live(i, r["ST"]) and live(j, r["ST"])
            x = r["X"]
            if r["R"] == 0 and x == 0:
                x = THRSHZ
                assumed.append(
                    Assumed(
                        nm,
                        "reactance (pu)",
                        THRSHZ,
                        "R = X = 0 in the file (a zero-impedance jumper); PSS/E's "
                        "threshold THRSHZ is used",
                    )
                )
                r = {**r, "X": x}
            rate = r[rate1]
            if kv[i] != kv[j]:
                s_nom = rate if rate > 0 else sb
                n.add(
                    "Transformer",
                    nm,
                    bus0=bname[i],
                    bus1=bname[j],
                    s_nom=s_nom,
                    model="pi",
                    r=r["R"] * s_nom / sb,
                    x=r["X"] * s_nom / sb,
                    b=r["B"] * s_nom / sb,
                    tap_ratio=1.0,
                    active=on,
                )
                warnings.append(
                    f"{nm} joins buses of {kv[i]:g} kV and {kv[j]:g} kV; imported as a "
                    "1:1 transformer with the line's per-unit impedance"
                )
                n_branch_tr += 1
                phys = branch_physical(case, r)
            else:
                phys = branch_physical(case, r)
                n.add(
                    "Line",
                    nm,
                    bus0=bname[i],
                    bus1=bname[j],
                    r=phys["r_ohm"],
                    x=phys["x_ohm"],
                    b=phys["b_siemens"],
                    s_nom=rate,
                    active=on,
                    length=r["LEN"],
                )
                n.lines.at[nm, "psse_circuit"] = r["CKT"]
                n_lines += 1
                if rate <= 0:
                    assumed.append(
                        Assumed(
                            nm,
                            "thermal rating (MVA)",
                            0.0,
                            "no rating in the file; left at 0 for you to set",
                        )
                    )
            for end, bno in (("from", i), ("to", j)):
                g, b = phys[f"g_{end}_siemens"], phys[f"b_{end}_siemens"]
                if g or b:
                    # line-end shunts are on the from-bus base; re-base to this end's kV
                    scale = (kv[i] / kv[bno]) ** 2
                    shunts.append(
                        (
                            f"{nm} {end}-end shunt",
                            bno,
                            g * scale,
                            b * scale,
                            on,
                            "line end",
                        )
                    )

    # ---------------------------------------------------------- transformers
    tr = t["transformer"]
    n2 = n3 = 0
    used_labels = set(n.lines.index) | set(n.transformers.index)
    for r in tr.to_dict("records"):
        three = bool(r["K"])
        label = r["NAME"].strip() or (
            f"Transformer {r['I']}-{r['J']}-{r['K']} {r['CKT']}"
            if three
            else f"Transformer {r['I']}-{r['J']} {r['CKT']}"
        )
        base, k = label, 1
        while label in used_labels:
            k += 1
            label = f"{base} ({k})"
        used_labels.add(label)
        windings = transformer_windings(case, r)
        if three:
            star = f"{label} star"
            n.add("Bus", star, v_nom=kv[r["I"]], v_mag_pu_set=r["VMSTAR"], carrier="AC")
            n.buses.at[star, "psse_number"] = 0
            n.buses.at[star, "bus_type"] = "three-winding star point"
            n.buses.at[star, "v_ang_start_deg"] = r["ANSTAR"]
            n3 += 1
        else:
            n2 += 1
        for w in windings:
            nm = label if not three else f"{label} winding {w['winding']}"
            to = bname[w["to"]] if w["to"] is not None else star
            on = (
                w["in_service"]
                and live(w["bus"], 1)
                and (w["to"] is None or live(w["to"], 1))
            )
            s_nom = w["rate_mva"]
            if s_nom <= 0:
                base_key = (
                    "SBASE1-2"
                    if w["winding"] in (0, 1)
                    else ("SBASE2-3" if w["winding"] == 2 else "SBASE3-1")
                )
                s_nom = r[base_key]
                assumed.append(
                    Assumed(
                        nm,
                        "rating (MVA)",
                        float(s_nom),
                        f"no rating in the file; its impedance base {base_key} is used",
                    )
                )
            n.add(
                "Transformer",
                nm,
                bus0=bname[w["bus"]],
                bus1=to,
                s_nom=s_nom,
                model="pi",
                r=w["r_pu"] * s_nom / sb,
                x=w["x_pu"] * s_nom / sb,
                g=0.0,
                b=0.0,
                tap_ratio=w["tap"],
                tap_side=0,
                phase_shift=w["shift_deg"],
                active=on,
            )
            n.transformers.at[nm, "psse_circuit"] = r["CKT"]
            n.transformers.at[nm, "vector_group"] = r.get("VECGRP", "")
            if w["g_pu"] or w["b_pu"]:
                k1 = kv[w["bus"]]
                shunts.append(
                    (
                        f"{label} magnetising",
                        w["bus"],
                        w["g_pu"] * sb / k1**2,
                        w["b_pu"] * sb / k1**2,
                        on,
                        "magnetising",
                    )
                )

    if shunts:
        sn = _unique([s[0] for s in shunts])
        shunts = [(nm, *s[1:]) for nm, s in zip(sn, shunts)]
        n.add(
            "ShuntImpedance",
            sn,
            bus=[bname[s[1]] for s in shunts],
            g=[s[2] for s in shunts],
            b=[s[3] for s in shunts],
            active=[s[4] for s in shunts],
        )
        n.shunt_impedances.loc[sn, "shunt_kind"] = [s[5] for s in shunts]
    sw = t["switched_shunt"]
    if len(sw):
        swn = [s[0] for s in shunts if s[5] == "switched"]
        n.shunt_impedances.loc[swn, "v_high_pu"] = sw["VSWHI"].values
        n.shunt_impedances.loc[swn, "v_low_pu"] = sw["VSWLO"].values
        n.shunt_impedances.loc[swn, "control_mode"] = sw["MODSW"].values
        n.shunt_impedances.loc[swn, "blocks_mvar"] = [
            "; ".join(
                f"{int(r[f'N{k}'])}x{r[f'B{k}']:g}" for k in range(1, 9) if r[f"N{k}"]
            )
            for r in sw.to_dict("records")
        ]

    lone = _lone_buses(n)
    if lone:
        warnings.append(
            f"{len(lone)} bus(es) have no in-service connection and no generator "
            f"({', '.join(lone[:5])}{', ...' if len(lone) > 5 else ''}); PyPSA's power flow "
            "cannot solve such a bus on its own, so switch it out of the study or connect it"
        )

    counts = {
        "Buses": len(bus),
        "Loads": len(ld),
        "Generators": len(gen),
        "Lines": n_lines,
        "Branches imported as transformers": n_branch_tr,
        "Two-winding transformers": n2,
        "Three-winding transformers": n3,
        "Fixed shunts": len(t["fixed_shunt"]),
        "Switched shunts": len(sw),
        "Areas": len(t.get("area", [])),
        "Zones": len(t.get("zone", [])),
        "Owners": len(t.get("owner", [])),
    }
    counts = {k: int(v) for k, v in counts.items() if v}
    return n, counts, assumed, warnings


def _lone_buses(n):
    """
    Buses that form an island of one with no active generator (PyPSA 1.x's
    single-bus power flow needs a generator there).
    """
    n.determine_network_topology()
    sizes = n.buses["sub_network"].value_counts()
    gen_buses = set(n.generators.loc[n.generators["active"], "bus"])
    return [
        b
        for b, sub in n.buses["sub_network"].items()
        if sizes[sub] == 1 and b not in gen_buses
    ]


def _unique(names):
    """Names made unique in order: a repeat gets " (2)", " (3)", ..."""
    seen, out = {}, []
    for nm in names:
        k = seen.get(nm, 0) + 1
        seen[nm] = k
        out.append(nm if k == 1 else f"{nm} ({k})")
    return out


def _lookup(t, col):
    if t is None or not len(t):
        return {}
    return {int(i): (str(nm).strip() or str(int(i))) for i, nm in zip(t["I"], t[col])}


def _branch_names(br, bname, version):
    out = []
    for r in br.to_dict("records"):
        nm = str(r.get("NAME", "") or "").strip() if version >= 35 else ""
        out.append(nm or f"Line {bname[r['I']]} - {bname[r['J']]} {r['CKT']}")
    dup = pd.Series(out).duplicated(keep=False)
    return [
        f"{nm} ({r['I']}-{r['J']} {r['CKT']})" if d else nm
        for nm, d, r in zip(out, dup, br.to_dict("records"))
    ]
