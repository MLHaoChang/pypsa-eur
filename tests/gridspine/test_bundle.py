"""Task 12: contingencies.csv, the ledger README, and bundle assembly.

The bundle is the artifact a dynamics engineer opens WITHOUT the study
directory: .raw, .dyr, contingencies.csv, ledger.md, the flow and the hour's
dispatch and loads, plus a manifest. A bundle that needs the study directory
to be intelligible is not a handoff.

The ledger is the product, so it is not optional: a README that omits a
canonical assumption fails to BUILD. The builder checks that the profile
ledger and the slack-exclusion ledger are all present in the input, that every
per-field `assumed` template value is named in the text, and that the two
measurements increment 3 owes (the DC-severity blind spot, task 8; the N-2
prune threshold, task 5) are at least declared — a value of None renders as
"not yet measured", a missing KEY raises. Nothing is invented to fill a gap.

Fixture: one hand-built hour on case39_res. Native gen setpoints, slack at
zero, RES at 0.3x installed (the derating `test_case39_res` uses so the
flow converges), native loads. No UC solve.
"""
import json

import pandas as pd
import pytest

from gridspine.handoff.bundle import (
    BUNDLE_FILES,
    README_SECTIONS,
    REQUIRED_MEASUREMENTS,
    BundleInputs,
    export_bundle,
    write_ledger_readme,
)
from gridspine.handoff.contingencies import write_contingencies
from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import RES_LEDGER, load_case39_res, registry_from_net
from gridspine.ingest.synthetic_profiles import PROFILE_LEDGER
from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import (
    EXT_GRID_EXCLUSION_LEDGER,
    branch_contingencies,
    n2_candidates,
    unit_contingencies,
)
from gridspine.static.loadflow import LFResult, apply_snapshot, run_lf
from gridspine.templates.unit_params import load_unit_params, load_unit_templates

HOUR = 0
RES_CF = 0.3
F_HZ = 60.0

DRIVER_ENTRIES = [
    "case39 exported at f_hz=60.0 (60 Hz system)",
    "loads q_mvar scaled at constant power factor (assumed)",
]


def _hour_tables(net, registry):
    cap = {e["name"]: e["p_mw"] for e in RES_LEDGER}
    rows = []
    for unit_id, rec in registry.iterrows():
        if rec["kind"] == "gen":
            i = net.gen.index[net.gen["name"] == unit_id][0]
            p = float(net.gen.at[i, "p_mw"])
        elif rec["kind"] == "res":
            p = RES_CF * cap[unit_id]
        else:
            p = 0.0
        rows.append({"unit_id": unit_id, "hour": HOUR, "p_mw": p, "q_mvar": 0.0, "status": 1})
    dispatch = pd.DataFrame(rows)
    name_of = net.bus["name"]
    by_bus = net.load.groupby("bus")[["p_mw", "q_mvar"]].sum()
    loads = pd.DataFrame({
        "bus": [name_of.at[b] for b in by_bus.index],
        "hour": HOUR,
        "p_mw": by_bus["p_mw"].values,
        "q_mvar": by_bus["q_mvar"].values,
    })
    return dispatch, loads


def _entries():
    return [*DRIVER_ENTRIES, *PROFILE_LEDGER, *EXT_GRID_EXCLUSION_LEDGER]


def _measurements(**over):
    m = {k: None for k in REQUIRED_MEASUREMENTS}
    m.update(over)
    return m


@pytest.fixture(scope="module")
def snapshot():
    net = load_case39_res()
    registry = registry_from_net(net)
    dispatch, loads = _hour_tables(net, registry)
    apply_snapshot(net, dispatch, loads, hour=HOUR, registry=registry)
    lf = run_lf(net)
    assert lf.converged, "fixture must converge or every downstream check is moot"
    cset = pd.concat(
        [branch_contingencies(net), unit_contingencies(registry)], ignore_index=True
    )
    return dict(net=net, registry=registry, dispatch=dispatch, loads=loads, lf=lf, cset=cset)


def _inputs(snap, **over):
    kw = dict(
        net=snap["net"], hour=HOUR, dispatch=snap["dispatch"], loads=snap["loads"],
        registry=snap["registry"], unit_params=load_unit_params(),
        templates=load_unit_templates(), contingency_set=snap["cset"], lf=snap["lf"],
        ledger_entries=_entries(), measurements=_measurements(), f_hz=F_HZ,
    )
    kw.update(over)
    return BundleInputs(**kw)


@pytest.fixture(scope="module")
def bundle(snapshot, tmp_path_factory):
    out = tmp_path_factory.mktemp("bundle")
    return export_bundle(out, _inputs(snapshot))


# --------------------------------------------------------------------------
# contingencies.csv
# --------------------------------------------------------------------------

def test_contingencies_csv_round_trips_the_set_with_raw_numbers(snapshot, tmp_path):
    net, cset = snapshot["net"], snapshot["cset"]
    nums = write_raw(net, tmp_path / "c.raw", f_hz=F_HZ)
    written = write_contingencies(cset, net, tmp_path / "contingencies.csv")
    back = pd.read_csv(tmp_path / "contingencies.csv", dtype={"ckt": str, "machine_id": str})

    assert len(back) == len(cset)
    assert back["contingency_id"].tolist() == cset["contingency_id"].tolist()
    assert back["element_ids"].map(lambda s: s.split("|")).tolist() == cset["element_ids"].tolist()

    branches = back[back["kind"] == "branch"]
    assert (branches["from_bus_number"] == branches["from_bus"].map(nums)).all()
    assert (branches["to_bus_number"] == branches["to_bus"].map(nums)).all()
    units = back[back["kind"] == "unit"]
    assert (units["bus_number"] == units["bus"].map(nums)).all()
    assert units["machine_id"].notna().all()
    # G_BUS_33 shares its bus with W_BUS_33: the RAW gives them IDs '1' and '2'.
    ids = units.set_index("contingency_id")["machine_id"]
    assert ids["G_BUS_33"] == "1" and ids["W_BUS_33"] == "2"
    pd.testing.assert_frame_equal(written.reset_index(drop=True), back, check_dtype=False)


def test_contingencies_csv_carries_n2_pairs_as_pipe_joined_element_ids(snapshot, tmp_path):
    n1 = branch_contingencies(snapshot["net"])
    n2 = n2_candidates(n1).head(5)
    write_contingencies(n2, snapshot["net"], tmp_path / "n2.csv")
    back = pd.read_csv(tmp_path / "n2.csv")
    assert (back["order"] == 2).all()
    assert back["element_ids"].str.count(r"\|").eq(1).all()


def test_contingencies_csv_validates_its_input(snapshot, tmp_path):
    bad = snapshot["cset"].copy()
    bad.loc[0, "order"] = 3
    with pytest.raises(ContractError, match="order"):
        write_contingencies(bad, snapshot["net"], tmp_path / "x.csv")


# --------------------------------------------------------------------------
# the ledger README
# --------------------------------------------------------------------------

def test_readme_has_every_section(tmp_path):
    text = write_ledger_readme(_entries(), load_unit_templates(), _measurements(), tmp_path / "l.md")
    for section in README_SECTIONS:
        assert f"## {section}" in text, section


def test_readme_names_every_res_site_and_every_per_field_assumed_tag(tmp_path):
    t = load_unit_templates()
    text = write_ledger_readme(_entries(), t, _measurements(), tmp_path / "l.md")
    for site in RES_LEDGER:
        assert site["name"] in text
    assumed = t.params[t.params["source"] == "assumed"]
    assert len(assumed) > 0
    for unit_id, param in zip(assumed["unit_id"], assumed["param"]):
        assert f"{unit_id}.{param}" in text, (unit_id, param)


def test_readme_carries_the_provenance_counts(tmp_path):
    from gridspine.templates.unit_params import provenance_counts

    t = load_unit_templates()
    text = write_ledger_readme(_entries(), t, _measurements(), tmp_path / "l.md")
    counts = provenance_counts(t)
    for source in ("measured", "datasheet", "assumed"):
        assert f"{int(counts[source])} {source}" in text


def test_readme_declares_unmeasured_numbers_instead_of_inventing_them(tmp_path):
    text = write_ledger_readme(_entries(), load_unit_templates(), _measurements(), tmp_path / "l.md")
    for key in REQUIRED_MEASUREMENTS:
        assert key in text
    assert text.count("not yet measured") == len(REQUIRED_MEASUREMENTS)


def test_readme_renders_a_supplied_measurement(tmp_path):
    m = _measurements(n2_prune_threshold="90 % loading; loses no violation on case39 (measured 2026-09-05)")
    text = write_ledger_readme(_entries(), load_unit_templates(), m, tmp_path / "l.md")
    assert "90 % loading" in text
    assert text.count("not yet measured") == len(REQUIRED_MEASUREMENTS) - 1


def test_readme_refuses_a_missing_measurement_key(tmp_path):
    m = _measurements()
    del m["dc_severity_blind_spot"]
    with pytest.raises(ContractError, match="dc_severity_blind_spot"):
        write_ledger_readme(_entries(), load_unit_templates(), m, tmp_path / "l.md")


def test_readme_refuses_an_input_that_dropped_a_profile_ledger_entry(tmp_path):
    """THE rule: delete an assumed entry from the input and the bundle does not build."""
    entries = [e for e in _entries() if not e.startswith("wind_cf")]
    assert len(entries) == len(_entries()) - 1
    with pytest.raises(ContractError, match="omits"):
        write_ledger_readme(entries, load_unit_templates(), _measurements(), tmp_path / "l.md")


def test_readme_refuses_an_input_that_dropped_the_slack_exclusion(tmp_path):
    entries = [e for e in _entries() if e not in EXT_GRID_EXCLUSION_LEDGER]
    with pytest.raises(ContractError, match="omits"):
        write_ledger_readme(entries, load_unit_templates(), _measurements(), tmp_path / "l.md")


def test_readme_lists_the_dyr_omission_of_inverters(tmp_path):
    text = write_ledger_readme(_entries(), load_unit_templates(), _measurements(), tmp_path / "l.md")
    assert "no .dyr record" in text and "W_BUS_33" in text


# --------------------------------------------------------------------------
# the bundle
# --------------------------------------------------------------------------

def test_bundle_contains_every_required_file(bundle):
    assert bundle.is_dir() and bundle.name == f"bundle_h{HOUR}"
    for name in BUNDLE_FILES:
        assert (bundle / name).exists(), name


def test_bundle_manifest_lists_real_files_and_the_hour(bundle):
    m = json.loads((bundle / "manifest.json").read_text())
    assert m["hour"] == HOUR and m["converged"] is True
    assert m["case"] == "case39" and m["f_hz"] == F_HZ
    for name in m["files"]:
        assert (bundle / name).exists(), name
    assert set(BUNDLE_FILES) <= set(m["files"])
    assert m["dyr_units_written"] == 10 and sorted(m["dyr_units_omitted"]) == sorted(
        e["name"] for e in RES_LEDGER)


def test_bundle_raw_and_dyr_agree_on_bus_numbers(bundle):
    raw = (bundle / "case39_h0.raw").read_text()
    dyr = (bundle / "case39_h0.dyr").read_text()
    raw_buses = set()
    lines = raw.splitlines()
    start = next(i for i, ln in enumerate(lines) if "BEGIN GENERATOR DATA" in ln) + 1
    stop = next(i for i, ln in enumerate(lines) if "END OF GENERATOR DATA" in ln)
    for ln in lines[start:stop]:
        raw_buses.add(int(ln.split(",")[0]))
    dyr_buses = {int(ln.split()[0]) for ln in dyr.splitlines() if ln.strip()}
    assert dyr_buses <= raw_buses and len(dyr_buses) == 10


def test_bundle_dispatch_and_loads_are_the_hours_rows_only(bundle, snapshot):
    d = pd.read_csv(bundle / "dispatch_h0.csv")
    l = pd.read_csv(bundle / "loads_h0.csv")
    assert (d["hour"] == HOUR).all() and len(d) == len(snapshot["dispatch"])
    assert (l["hour"] == HOUR).all() and len(l) == len(snapshot["loads"])


def test_bundle_flow_files_match_the_lfresult(bundle, snapshot):
    bus = pd.read_csv(bundle / "lf_bus.csv", index_col="bus")
    flow = pd.read_csv(bundle / "lf_branch_flow.csv", dtype={"ckt": str})
    pd.testing.assert_frame_equal(bus, snapshot["lf"].bus, check_dtype=False)
    assert len(flow) == len(snapshot["lf"].branch_flow) == 46


def test_bundle_refuses_a_net_that_does_not_carry_the_hour(snapshot, tmp_path):
    """The stage-order guard, in the builder this time: a RAW exported off a net
    the snapshot was never applied to is the increment-1 defect in a new file."""
    fresh = load_case39_res()  # native peak loads, native gens, RES at installed
    with pytest.raises(ContractError, match="does not carry hour"):
        export_bundle(tmp_path, _inputs(snapshot, net=fresh))


def test_bundle_refuses_a_net_whose_loads_moved(snapshot, tmp_path):
    """The load half of the same guard. The fresh-net case above fires on the
    derated RES because this fixture's loads ARE the native loads — so on its
    own it would leave the load check untested."""
    import copy

    moved = copy.deepcopy(snapshot["net"])
    moved.load.at[moved.load.index[0], "p_mw"] *= 0.9
    with pytest.raises(ContractError, match="net.load total"):
        export_bundle(tmp_path, _inputs(snapshot, net=moved))


def test_bundle_builds_for_a_non_convergent_flow_and_says_so(snapshot, tmp_path):
    inputs = _inputs(snapshot, lf=LFResult(converged=False))
    out = export_bundle(tmp_path, inputs)
    m = json.loads((out / "manifest.json").read_text())
    assert m["converged"] is False
    for name in BUNDLE_FILES:
        assert (out / name).exists(), name


def test_bundle_propagates_a_ledger_refusal(snapshot, tmp_path):
    entries = [e for e in _entries() if not e.startswith("solar_cf")]
    with pytest.raises(ContractError, match="omits"):
        export_bundle(tmp_path, _inputs(snapshot, ledger_entries=entries))


def test_bundle_writes_optional_screening_and_fault_files_when_given(snapshot, tmp_path):
    from gridspine.schema.contingency import NON_CONVERGED_SEVERITY

    screening = pd.DataFrame({
        "contingency_id": ["BUS_01-BUS_02-1", "G_BUS_30"],
        "hour": [HOUR, HOUR],
        "converged": [True, False],
        "max_branch_loading_pct": [80.0, float("nan")],
        "min_vm_pu": [0.97, float("nan")],
        "max_vm_pu": [1.05, float("nan")],
        "n_violations": [0, 0],
        "severity": [0.0, NON_CONVERGED_SEVERITY],
    })
    faults = pd.DataFrame({"bus": ["BUS_01"], "ikss_ka": [10.0], "sk_mva": [6000.0], "case": ["max"]})
    out = export_bundle(tmp_path, _inputs(snapshot, screening=screening, fault_levels=faults))
    assert (out / "screening.csv").exists() and (out / "fault_levels.csv").exists()
    m = json.loads((out / "manifest.json").read_text())
    assert "screening.csv" in m["files"] and "fault_levels.csv" in m["files"]


def test_bundle_validates_optional_screening(snapshot, tmp_path):
    bad = pd.DataFrame({
        "contingency_id": ["X"], "hour": [HOUR], "converged": ["False"],
        "max_branch_loading_pct": [1.0], "min_vm_pu": [1.0], "max_vm_pu": [1.0],
        "n_violations": [0], "severity": [0.0],
    })
    with pytest.raises(ContractError, match="converged"):
        export_bundle(tmp_path, _inputs(snapshot, screening=bad))


def test_bundle_modules_import_no_engine():
    import gridspine.handoff.bundle as b
    import gridspine.handoff.contingencies as c

    for mod in (b, c):
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in ("import pypsa", "gridspine.producers", "gridspine.drivers"):
            assert banned not in src, (mod.__name__, banned)
