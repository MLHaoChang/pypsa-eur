"""
The PSCAD / EMT pack: Thevenin set, case schedule, automation stub, manifest.

EMT studies (TRGC 05, 07, 17, 19–22) and HIL (TRGC 23) are built by hand in
PSCAD or RSCAD from plant data. This module writes the inputs an EMT engineer
loads from the campus model and the tool-neutral test battery
(:mod:`gridspine.handoff.test_battery`). Nothing here runs PSCAD.

**Thevenin set.** The EMT source is always the grid at the PoC as the campus
file gives it (the TSO's figures on the PCC): the paired study cases ``max``
= (``sk_max_mva``, ``rx_max``), ``min`` = (``sk_min_mva``, ``rx_min``) and,
when given, ``normal`` = (``sk_normal_mva``, ``rx_normal``). ``rx_*`` is R/X,
so X/R = 1/rx. The battery's four corners take ``xr_min`` / ``xr_max`` as the
smaller / larger of 1/rx_max and 1/rx_min. Each study case carries its own
zero sequence from ``x0x_<case>`` (X0/X1) and ``r0x0_<case>`` (R0/X0); a case
without it is listed as to be supplied, never borrowed silently.

R and X come from :func:`test_battery.thevenin_impedances_ohm`, |Z| =
Un² / Sk at ``atan(X/R)``, with no IEC 60909 voltage factor c, as the test
battery does. Every row says so (:data:`C_NOTE`) and the manifest lists it as
an assumption to confirm with the TSO.

Per stage (an ``installed`` unit set, e.g. an investment period), the IEC
60909 fault level at the PoC bus with that stage's units energised
(:func:`campus_sc.campus_fault_levels`) is written beside the set as a
reference. It includes the plant's own contribution, so it is not the EMT
source.

**Case schedule.** One row per battery case: its Thevenin source in ohms,
event timings and parameters, the fault impedance every tool applies
(:func:`test_battery.fault_impedance_ohm`), the output name (the case id) and
the contract channel names in contract order.

**Battery from the campus.** :func:`battery_from_campus` builds the default
battery from the PCC corners. The battery holds one zero-sequence pair for
all corners; it is taken from the max case and recorded in the manifest as
``assumed: from max`` (in the manifest and on each battery-corner row). The
manifest's ``z0_differs_from_battery`` maps each study case whose own zero
sequence differs to the battery case ids at that case's Sk.

**Automation stub.** ``run_schedule.py`` is generated text for the mhi.pscad
API: it loops the schedule, sets the source and event parameters and runs
each case. It is never executed here or in CI.

**Parameter workbook.** One CSV per sheet of
:mod:`gridspine.handoff.emt_workbook` and ``RSCAD_NOTES.md``; its missing
fields join ``to_be_supplied.csv``.

**Manifest.** File list with a SHA-256 per file, a ``data_hash`` over them,
the contract version, case ids, channel names, assumptions and the
to-be-supplied list; ``model_set_id`` only when one is passed. Keys are
sorted, so the same inputs give byte-equal files.

Generic engineering only; TRGC numbers are cited, nothing else.
"""

import dataclasses
import hashlib
import json
import math
import shutil
from pathlib import Path

import pandas as pd

from gridspine.handoff.emt_workbook import RSCAD_NOTES, rscad_notes, workbook
from gridspine.handoff.test_battery import (
    CHANNEL_SEP,
    CHANNELS,
    CONTRACT_VERSION,
    PARAM_NAMES,
    Thevenin,
    battery_json,
    build_battery,
    fault_impedance_ohm,
    thevenin_impedances_ohm,
    validate_battery,
)
from gridspine.schema.contracts import ContractError

PACK_VERSION = "1"
#: The mhi.pscad API the stub is written for (PSCAD 5 automation library).
MHI_PSCAD_API = "2"
C_NOTE = "IEC 60909 c not applied (as the test battery); with c_max = 1.1 the max-case |Z| is 10 % higher"
STAGE_NOTE = "reference, not the EMT source: IEC 60909 at the PoC bus with the stage's units energised"
BATTERY_Z0_NOTE = "assumed: from max"
STUDY_CASES = ("max", "min", "normal")
_REQUIRED_PCC = ("sk_max_mva", "sk_min_mva", "rx_max", "rx_min")
_STUDY_FIELDS = {
    "max": ("sk_max_mva", "rx_max", "x0x_max", "r0x0_max"),
    "min": ("sk_min_mva", "rx_min", "x0x_min", "r0x0_min"),
    "normal": ("sk_normal_mva", "rx_normal", None, None),
}

SCHEDULE_JSON = "case_schedule.json"
SCHEDULE_CSV = "case_schedule.csv"
BATTERY_JSON = "battery.json"
THEVENIN_JSON = "thevenin_set.json"
THEVENIN_CSV = "thevenin_set.csv"
STAGES_CSV = "thevenin_stages.csv"
SUPPLY_CSV = "to_be_supplied.csv"
STUB = "run_schedule.py"
MANIFEST = "manifest.json"
RESULTS_DIR = "results"

THEVENIN_COLUMNS = [
    "label",
    "group",
    "sk_mva",
    "x_over_r",
    "vn_kv",
    "r_ohm",
    "x_ohm",
    "r0_ohm",
    "x0_ohm",
    "z0_over_z1",
    "x0_over_r0",
    "source",
    "note",
]
STAGE_COLUMNS = [
    "stage",
    "bus",
    "vn_kv",
    "ikss_max_ka",
    "ikss_min_ka",
    "sk_max_mva",
    "sk_min_mva",
    "note",
]
SCHEDULE_COLUMNS = (
    [
        "case_id",
        "kind",
        "trgc",
        "thevenin",
        "sk_mva",
        "x_over_r",
        "r_ohm",
        "x_ohm",
        "r0_ohm",
        "x0_ohm",
        "t_event_s",
        "t_end_s",
    ]
    + list(PARAM_NAMES)
    + ["fault_r_ohm", "fault_x_ohm", "output_name", "channels", "source"]
)
SUPPLY_COLUMNS = ["sheet", "element", "param", "why"]


class EmtPackError(ContractError):
    """The EMT pack cannot be written from these inputs; the message names why."""


@dataclasses.dataclass(frozen=True)
class EmtPack:
    path: Path
    files: tuple  # file names written, manifest last
    to_be_supplied: pd.DataFrame
    manifest: dict


def pcc_data(campus) -> tuple:
    """``(bus, vn_kv, {param: (value, source)})`` of the PCC from the campus."""
    bus = str(campus.registry.at["GRID", "bus"])
    vn_kv = float(campus.net.bus.loc[campus.net.bus["name"] == bus, "vn_kv"].iloc[0])
    p = campus.params[campus.params["element"] == bus]
    return (
        bus,
        vn_kv,
        {r.param: (float(r.value), str(r.source)) for r in p.itertuples()},
    )


def _x_over_r(case, rx_name, rx) -> float:
    if rx == 0:
        raise EmtPackError(
            f"study case {case}: {rx_name}=0 means X/R not given / infinite: an EMT source needs a finite X/R"
        )
    return 1.0 / rx


def study_sources(pcc: dict) -> tuple:
    """
    ``(sources, gaps)`` for the paired study cases.

    ``pcc`` maps a PCC parameter to ``(value, source)``. ``sources`` is a
    tuple of ``(Thevenin, source_tag)``; ``gaps`` lists ``(case, param, why)``
    for a study case whose zero sequence is not held.
    """
    missing = [f for f in _REQUIRED_PCC if f not in pcc]
    if missing:
        raise EmtPackError(
            f"PCC data lacks {missing}: the Thevenin set needs Sk and R/X of the max and min cases"
        )
    sk_min, sk_max = pcc["sk_min_mva"][0], pcc["sk_max_mva"][0]
    if sk_min > sk_max:
        raise EmtPackError(
            f"sk_min_mva={sk_min} > sk_max_mva={sk_max}: the minimum Sk must not exceed the maximum"
        )
    out, gaps = [], []
    for case in STUDY_CASES:
        sk_name, rx_name, x0x_name, r0x0_name = _STUDY_FIELDS[case]
        if sk_name not in pcc:
            continue
        xr = _x_over_r(case, rx_name, pcc[rx_name][0])
        z0, used = {}, [sk_name, rx_name]
        if x0x_name is None:
            pass  # the campus schema holds no zero sequence for this case and no battery case uses it
        elif x0x_name in pcc and r0x0_name in pcc:
            x0x, r0x0 = pcc[x0x_name][0], pcc[r0x0_name][0]
            if r0x0 == 0:
                gaps.append(
                    (
                        case,
                        r0x0_name,
                        f"{r0x0_name}=0: a zero-sequence source needs a finite X0/R0",
                    )
                )
            else:
                z0 = {
                    "z0_over_z1": x0x
                    * math.sqrt(1.0 + r0x0**2)
                    / math.sqrt(1.0 + 1.0 / xr**2),
                    "x0_over_r0": 1.0 / r0x0,
                }
                used += [x0x_name, r0x0_name]
        else:
            for n in (x0x_name, r0x0_name):
                if n not in pcc:
                    gaps.append(
                        (
                            case,
                            n,
                            f"zero sequence of the {case} case not held: the single-phase faults "
                            f"need it",
                        )
                    )
        tags = "/".join(sorted({pcc[n][1] for n in used}))
        out.append((Thevenin(case, pcc[sk_name][0], xr, **z0), tags))
    return tuple(out), gaps


def battery_from_campus(campus, overrides=None) -> tuple:
    """
    The default test battery for the campus PCC (``build_battery``).

    The battery's single zero-sequence pair comes from the max case
    (``assumed: from max``); without it the battery is refused.
    """
    _, _, pcc = pcc_data(campus)
    sources, _ = study_sources(pcc)
    by = {g.label: g for g, _ in sources}
    xrs = (by["max"].x_over_r, by["min"].x_over_r)
    if not by["max"].has_zero_sequence:
        raise EmtPackError(
            "battery: the max case has no zero sequence (x0x_max, r0x0_max); the single-phase faults need it"
        )
    return build_battery(
        by["min"].sk_mva,
        by["max"].sk_mva,
        min(xrs),
        max(xrs),
        z0_over_z1=by["max"].z0_over_z1,
        x0_over_r0=by["max"].x0_over_r0,
        overrides=overrides,
    )


def _impedance_row(g: Thevenin, vn_kv) -> dict:
    z1, z0 = thevenin_impedances_ohm(g, vn_kv)
    return {
        "sk_mva": g.sk_mva,
        "x_over_r": g.x_over_r,
        "vn_kv": float(vn_kv),
        "r_ohm": z1.real,
        "x_ohm": z1.imag,
        "r0_ohm": None if z0 is None else z0.real,
        "x0_ohm": None if z0 is None else z0.imag,
        "z0_over_z1": g.z0_over_z1,
        "x0_over_r0": g.x0_over_r0,
    }


def thevenin_set(sources, battery, vn_kv, battery_source="caller's battery") -> list:
    """
    Rows of the Thevenin set: the study cases (group ``study``, tagged
    with their PCC data source) then each distinct battery corner (group
    ``battery``).
    """
    rows = [
        {
            "label": g.label,
            "group": "study",
            **_impedance_row(g, vn_kv),
            "source": tag,
            "note": C_NOTE,
        }
        for g, tag in sources
    ]
    seen = set()
    for c in battery or ():
        if c.grid.label in seen:
            continue
        seen.add(c.grid.label)
        rows.append(
            {
                "label": c.grid.label,
                "group": "battery",
                **_impedance_row(c.grid, vn_kv),
                "source": battery_source,
                "note": C_NOTE,
            }
        )
    return rows


def stage_reference(campus, stages) -> list:
    """
    Per stage, IEC 60909 at the PoC bus with ``installed`` energised
    (reference, not the EMT source).
    """
    from gridspine.static.campus_sc import campus_fault_levels

    bus, vn_kv, _ = pcc_data(campus)
    rows = []
    for stage, installed in (stages or {}).items():
        fl = campus_fault_levels(campus, set(installed)).set_index("bus")
        ik_max, ik_min = (
            float(fl.at[bus, "ikss_max_ka"]),
            float(fl.at[bus, "ikss_min_ka"]),
        )
        rows.append(
            {
                "stage": str(stage),
                "bus": bus,
                "vn_kv": vn_kv,
                "ikss_max_ka": ik_max,
                "ikss_min_ka": ik_min,
                "sk_max_mva": math.sqrt(3) * vn_kv * ik_max,
                "sk_min_mva": math.sqrt(3) * vn_kv * ik_min,
                "note": STAGE_NOTE,
            }
        )
    return rows


def case_schedule(battery, vn_kv) -> list:
    """One schedule row per battery case, in battery order."""
    rows = []
    for c in validate_battery(battery):
        z1, z0 = thevenin_impedances_ohm(c.grid, vn_kv)
        zf = (
            fault_impedance_ohm(c, vn_kv)
            if c.kind in ("fault_3ph", "fault_1ph")
            else None
        )
        row = {
            "case_id": c.case_id,
            "kind": c.kind,
            "trgc": c.trgc,
            "thevenin": c.grid.label,
            "sk_mva": c.grid.sk_mva,
            "x_over_r": c.grid.x_over_r,
            "r_ohm": z1.real,
            "x_ohm": z1.imag,
            "r0_ohm": None if z0 is None else z0.real,
            "x0_ohm": None if z0 is None else z0.imag,
            "t_event_s": c.t_event_s,
            "t_end_s": c.t_end_s,
            **{p: c.params.get(p) for p in PARAM_NAMES},
            "fault_r_ohm": None if zf is None else zf.real,
            "fault_x_ohm": None if zf is None else zf.imag,
            "output_name": c.case_id,
            "channels": CHANNEL_SEP.join(c.channels),
            "source": c.source,
        }
        rows.append(row)
    return rows


def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, allow_nan=False) + "\n"


def _csv(rows, columns) -> str:
    return pd.DataFrame(rows, columns=columns).to_csv(index=False, lineterminator="\n")


def stub_text(case_count: int) -> str:
    """The mhi.pscad automation stub. Generated text, never executed here."""
    return f'''"""
PSCAD case-schedule runner, generated by gridspine (EMT pack v{PACK_VERSION}).

Generated, never executed in CI; written for the mhi.pscad API version {MHI_PSCAD_API},
untested against a PSCAD install.

Loops the {case_count} cases of {SCHEDULE_JSON} (next to this file). For each case it sets the
Thevenin source and the event block of the plant model, sets the run length and the output
file name (the case id), and runs the project. Name the model's output channels with the
contract names in {SCHEDULE_JSON}'s "channels" column (v_poc_pu, p_poc_mw, ...) and point the
project's output folder at {RESULTS_DIR}/, so the read-back maps each file to its case.

Usage: python {STUB} <workspace.pswx> <project> [--source NAME] [--event NAME]
Map the schedule columns to your components' parameter names in SOURCE_KEYS / EVENT_KEYS.
"""
import argparse
import json
from pathlib import Path

import mhi.pscad

HERE = Path(__file__).resolve().parent
SCHEDULE = HERE / "{SCHEDULE_JSON}"
RESULTS = HERE / "{RESULTS_DIR}"

#: schedule column -> parameter of the Thevenin source component (edit to your model)
SOURCE_KEYS = {{"r_ohm": "R", "x_ohm": "X", "r0_ohm": "R0", "x0_ohm": "X0"}}
#: schedule column -> parameter of the event block (edit to your model)
EVENT_KEYS = {{
    "kind": "KIND", "t_event_s": "T_EVENT", "duration_s": "T_DUR", "fault_r_ohm": "RF", "fault_x_ohm": "XF", "v_retained_pu": "VRET",
    "step_pu": "STEP_PU", "step_hz": "STEP_HZ", "ramp_hz_per_s": "RAMP_HZ_S", "angle_deg": "ANGLE_DEG",
}}


def _set(component, keys, case):
    values = {{name: case[col] for col, name in keys.items() if case.get(col) is not None}}
    if values:
        component.parameters(**values)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the EMT pack's case schedule in PSCAD.")
    ap.add_argument("workspace", help="the PSCAD workspace (.pswx) holding the plant model")
    ap.add_argument("project", help="the project (case) in that workspace")
    ap.add_argument("--source", default="GRID_SOURCE", help="name of the Thevenin source component")
    ap.add_argument("--event", default="EVENT", help="name of the event block")
    args = ap.parse_args(argv)
    cases = json.loads(SCHEDULE.read_text())
    RESULTS.mkdir(exist_ok=True)
    pscad = mhi.pscad.launch()
    pscad.load(args.workspace)
    project = pscad.project(args.project)
    source = project.find(args.source)
    event = project.find(args.event)
    for case in cases:
        _set(source, SOURCE_KEYS, case)
        _set(event, EVENT_KEYS, case)
        project.parameters(time_duration=case["t_end_s"], output_filename=case["output_name"] + ".out")
        project.run()
    pscad.quit()


if __name__ == "__main__":
    main()
'''


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_emt_pack(
    outdir, campus, *, battery=None, stages=None, model_set_id=None
) -> EmtPack:
    """
    Write the EMT pack into ``outdir``.

    ``battery`` defaults to :func:`battery_from_campus`; when the campus
    holds no max-case zero sequence the battery, schedule and stub are not
    written and the gap is listed in ``to_be_supplied.csv``. ``stages`` maps a
    stage label to its installed unit ids (reference fault levels).
    ``model_set_id`` is cited in the manifest only when given.
    """
    out = Path(outdir)
    bus, vn_kv, pcc = pcc_data(campus)
    sources, gaps = study_sources(pcc)
    supply = [
        {"sheet": "thevenin", "element": bus, "param": p, "why": why}
        for _, p, why in gaps
    ]
    assumptions = [
        f"thevenin: {C_NOTE}; confirm the source impedance convention with the TSO"
    ]
    z0_differs, battery_source = {}, "caller's battery"
    if battery is None:
        if {g.label: g for g, _ in sources}["max"].has_zero_sequence:
            battery = battery_from_campus(campus)
            battery_source = f"campus PCC corners; Z0 {BATTERY_Z0_NOTE}"
            assumptions.append(
                f"battery zero sequence: {BATTERY_Z0_NOTE} (x0x_max, r0x0_max)"
            )
        else:
            supply.append(
                {
                    "sheet": "case_schedule",
                    "element": bus,
                    "param": "x0x_max, r0x0_max",
                    "why": "the test battery needs the max case's zero sequence; no schedule is written",
                }
            )
    battery = validate_battery(battery) if battery is not None else None
    if battery is not None:
        pair = {(c.grid.z0_over_z1, c.grid.x0_over_r0) for c in battery}
        z0_differs = {
            g.label: [c.case_id for c in battery if c.grid.sk_mva == g.sk_mva]
            for g, _ in sources
            if g.has_zero_sequence and (g.z0_over_z1, g.x0_over_r0) not in pair
        }

    set_rows = thevenin_set(sources, battery, vn_kv, battery_source)
    stage_rows = stage_reference(campus, stages)
    texts = {
        THEVENIN_CSV: _csv(set_rows, THEVENIN_COLUMNS),
        STAGES_CSV: _csv(stage_rows, STAGE_COLUMNS),
    }
    texts[THEVENIN_JSON] = _dumps(
        {
            "note": C_NOTE,
            "stage_note": STAGE_NOTE,
            "pcc_bus": bus,
            "vn_kv": vn_kv,
            "sources": set_rows,
            "stages": stage_rows,
        }
    )
    if battery is not None:
        schedule = case_schedule(battery, vn_kv)
        texts[BATTERY_JSON] = battery_json(battery)
        texts[SCHEDULE_JSON] = _dumps(schedule)
        texts[SCHEDULE_CSV] = _csv(schedule, SCHEDULE_COLUMNS)
        texts[STUB] = stub_text(len(schedule))
    sheets, sheet_gaps = workbook(campus)
    for name, frame in sheets.items():
        texts[f"{name}.csv"] = frame.to_csv(index=False, lineterminator="\n")
    texts[RSCAD_NOTES] = rscad_notes()
    supply += sheet_gaps.to_dict("records")
    texts[SUPPLY_CSV] = _csv(supply, SUPPLY_COLUMNS)

    out.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (out / name).write_text(text, newline="\n")
    files = {name: _sha(text) for name, text in sorted(texts.items())}
    manifest = {
        "pack": "pscad_emt",
        "pack_version": PACK_VERSION,
        "contract_version": CONTRACT_VERSION,
        "f_hz": float(campus.net.f_hz),
        "pcc_bus": bus,
        "vn_kv": vn_kv,
        "files": files,
        "data_hash": _sha("".join(f"{n}:{h}\n" for n, h in files.items())),
        "case_ids": [c.case_id for c in battery] if battery is not None else [],
        "channels": list(CHANNELS),
        "outputs": {f"{RESULTS_DIR}/": "PSCAD .inf/.out per case, named by case id"},
        "assumptions": assumptions,
        "z0_differs_from_battery": z0_differs,
        "to_be_supplied": SUPPLY_CSV,
        "trgc": ["02", "03", "05", "07", "17", "19", "20", "21", "22", "23"],
    }
    if model_set_id is not None:
        manifest["model_set_id"] = str(model_set_id)
    (out / MANIFEST).write_text(_dumps(manifest), newline="\n")
    return EmtPack(
        out, (*files, MANIFEST), pd.DataFrame(supply, columns=SUPPLY_COLUMNS), manifest
    )


BUNDLE_DIR = "emt"


def bundle_emt_pack(bundle_dir, campus, rows) -> dict:
    """
    The EMT pack inside a campus bundle (``<bundle>/emt/``), for its manifest.

    The stage is the bundle's hour: the units with ``status == 1`` in
    ``rows``. Returns ``{"manifest": "emt/manifest.json", "data_hash": ...}``,
    or ``{"not_written": <reason>}`` when the campus data refuses the pack
    (any :class:`ContractError`, e.g. R/X = 0 at the PCC or a workbook field
    with no unit), so the PSS/E bundle is still written and the refusal is
    recorded, not dropped. Any other exception propagates.
    """
    installed = set(rows.loc[rows["status"] == 1, "unit_id"])
    target = Path(bundle_dir) / BUNDLE_DIR
    shutil.rmtree(target, ignore_errors=True)  # no stale pack from an earlier export
    try:
        p = write_emt_pack(target, campus, stages={"hour": installed})
    except ContractError as e:  # EmtPackError and the workbook's refusals
        return {"not_written": str(e)}
    return {
        "manifest": f"{BUNDLE_DIR}/{MANIFEST}",
        "data_hash": p.manifest["data_hash"],
    }
