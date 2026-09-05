"""The handoff bundle: one directory per selected hour that stands alone.

Contents: the .raw and .dyr for the hour, ``contingencies.csv``, the ledger as
prose (``ledger.md``), the load-flow result, the hour's own dispatch and loads
rows, optional screening and fault-level tables, and a manifest. A bundle
that needs the study directory to be intelligible is not a handoff, and this
is the artifact the GUI will later surface as a download.

THE LEDGER IS THE PRODUCT, so it cannot be optional. ``write_ledger_readme``
refuses to build when the input omits any entry of the canonical ledgers it
knows about (the synthetic-profile ledger, the slack-exclusion ledger), when a
required measurement is not even declared, or when its own rendering fails to
name an ``assumed`` template value. A value of ``None`` for a measurement is
legal and renders as "not yet measured" with the task that owes it — nothing
is invented to fill a gap, and the gap is visible.

``export_bundle`` also carries the stage-order guard forward: the net handed
in must already carry the hour (loads, gen setpoints and RES output all
matching the tables at that hour), or the .raw would be the increment-1
defect in a new file. It checks; it does not apply.

Nothing here imports an engine or ``drivers/``; the driver-level ledger
entries (planning.LEDGER and friends) arrive as input.
"""
import dataclasses
import json
from pathlib import Path

import pandas as pd

from gridspine.handoff.contingencies import write_contingencies
from gridspine.handoff.dyr_writer import _machines, write_dyr
from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import RES_LEDGER
from gridspine.ingest.synthetic_profiles import PROFILE_LEDGER
from gridspine.schema.contingency import (
    validate_contingency_results,
    validate_fault_levels,
)
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.contingency_set import EXT_GRID_EXCLUSION_LEDGER
from gridspine.templates.unit_params import UnitTemplates, provenance_counts

#: Numbers increment 3 owes the ledger. A missing KEY raises; None renders as
#: "not yet measured" with the owner, so the gap is declared, never papered.
REQUIRED_MEASUREMENTS = ("dc_severity_blind_spot", "n2_prune_threshold")
_MEASUREMENT_OWNER = {
    "dc_severity_blind_spot": (
        "the DC-LODF severity that ranks the year against AC severity on a "
        "sample of hours — increment 3 task 8"
    ),
    "n2_prune_threshold": (
        "the DC loading threshold below which N-2 pairs are not AC-verified, "
        "and the full-AC run that showed it loses no violation — increment 3 task 5"
    ),
}

#: Canonical ledgers every bundle must carry in full. The driver's own entries
#: (planning.LEDGER etc.) are the driver's responsibility and arrive as input.
REQUIRED_LEDGERS = (
    ("synthetic-profile", tuple(PROFILE_LEDGER)),
    ("slack-exclusion", tuple(EXT_GRID_EXCLUSION_LEDGER)),
)

README_SECTIONS = (
    "Provenance", "Assumed values", "Renewable sites",
    "Study assumptions", "Measurements", "Omissions",
)

#: Hour-independent required files. The .raw, .dyr, dispatch and loads files
#: are named for the case and hour and are checked alongside these.
BUNDLE_FILES = (
    "contingencies.csv", "ledger.md", "lf_bus.csv", "lf_branch_flow.csv", "manifest.json",
)

_P_TOL_MW = 1e-3


def write_ledger_readme(entries, templates: UnitTemplates, measurements: dict, path) -> str:
    entries = list(entries)
    for label, ledger in REQUIRED_LEDGERS:
        missing = [e for e in ledger if e not in entries]
        if missing:
            raise ContractError(
                f"ledger input omits {len(missing)} {label} ledger entr"
                f"{'y' if len(missing) == 1 else 'ies'}: {missing}"
            )
    missing_keys = [k for k in REQUIRED_MEASUREMENTS if k not in measurements]
    if missing_keys:
        raise ContractError(f"measurements missing required key(s): {missing_keys}")
    unknown = sorted(set(measurements) - set(REQUIRED_MEASUREMENTS))
    if unknown:
        raise ContractError(f"measurements has unknown key(s): {unknown}")

    counts = provenance_counts(templates)
    assumed = templates.params[templates.params["source"] == "assumed"]
    inverters = templates.units.index[templates.units["model"] == "inverter"].tolist()
    models = templates.units["model"].value_counts()

    lines = ["# gridspine handoff — assumptions ledger", ""]
    lines += [
        "Every number in this bundle is tagged `measured`, `datasheet` or "
        "`assumed`. This file is the appendix that lets a reader tell them apart.",
        "",
    ]

    lines += ["## Provenance", ""]
    lines.append(
        "Unit-parameter values by tag: "
        + ", ".join(f"{int(counts[s])} {s}" for s in ("measured", "datasheet", "assumed"))
        + f" over {len(templates.params)} (unit, parameter) pairs."
    )
    lines.append(
        "Units by dynamic model: "
        + ", ".join(f"{int(n)} {m}" for m, n in models.sort_index().items()) + "."
    )
    lines.append("")

    lines += ["## Assumed values", ""]
    lines.append("Template parameters carrying the `assumed` tag, one per line as `unit.parameter = value`:")
    lines.append("")
    for r in assumed.sort_values(["unit_id", "param"]).itertuples(index=False):
        lines.append(f"- {r.unit_id}.{r.param} = {r.value:g}")
    lines.append("")

    lines += ["## Renewable sites", ""]
    lines.append(
        "The IEEE 39-bus case carries no renewables; these sites and capacities "
        "are invented for the study (assumed), not sourced:"
    )
    lines.append("")
    for e in RES_LEDGER:
        lines.append(f"- {e['name']} at {e['bus']}: {e['p_mw']:g} MW {e['tech']}")
    lines.append("")

    lines += ["## Study assumptions", ""]
    for e in entries:
        lines.append(f"- {e}")
    lines.append("")

    lines += ["## Measurements", ""]
    lines.append("Numbers the screening stages must establish on the fixture before they are quoted:")
    lines.append("")
    for key in REQUIRED_MEASUREMENTS:
        value = measurements[key]
        if value is None:
            lines.append(f"- {key}: not yet measured — {_MEASUREMENT_OWNER[key]}")
        else:
            lines.append(f"- {key}: {value}")
    lines.append("")

    lines += ["## Omissions", ""]
    lines.append(
        "Inverter-based units have no .dyr record: no IBR dynamic model (REGC/REEC) "
        "is in scope yet, so they enter the dynamics case as static injections. "
        f"Units affected: {', '.join(inverters) if inverters else 'none'}."
    )
    lines.append("")

    text = "\n".join(lines)

    # Self-check: the rendering must name everything it was given.
    dropped = [e for e in entries if e not in text]
    dropped += [
        f"{r.unit_id}.{r.param}" for r in assumed.itertuples(index=False)
        if f"{r.unit_id}.{r.param}" not in text
    ]
    dropped += [e["name"] for e in RES_LEDGER if e["name"] not in text]
    if dropped:
        raise ContractError(f"README rendering dropped ledger items: {dropped}")

    Path(path).write_text(text, encoding="utf-8")
    return text


@dataclasses.dataclass
class BundleInputs:
    net: object
    hour: int
    dispatch: pd.DataFrame
    loads: pd.DataFrame
    registry: pd.DataFrame
    unit_params: pd.DataFrame
    templates: UnitTemplates
    contingency_set: pd.DataFrame
    lf: object                      # LFResult, converged or not
    ledger_entries: list
    measurements: dict
    f_hz: float = 50.0
    case_name: str = "case39"
    screening: pd.DataFrame = None
    fault_levels: pd.DataFrame = None


def _check_net_carries_hour(inp: BundleInputs, dispatch, loads) -> None:
    net, hour = inp.net, int(inp.hour)
    at_hour = dispatch[dispatch["hour"] == hour].set_index("unit_id")
    load_rows = loads[loads["hour"] == hour]
    if at_hour.empty or load_rows.empty:
        raise ContractError(f"dispatch/loads tables have no rows for hour {hour}")

    net_load = float(net.load["p_mw"].sum())
    table_load = float(load_rows["p_mw"].sum())
    if abs(net_load - table_load) > _P_TOL_MW:
        raise ContractError(
            f"net does not carry hour {hour}: net.load total {net_load:.3f} MW vs "
            f"loads table {table_load:.3f} MW — apply_snapshot first"
        )
    gen_name = {net.gen.at[i, "name"]: i for i in net.gen.index}
    sgen = getattr(net, "sgen", None)
    sgen_name = {sgen.at[i, "name"]: i for i in sgen.index} if sgen is not None else {}
    for unit_id, rec in inp.registry.iterrows():
        if rec["kind"] == "gen":
            table, idx = net.gen, gen_name.get(unit_id)
        elif rec["kind"] == "res":
            table, idx = sgen, sgen_name.get(unit_id)
        else:
            continue
        if idx is None or unit_id not in at_hour.index:
            raise ContractError(f"unit {unit_id} missing from the net or the dispatch at hour {hour}")
        want, have = float(at_hour.at[unit_id, "p_mw"]), float(table.at[idx, "p_mw"])
        if abs(want - have) > _P_TOL_MW:
            raise ContractError(
                f"net does not carry hour {hour}: {unit_id} p_mw {have:.3f} on the net vs "
                f"{want:.3f} in the dispatch — apply_snapshot first"
            )


def export_bundle(outdir, inp: BundleInputs) -> Path:
    dispatch = validate_dispatch(inp.dispatch)
    loads = validate_loads(inp.loads)
    _check_net_carries_hour(inp, dispatch, loads)
    hour = int(inp.hour)

    bundle = Path(outdir) / f"bundle_h{hour}"
    bundle.mkdir(parents=True, exist_ok=True)
    stem = f"{inp.case_name}_h{hour}"
    files = []

    raw = bundle / f"{stem}.raw"
    write_raw(inp.net, raw, title=f"{inp.case_name} UC dispatch hour {hour}", f_hz=inp.f_hz)
    files.append(raw.name)

    dyr = bundle / f"{stem}.dyr"
    written = write_dyr(inp.net, inp.unit_params, dyr)
    omitted = [uid for uid, _n, _i, _mb, sync in _machines(inp.net) if not sync]
    files.append(dyr.name)

    write_contingencies(inp.contingency_set, inp.net, bundle / "contingencies.csv")
    files.append("contingencies.csv")

    write_ledger_readme(inp.ledger_entries, inp.templates, inp.measurements, bundle / "ledger.md")
    files.append("ledger.md")

    inp.lf.bus.to_csv(bundle / "lf_bus.csv", index_label="bus")
    inp.lf.branch_flow.to_csv(bundle / "lf_branch_flow.csv", index=False)
    files += ["lf_bus.csv", "lf_branch_flow.csv"]

    dispatch[dispatch["hour"] == hour].to_csv(bundle / f"dispatch_h{hour}.csv", index=False)
    loads[loads["hour"] == hour].to_csv(bundle / f"loads_h{hour}.csv", index=False)
    files += [f"dispatch_h{hour}.csv", f"loads_h{hour}.csv"]

    if inp.screening is not None:
        validate_contingency_results(inp.screening).to_csv(bundle / "screening.csv", index=False)
        files.append("screening.csv")
    if inp.fault_levels is not None:
        validate_fault_levels(inp.fault_levels).to_csv(bundle / "fault_levels.csv", index=False)
        files.append("fault_levels.csv")

    files.append("manifest.json")
    (bundle / "manifest.json").write_text(json.dumps({
        "case": inp.case_name,
        "hour": hour,
        "f_hz": inp.f_hz,
        "converged": bool(inp.lf.converged),
        "slack_p_mw": None if not inp.lf.converged else float(inp.lf.slack_p_mw),
        "dyr_units_written": len(written),
        "dyr_units_omitted": omitted,
        "files": files,
    }, indent=2))

    required = list(BUNDLE_FILES) + [raw.name, dyr.name, f"dispatch_h{hour}.csv", f"loads_h{hour}.csv"]
    missing = [f for f in required if not (bundle / f).exists()]
    if missing:
        raise ContractError(f"bundle {bundle} is incomplete; missing {missing}")
    return bundle
