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
  for the aggregated plant (GC-01), which carries the PoC branch;
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
from pathlib import Path

import pandas as pd

from gridspine.handoff.contingencies import write_con, write_mon, write_sub
from gridspine.handoff.dyr_writer import (
    PIN_NOTE,
    PocBranch,
    converter_records,
    machine_records,
)
from gridspine.handoff.psspy_scripts import PENDING as PSSPY_NOTE
from gridspine.handoff.psspy_scripts import check_script, write_scripts
from gridspine.handoff.raw_writer import write_raw
from gridspine.handoff.seq_writer import NOT_WRITTEN, write_seq
from gridspine.handoff.test_battery import build_battery
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
)
_SCRIPTS_NEED_PLANT = (
    "psspy scripts not written: the SMIB cases need the aggregated plant's PoC branch and "
    "machine (GC-01)"
)
#: Campus unit kinds behind a converter (a load, a genset and a UPS are not).
CONVERTER_KINDS = ("bess", "pv", "wind", "fuel_cell")
_COLUMNS = ["mbase_mva", *IBR_PARAMS, *REECC1_ONLY, *WECC_GROUP_PARAMS]


@dataclasses.dataclass(frozen=True)
class Plant:
    """
    The aggregated plant (GC-01): its converter's unit id and PoC branch.
    It takes the REPCA1 record, and the psspy scripts drive it.
    """

    unit_id: str
    branch: PocBranch


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
        is_plant = plant is not None and uid == plant.unit_id
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
    if plant is not None and plant.unit_id not in rows:
        raise ContractError(
            f"psse pack: plant unit {plant.unit_id!r} is not a grid-following converter of this campus"
        )
    ibr = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=_COLUMNS)
    poc = {plant.unit_id: plant.branch} if plant is not None else None
    conv_lines, _, repc = converter_records(net, ibr, version=VERSION, poc=poc)
    with open(out / names["dyr"], "w", newline="\n") as f:
        f.write("".join(f"{ln}\n" for ln in [*sync_lines, *conv_lines]))

    cs = _contingency_set(campus) if contingency_set is None else contingency_set
    con_skipped = write_con(cs, net, out / names["con"])
    write_sub(net, out / names["sub"])
    write_mon(out / names["mon"], *MON_BAND_PU)

    files = [*names.values(), "README.md", "manifest.json"]
    scripts, outputs, battery_notes = {}, {}, []
    if plant is not None:
        if cases is None:
            cases, battery_notes = _battery_cases(net)
        _busr, bus1, bus2, ckt = repc[plant.unit_id]
        num, mid, mbase = machines[plant.unit_id]
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
            plant_branch=(bus1, bus2, ckt),
            plant_machine=(num, mid),
            plant_mbase_mva=mbase,
        )
        outputs = {cid: [f"{cid}.out", f"{cid}.csv"] for cid in scripts}
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
        "contingencies": {
            "written": int(len(cs)) - len(con_skipped),
            "not_written": list(con_skipped),
        },
        "scripts_not_written": None if plant is not None else _SCRIPTS_NEED_PLANT,
        "battery_assumed": battery_notes,
        "mon_assumed": MON_ASSUMED,
        "pending_verification": [PIN_NOTE, BUSR_NOTE, PSSPY_NOTE],
    }
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
    lines += [f"- .seq: {n}" for n in m["seq"]["assumed"]]
    lines += [f"- {n}" for n in m["battery_assumed"]]
    lines.append(f"- {m['mon_assumed']}")
    lines += ["", "## Not written", ""]
    not_written = [f".seq: {n}" for n in m["seq"]["not_written"]]
    not_written += [
        f".con: {cid} (compound)" for cid in m["contingencies"]["not_written"]
    ]
    if m["scripts_not_written"]:
        not_written.append(m["scripts_not_written"])
    lines += [f"- {n}" for n in not_written] or ["- nothing"]
    lines += [
        "",
        "## Pending verification",
        "",
        *(f"- {n}" for n in m["pending_verification"]),
        "",
    ]
    return "\n".join(lines)
