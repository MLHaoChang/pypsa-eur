# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
The PSS/E v35 study pack for one campus island hour (GC-04).

Written next to the campus bundle, in its own folder:

* ``<case>.raw`` (v35), ``<case>.seq`` (v35, maximum short-circuit case) and
  ``<case>.dyr`` (v35): the bundle's synchronous and grid records, plus WECC
  converter records for every grid-following converter;
* ``<case>.con``, ``.sub`` and ``.mon``: the contingency set given, or else
  the generated branch, unit and bus-section set;
* ``run_<case_id>.py``: one psspy script per test-battery case, written only
  for the aggregated plant (GC-01), or a detailed plant (GC-04b), which
  carries the PoC branch. Each writes
  ``<case_id>.out``, ``<case_id>.csv`` (battery contract
  :data:`PSSE_CONTRACT_VERSION`, ``connected`` included) and
  ``<case_id>.status.txt`` (failed status polls, text only);
* ``README.md`` and ``manifest.json``.

A grid-following converter with no supplied dynamic parameters gets the
public generic WECC set (``templates/wecc_generic.py``, owner decision
GC04-DEFAULTS-1). Every value is assumed, each unit's assumed groups are
listed, and the pack is marked ``NOT_FOR_SUBMISSION``. The campus bundle's
own files are unchanged.

The manifest cites the GC-01 model set (``model_set_id``, ``data_hash``,
``variant``, ``model_hash``) only when one is given (GK S3), and every script
is checked against the manifest's file list (GK S1).
"""

import dataclasses
import json
import re
from pathlib import Path

import pandas as pd

from gridspine.handoff.contingencies import conmap_path, write_con, write_mon, write_sub
from gridspine.handoff.dyr_writer import (
    PIN_NOTE,
    PocBranch,
    converter_records,
    machine_records,
    unequal_dispatch,
)
from gridspine.handoff.psspy_scripts import PENDING as PSSPY_NOTE
from gridspine.handoff.psspy_scripts import STATUS_SIDECAR, check_script, write_scripts
from gridspine.handoff.raw_writer import write_raw
from gridspine.handoff.seq_writer import NOT_WRITTEN, write_seq
from gridspine.handoff.test_battery import C_BRACKET_NOTE, build_battery
from gridspine.ingest.pandapower_source import registry_from_net
from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import manifest as model_set_manifest
from gridspine.static.contingency_set import (
    branch_contingencies,
    bus_section_contingencies,
    unit_contingencies,
)
from gridspine.templates.unit_params import IBR_PARAMS, REECC1_ONLY, WECC_GROUP_PARAMS
from gridspine.templates.wecc_generic import (
    BLOCKS,
    NOT_FOR_SUBMISSION,
    SOURCE,
    converter_set,
    groups,
    load_generic,
)
from gridspine.templates.wecc_generic import (
    PATH as GENERIC_PATH,
)

VERSION = 35
#: The battery contract version the pack's scripts write (GC-12): version 2,
#: since each script polls ``connected``; caller-given ``cases`` must be built at it.
PSSE_CONTRACT_VERSION = "2"
SUBDIR = "psse_v35"
BUSR_NOTE = "BUSR = BUS2, BUS1→BUS2 export: pending verification on first PSS/E load"
#: The .mon voltage band. The campus carries no planning band of its own, so
#: the pack uses this one and lists it as assumed.
MON_BAND_PU = (0.90, 1.10)
MON_ASSUMED = (
    ".mon: voltage band 0.90-1.10 pu (assumed; no planning band in the campus data)"
)
#: The test battery's grid zero sequence when the caller gives no cases: the
#: same Z0 = Z1 the .seq assumes for the grid, and X0/R0 at the grid's largest X/R.
BATTERY_ASSUMED = (
    "test battery: grid |Z0|/|Z1| = 1.0 (as the .seq's grid X0 = X1)",
    "test battery: grid X0/R0 = the grid's largest X/R",
    f"test battery: {C_BRACKET_NOTE}; c_min from the PoC kV (GC-14)",
)
#: GC-14: the psspy Thevenin source carries IEC 60909 c; older packs did not say.
C_NOTE = (
    "The psspy scripts' Thevenin source is |Z| = c·Un²/Sk'' with c_max = 1.1 on the "
    "Sk-min corners and c_min on the Sk-max corners. Packs written before GC-14 used "
    "c = 1 (Sk-min |Z| 10 % lower); regenerate them."
)
#: GC-04 PR6: the README says which transformer convention this pack uses, since
#: the manifest has no writer-version field to tell old packs apart.
TRAFO_NOTE = (
    "Transformer R/X are written at the winding's own voltage and PSS/E applies "
    "WINDV2² itself. Packs written before this fix carry an impedance too high by "
    "WINDV2² for every transformer with an LV tap or an LV rating off its bus "
    "voltage; regenerate them."
)
_SCRIPTS_NEED_PLANT = (
    "psspy scripts not written: the SMIB cases need the aggregated plant's PoC branch and "
    "machine (GC-01)"
)
_REPCA1_NEEDS_PLANT = (
    "plant controller (REPCA1) not written: no aggregated plant (GC-01) is given; a pack "
    "written by the campus bundle has none yet (issue 04c)"
)
#: The head line of a .dyr record: bus number, 'MODEL', 'ID'.
_DYR_HEAD = re.compile(r"^\s*(\d+)\s+'[^']*'\s+'([^']*)'")
#: Campus unit kinds behind a converter (a load, a genset and a UPS are not).
CONVERTER_KINDS = ("bess", "pv", "wind", "fuel_cell")
_COLUMNS = ["mbase_mva", *IBR_PARAMS, *REECC1_ONLY, *WECC_GROUP_PARAMS]


@dataclasses.dataclass(frozen=True)
class Plant:
    """
    The aggregated plant (GC-01): its converter's unit id and PoC branch.
    It takes the REPCA1 record, and the psspy scripts drive it.

    A detailed plant (GC-04b) names its converters in ``members``; ``unit_id``
    is then the plant's name. Every member gets a gain-shared REPCA1 on
    ``branch`` (``dyr_writer.share_repca1``), the scripts step all of them,
    and the status poll covers all of them.
    """

    unit_id: str
    branch: PocBranch
    members: tuple = ()

    @property
    def machine_ids(self) -> tuple:
        """The plant's converters: the members, or the aggregated machine."""
        return tuple(self.members) or (self.unit_id,)


def grid_following(campus, unit_id) -> bool:
    """
    A converter the campus bundle leaves without a dynamic record: not a
    genset or UPS, and not grid-forming (the bundle's own rule).
    """
    if unit_id not in campus.units.index:
        return False
    if campus.units.at[unit_id, "kind"] not in CONVERTER_KINDS:
        return False
    block = campus.island["units"].get(unit_id)
    return block is None or block.get("control") not in ("gfm_droop", "gfm_vsm")


def _no_record(campus, dyr_lines):
    """
    Every RAW machine with no record in the pack's .dyr, with the reason read
    from the campus data: not a campus unit (a compensator such as a STATCOM),
    a grid-forming converter, or a unit of a kind that got none.
    """
    heads = {
        (int(h[1]), h[2].strip())
        for h in (_DYR_HEAD.match(ln) for ln in dyr_lines)
        if h is not None
    }
    out = []
    for uid, num, mid, _mbase, _sync in machine_records(campus.net):
        if (num, mid.strip()) in heads:
            continue
        block = campus.island["units"].get(uid) or {}
        if uid not in campus.units.index:
            why = "not a campus unit (a compensator such as a STATCOM): no dynamic model supplied"
        elif block.get("control") in ("gfm_droop", "gfm_vsm"):
            why = (
                f"grid-forming converter ({block['control']}): no generic grid-forming model; "
                "its parameters are in the campus bundle's GFM table (gfm_models.csv) only"
            )
        else:
            why = f"{campus.units.at[uid, 'kind']}: no dynamic record supplied"
        out.append({"unit": uid, "bus": num, "id": mid.strip(), "reason": why})
    return out


def _battery_cases(net):
    g = net.ext_grid.iloc[0]
    for col in ("rx_max", "rx_min"):
        if not float(g[col]) > 0:
            raise ContractError(
                f"psse pack: the grid's {col}={float(g[col])!r} must be > 0 to build the test battery"
            )
    # rx_max/rx_min belong to the max/min short-circuit cases, not an ordered range
    xr_min, xr_max = sorted((1.0 / float(g["rx_max"]), 1.0 / float(g["rx_min"])))
    cases = build_battery(
        float(g["s_sc_min_mva"]),
        float(g["s_sc_max_mva"]),
        xr_min,
        xr_max,
        z0_over_z1=1.0,
        x0_over_r0=xr_max,
        contract_version=PSSE_CONTRACT_VERSION,
    )
    return cases, list(BATTERY_ASSUMED)


def _contingency_set(campus):
    return pd.concat(
        [
            branch_contingencies(campus.net),
            unit_contingencies(registry_from_net(campus.net)),
            bus_section_contingencies(campus.net),
        ],
        ignore_index=True,
    )


def write_psse_pack(
    out,
    campus,
    case,
    sync_lines,
    grid_machine,
    *,
    plant=None,
    cases=None,
    contingency_set=None,
    model_set=None,
    variant=None,
    generic=None,
) -> dict:
    """
    Write the pack into ``out`` and return its manifest (also written as
    ``manifest.json``).

    ``campus`` is the hour's campus (its ``net`` already set to the hour);
    ``sync_lines`` the bundle's synchronous and grid ``.dyr`` records;
    ``grid_machine`` the grid's ``(bus, id)``. ``plant`` (a ``Plant``) is the
    aggregated plant; ``cases`` the test battery (default: built from the
    grid's short-circuit range); ``contingency_set`` the ``.con`` input
    (default: the generated set); ``model_set``/``variant`` the GC-01 model
    set to cite; ``generic`` the generic WECC set (default: ``load_generic()``).
    """
    if (model_set is None) != (variant is None):
        raise ContractError(
            "psse pack: give the GC-01 model_set and its variant together, or neither"
        )
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    net = campus.net
    f_hz = float(net.f_hz)
    names = {ext: f"{case}.{ext}" for ext in ("raw", "seq", "dyr", "con", "sub", "mon")}

    write_raw(
        net,
        out / names["raw"],
        title=f"{case} PSS/E v35 pack",
        f_hz=f_hz,
        version=VERSION,
    )
    seq_notes = write_seq(
        campus, out / names["seq"], version=VERSION, case="max", f_hz=f_hz
    )

    generic = load_generic() if generic is None else generic
    records = machine_records(net)
    machines = {uid: (num, mid, mbase) for uid, num, mid, mbase, _ in records}
    sync = {uid for uid, *_, synchronous in records if synchronous}
    rows, assumed = {}, []
    for uid in machines:
        if uid in sync or not grid_following(campus, uid):
            continue
        battery = campus.units.at[uid, "kind"] == "bess"
        is_plant = plant is not None and uid in plant.machine_ids
        rows[uid] = {"mbase_mva": machines[uid][2]} | converter_set(
            generic, battery, is_plant
        )
        assumed.append(
            {
                "unit": uid,
                "models": list(
                    dict.fromkeys(BLOCKS[g][2] for g in groups(battery, is_plant))
                ),
                "source": SOURCE,
                "from": GENERIC_PATH.name,
            }
        )
    for uid in plant.machine_ids if plant is not None else ():
        if uid not in rows:
            raise ContractError(
                f"psse pack: plant unit {uid!r} is not a grid-following converter of this campus"
            )
    ibr = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=_COLUMNS)
    poc = {u: plant.branch for u in plant.machine_ids} if plant is not None else None
    plants = (
        {plant.unit_id: plant.members} if plant is not None and plant.members else None
    )
    conv_lines, _, repc = converter_records(
        net, ibr, version=VERSION, poc=poc, plants=plants
    )
    plant_assumed = unequal_dispatch(net, plants)
    with open(out / names["dyr"], "w", newline="\n") as f:
        f.write("".join(f"{ln}\n" for ln in [*sync_lines, *conv_lines]))
    no_record = _no_record(campus, [*sync_lines, *conv_lines])

    cs = _contingency_set(campus) if contingency_set is None else contingency_set
    con_skipped = write_con(cs, net, out / names["con"])
    write_sub(net, out / names["sub"])
    write_mon(out / names["mon"], *MON_BAND_PU)

    conmap_name = conmap_path(names["con"]).name if len(cs) > len(con_skipped) else None
    files = [
        *names.values(),
        *([conmap_name] if conmap_name else []),
        "README.md",
        "manifest.json",
    ]
    scripts, outputs, battery_notes = {}, {}, []
    if plant is not None:
        if cases is None:
            cases, battery_notes = _battery_cases(net)
        ids = plant.machine_ids
        _busr, bus1, bus2, ckt = repc[ids[0]]
        num, mid, _mbase = machines[ids[0]]
        mbase = sum(machines[u][2] for u in ids)  # the plant rating
        # the machines the RAW models for the plant, polled for connected (GK A P3);
        # each plant converter is one RAW machine record
        plant_machines = [(n, i) for u, n, i, _m, _s in records if u in ids]
        if len(plant_machines) != len(ids):
            raise ContractError(
                f"psse pack: plant {plant.unit_id!r} has {len(plant_machines)} RAW machine "
                f"records for {len(ids)} converters; each must be exactly one"
            )
        plant_machines = [machines[u][:2] for u in ids]  # plant_machine first
        grid_mbase = next(
            (
                m
                for n, i, m in machines.values()
                if grid_machine is not None and (n, i) == tuple(grid_machine)
            ),
            None,
        )
        if grid_mbase is None:
            raise ContractError(
                f"psse pack: grid machine {grid_machine!r} is not a machine of the RAW"
            )
        scripts = write_scripts(
            cases,
            out,
            raw=names["raw"],
            seq=names["seq"],
            dyr=names["dyr"],
            poc_bus=bus2,
            poc_kv=float(net.bus.at[plant.branch.grid_bus, "vn_kv"]),
            grid_machine=tuple(grid_machine),
            grid_mbase_mva=grid_mbase,
            f0_hz=f_hz,
            plant_branch=(bus2, bus1, ckt),  # measured at the PoC (GK A2)
            plant_machine=(num, mid),
            plant_mbase_mva=mbase,
            contract_version=PSSE_CONTRACT_VERSION,
            status_machines=plant_machines,
            plant_machines=plant_machines if plant.members else None,
        )
        outputs = {
            cid: [f"{cid}.out", f"{cid}.csv", STATUS_SIDECAR.format(case_id=cid)]
            for cid in scripts
        }
        files += sorted(scripts.values())
        allowed = set(files) | {f for pair in outputs.values() for f in pair}
        for c in cases:
            check_script((out / scripts[c.case_id]).read_text(), allowed, c.channels)

    manifest = {
        "case": case,
        "psse_version": VERSION,
        "not_for_submission": NOT_FOR_SUBMISSION if assumed else None,
        "files": files,
        "outputs": outputs,
        "assumed": assumed,
        "seq": {
            "not_written": [n for n in seq_notes if NOT_WRITTEN in n],
            "assumed": [n for n in seq_notes if NOT_WRITTEN not in n],
        },
        "dyr": {"not_written": no_record},
        "contingencies": {
            "written": int(len(cs)) - len(con_skipped),
            "not_written": list(con_skipped),
            "label_map": conmap_name,
        },
        "scripts_not_written": None if plant is not None else _SCRIPTS_NEED_PLANT,
        "plant_controller_not_written": None
        if plant is not None
        else _REPCA1_NEEDS_PLANT,
        "battery_assumed": battery_notes,
        **({"plant_assumed": plant_assumed} if plant_assumed else {}),
        "mon_assumed": MON_ASSUMED,
        "pending_verification": [PIN_NOTE, BUSR_NOTE, PSSPY_NOTE],
    }
    if scripts:
        # a pack declares a contract version only with the scripts that write it (GK A2 T1)
        manifest["contract_version"] = PSSE_CONTRACT_VERSION
    if model_set is not None:
        manifest["model_set"] = model_set_manifest(model_set, variant)
    (out / "README.md").write_text(_readme(manifest), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _readme(m) -> str:
    lines = [f"# {m['case']}: PSS/E v{m['psse_version']} study pack", ""]
    if m["not_for_submission"]:
        mark = m["not_for_submission"]
        lines += [f"**{mark[:1].upper()}{mark[1:]}.**", ""]
    lines += ["## Files", "", *(f"- `{f}`" for f in m["files"]), ""]
    if m["contingencies"]["label_map"]:
        lines += [
            f"The contingencies in the .con are labelled C0001, C0002 and so on. "
            f"`{m['contingencies']['label_map']}` turns each label back into its contingency id.",
            "",
        ]
    if "model_set" in m:
        ms = m["model_set"]
        lines += [
            "## Model set (GC-01)",
            "",
            f"- model_set_id: `{ms['model_set_id']}`",
            f"- data_hash: `{ms['data_hash']}`",
            f"- variant: `{ms['variant']}`",
            f"- model_hash: `{ms['model_hash']}`",
            "",
        ]
    lines += ["## Assumed values", ""]
    lines += [
        f"- {a['unit']}: {', '.join(a['models'])}, every value {a['source']} "
        f"(generic WECC set, `{a['from']}`)"
        for a in m["assumed"]
    ] or ["- none"]
    lines += [f"- .seq: {n.removeprefix('seq: ')}" for n in m["seq"]["assumed"]]
    lines += [f"- {n}" for n in m["battery_assumed"]]
    lines += [f"- {n}" for n in m.get("plant_assumed", [])]
    lines.append(f"- {m['mon_assumed']}")
    lines += ["", "## Not written", ""]
    not_written = [f".seq: {n.removeprefix('seq: ')}" for n in m["seq"]["not_written"]]
    not_written += [
        f".dyr: {d['unit']} (bus {d['bus']}, id '{d['id']}'): {d['reason']}"
        for d in m["dyr"]["not_written"]
    ]
    not_written += [
        f".con: {cid} (compound)" for cid in m["contingencies"]["not_written"]
    ]
    if m["scripts_not_written"]:
        not_written.append(m["scripts_not_written"])
    if m["plant_controller_not_written"]:
        not_written.append(m["plant_controller_not_written"])
    lines += [f"- {n}" for n in not_written] or ["- nothing"]
    lines += [
        "",
        "## Pending verification",
        "",
        *(f"- {n}" for n in m["pending_verification"]),
        "",
        "## Transformer impedance",
        "",
        TRAFO_NOTE,
        "",
        "## Thevenin source voltage factor c",
        "",
        C_NOTE,
        "",
    ]
    return "\n".join(lines)
