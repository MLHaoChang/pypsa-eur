"""
The campus PowerFactory handoff (DGS + harmonic sources), its closed-loop
read-back, and the field background in the harmonic screen (GC-05).
"""

import json

import pandas as pd
import pytest

from gridspine.drivers.campus_study import (
    HARMONIC_SOURCES_CSV,
    HARMONICS_CSV,
    SAME_LIMIT,
    campus_tables,
    harmonic_units,
    harmonics_campus,
    import_background_campus,
    load_run_campus,
    powerfactory_campus,
)
from gridspine.ingest.background_harmonics import combine
from gridspine.readback.results import DGS_NAMES_CSV, read_back
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_dgs_writer import read_dgs
from tests.gridspine.test_harmonics_campus import _hub, _run


@pytest.fixture
def run(tmp_path):
    run, _ = _run(tmp_path, _hub())
    return run


def _hour(run):
    hourly, _ = campus_tables(run)
    return int(hourly["period"].iloc[0]), int(hourly["hour"].iloc[0])


@pytest.fixture
def case(run):
    (out,) = powerfactory_campus(run, hours=[_hour(run)])
    return out


def test_one_directory_per_hour_with_the_network_and_its_load_flow(run, case):
    period, hour = _hour(run)
    name = load_run_campus(run).name
    assert case.name == f"{name}_p{period}_h{hour}"
    manifest = json.loads((case / "manifest.json").read_text())
    assert all((case / f).is_file() for f in manifest["files"])
    assert (
        manifest["converged"]
        and manifest["hour"] == hour
        and manifest["pending_verification"]
    )
    dgs = read_dgs(case / f"{case.name}.dgs")
    net = load_run_campus(run).net
    assert len(dgs["ElmTerm"]) == len(net.bus) and len(dgs["ElmXnet"]) == 1
    assert dgs["ElmXnet"][0]["snssmin"] == float(net.ext_grid["s_sc_min_mva"].iloc[0])
    with pytest.raises(ContractError, match="no hour 9999"):
        powerfactory_campus(run, hours=[(period, 9999)])


def test_the_process_units_carry_their_spectra_and_bare_converters_are_listed(case):
    hs = pd.read_csv(case / HARMONIC_SOURCES_CSV, keep_default_na=False).set_index(
        "pp_name"
    )
    statuses = hs["status"].to_dict()
    # network elements are the campus unit ids; the project's links map onto them by pypsa_name
    assert (
        statuses["COMPRESSOR"] == "spectrum"
        and hs.at["COMPRESSOR", "spectrum_source"] == "demo default (6-pulse)"
    )
    assert statuses["PUMP"] == "spectrum"
    assert statuses["PV"] == "no spectrum" and statuses["BESS"] == "no spectrum"
    assert "DC_LOAD" not in statuses  # a plain load: nothing to list
    manifest = json.loads((case / "manifest.json").read_text())
    assert manifest["no_spectrum"] == ["BESS", "PV"]
    dgs = read_dgs(case / f"{case.name}.dgs")
    by_id = {r["ID"]: r for r in dgs["TypHmccur"]}
    compressor = next(r for r in dgs["ElmLod"] if r["loc_name"] == "COMPRESSOR")
    assert by_id[compressor["phmc"]]["norder"][:2] == [5, 7]


def _pf_exports(case, tmp_path, vm_shift=0.0, bad_bus=None):
    """A synthetic PowerFactory export of the DGS network: keyed by DGS names."""
    names = pd.read_csv(case / DGS_NAMES_CSV, keep_default_na=False)
    to_dgs = dict(
        zip(
            names.loc[names["pp_table"] == "bus", "pp_name"],
            names.loc[names["pp_table"] == "bus", "dgs_name"],
        )
    )
    bus = pd.read_csv(case / "lf_bus.csv")
    pf = pd.DataFrame(
        {
            "bus_name": bus["bus"].map(to_dgs),
            "vm_pu": bus["vm_pu"] + vm_shift,
            "va_degree": bus["va_degree"],
        }
    )
    if bad_bus:
        pf.loc[0, "bus_name"] = bad_bus
    br = pd.read_csv(case / "lf_branch_flow.csv", dtype={"ckt": str})
    keyed = names[names["pp_table"].isin(["line", "trafo"])]
    by_triple = {
        (r.from_bus, r.to_bus, str(r.ckt)): r.dgs_name for r in keyed.itertuples()
    }
    pfb = pd.DataFrame(
        {
            "branch_name": [
                by_triple[(r.from_bus, r.to_bus, str(r.ckt))] for r in br.itertuples()
            ],
            "p_from_mw": br["p_from_mw"] + 0.05,
            "q_from_mvar": br["q_from_mvar"],
            "loading_percent": br["loading_percent"],
        }
    )
    pf.to_csv(tmp_path / "pf_bus_in.csv", index=False)
    pfb.to_csv(tmp_path / "pf_br_in.csv", index=False)
    return tmp_path / "pf_bus_in.csv", tmp_path / "pf_br_in.csv"


def test_a_powerfactory_export_keyed_by_dgs_names_reads_back_within_tolerance(
    case, tmp_path
):
    bus_csv, br_csv = _pf_exports(case, tmp_path, vm_shift=0.001)
    summary = read_back(case, bus_csv, br_csv)
    assert summary["pass"] and summary["bus"]["n"] == summary["bus"]["n_ok"]
    assert summary["branches"]["n"] >= 1 and summary["branches"]["pass"]
    bus_csv, _ = _pf_exports(case, tmp_path, vm_shift=0.05)
    assert not read_back(case, bus_csv)["pass"]  # 5 % off: the gate catches it


def test_a_name_the_dgs_never_wrote_is_refused(case, tmp_path):
    bus_csv, _ = _pf_exports(case, tmp_path, bad_bus="Terminal(1)")
    with pytest.raises(ContractError, match="not in the DGS export"):
        read_back(case, bus_csv)


BG = "order,u_h_pct_p95\n5,2.0\n7,0.9\n"


def test_the_background_adds_a_with_background_row_by_the_iec_sum(run, tmp_path):
    plain = harmonics_campus(run)
    rec = import_background_campus(
        run, BG, source="PQ survey", measured_on="2026-09-30"
    )
    assert rec["poc"] == plain.iloc[0]["poc"]
    out = harmonics_campus(run)
    poc_rows = out[out["level"] == "poc"]
    assert len(poc_rows) == 2 and len(out) == len(plain) + 1
    plant, bg = poc_rows.iloc[0], poc_rows.iloc[1]
    assert bg["label"].startswith("with background") and not str(
        plant["label"]
    ).startswith("with background")
    expected = combine(plant["u_h_pct"], {5: 2.0, 7: 0.9})
    assert bg["u_h_pct"] == pytest.approx(expected)
    assert bg["u_h_pct"][5] > plant["u_h_pct"][5]
    assert "PQ survey" in bg["note"] and "2026-09-30" in bg["note"]
    assert "no background measured at order(s) 11" in bg["note"]
    # PUMP has no rated MVA: the background row cannot pass either
    assert bg["verdict"] in ("unknown", "fail") and "no input for pump" in bg["note"]
    assert "with background" in (run / HARMONICS_CSV).read_text()


def test_a_bad_background_is_refused_and_stores_nothing(run):
    with pytest.raises(ContractError, match="not a finite value >= 0"):
        import_background_campus(
            run,
            "order,u_h_pct_p95\n5,-1\n",
            source="PQ survey",
            measured_on="2026-09-30",
        )
    assert len(harmonics_campus(run).query("level == 'poc'")) == 1


def test_the_manifest_pins_the_attribute_ledger(case):
    manifest = json.loads((case / "manifest.json").read_text())
    assert manifest["pending_verification"] == [
        "attribute names pending verification on first PowerFactory import"
    ]


def test_each_source_equals_its_units_spectrum_from_the_harmonic_screen(run, case):
    from gridspine.drivers.campus_study import _unchanged_project
    from gridspine.static import harmonics as hm

    units = harmonic_units(_unchanged_project(run), "x").set_index("unit")
    dgs = read_dgs(case / f"{case.name}.dgs")
    by_id = {r["ID"]: r for r in dgs["TypHmccur"]}
    for elm, link in (("COMPRESSOR", "compressor"), ("PUMP", "pump")):
        _, spectrum, _, _ = hm.unit_inputs(units.loc[link].to_dict())
        typ = by_id[next(r for r in dgs["ElmLod"] if r["loc_name"] == elm)["phmc"]]
        assert dict(zip(typ["norder"], typ["ampl"])) == spectrum
        assert typ["alpha"] == [hm.alpha(h) for h in typ["norder"]]


def test_without_a_background_the_screen_is_unchanged(run):
    first = harmonics_campus(run)
    before = (run / HARMONICS_CSV).read_bytes()
    import_background_campus(run, BG, source="PQ survey", measured_on="2026-09-30")
    harmonics_campus(run)
    (run / "background_harmonics.json").unlink()
    again = harmonics_campus(run)
    assert (run / HARMONICS_CSV).read_bytes() == before
    pd.testing.assert_frame_equal(first, again)


def test_the_background_row_states_its_basis_and_follows_the_studys_law(run):
    import_background_campus(run, BG, source="PQ survey", measured_on="2026-09-30")
    out = harmonics_campus(run)
    bg = out[out["level"] == "poc"].iloc[1]
    assert SAME_LIMIT in bg["note"]
    assert SAME_LIMIT == (
        "judged against the same limit as the plant alone (conservative where that limit is an "
        "installation's emission share)"
    )
    arith = harmonics_campus(run, law="arithmetic")
    plant, bga = (
        arith[arith["level"] == "poc"].iloc[0],
        arith[arith["level"] == "poc"].iloc[1],
    )
    assert bga["u_h_pct"][5] == pytest.approx(
        plant["u_h_pct"][5] + 2.0
    )  # alpha 1 at every order


def test_a_background_measured_elsewhere_is_refused(run):
    import json as _json

    import_background_campus(run, BG, source="PQ survey", measured_on="2026-09-30")
    path = run / "background_harmonics.json"
    rec = _json.loads(path.read_text())
    path.write_text(_json.dumps(rec | {"poc": "elsewhere"}))
    with pytest.raises(
        ContractError, match="'elsewhere', which is no PoC of this study"
    ):
        harmonics_campus(run)


def test_an_element_powerfactory_did_not_export_is_listed_never_matched(case, tmp_path):
    bus_csv, br_csv = _pf_exports(case, tmp_path)
    bus = pd.read_csv(bus_csv, dtype=str)
    dropped = bus.loc[0, "bus_name"]
    bus.iloc[1:].to_csv(bus_csv, index=False)
    br = pd.read_csv(br_csv, dtype=str)
    br.iloc[1:].to_csv(br_csv, index=False)
    summary = read_back(case, bus_csv, br_csv)
    names = pd.read_csv(case / DGS_NAMES_CSV, keep_default_na=False)
    assert (
        summary["not_compared"]["bus"]
        == names.loc[
            (names["pp_table"] == "bus") & (names["dgs_name"] == dropped), "pp_name"
        ].tolist()
    )
    assert len(summary["not_compared"]["branches"]) == 1
    assert summary["bus"]["n"] == len(bus) - 1 and summary["bus"]["pass"]
    assert summary["pass"] is False


def test_a_failing_background_stays_fail_with_a_unit_missing(run):
    import_background_campus(
        str(run),
        "order,u_h_pct_p95\n5,50\n",
        source="PQ survey",
        measured_on="2026-09-30",
    )
    bg = harmonics_campus(str(run)).query("level == 'poc'").iloc[1]
    assert (
        bg["verdict"] == "fail"
    )  # a missing unit downgrades only a pass, never a fail
    assert "worst order 5" in bg["note"] and "no input for pump" in bg["note"]


def test_the_manifest_lists_exactly_its_files(case):
    manifest = json.loads((case / "manifest.json").read_text())
    assert manifest["files"] == [
        f"{case.name}.dgs",
        DGS_NAMES_CSV,
        HARMONIC_SOURCES_CSV,
        "lf_bus.csv",
        "lf_branch_flow.csv",
        "manifest.json",
    ]


def test_a_statcom_is_a_converter_to_list(tmp_path):
    def edit(spec):
        spec["campus"].setdefault("compensation", []).append(
            {
                "name": "ST1",
                "bus": spec["campus"]["pcc"]["bus"],
                "kind": "statcom",
                "q_mvar": {"value": 4.0, "source": "assumed"},
            }
        )

    run, _ = _run(tmp_path, _hub(), edit)
    (out,) = powerfactory_campus(str(run), hours=[_hour(run)])
    hs = pd.read_csv(out / HARMONIC_SOURCES_CSV, keep_default_na=False).set_index(
        "pp_name"
    )
    assert hs.at["ST1", "status"] == "no spectrum"


def test_a_branch_name_the_dgs_never_wrote_is_refused(case, tmp_path):
    bus_csv, br_csv = _pf_exports(case, tmp_path)
    br = pd.read_csv(br_csv, dtype=str)
    br.loc[0, "branch_name"] = "Line(9)"
    br.to_csv(br_csv, index=False)
    with pytest.raises(ContractError, match="branch name.*not in the DGS export"):
        read_back(case, bus_csv, br_csv)


@pytest.mark.parametrize("drop", ["bus", "branch"])
def test_either_side_missing_alone_blocks_the_pass(case, tmp_path, drop):
    bus_csv, br_csv = _pf_exports(case, tmp_path)
    path = bus_csv if drop == "bus" else br_csv
    df = pd.read_csv(path, dtype=str)
    df.iloc[1:].to_csv(path, index=False)
    summary = read_back(case, bus_csv, br_csv)
    assert bool(summary["not_compared"]["bus"]) == (drop == "bus")
    assert bool(summary["not_compared"]["branches"]) == (drop == "branch")
    assert summary["pass"] is False


def _small_run(tmp_path, rated):
    """A campus whose plant alone passes the default profile's limits."""
    run, _ = _run(tmp_path, _hub(rated=rated))
    plant = harmonics_campus(run).query("level == 'poc'").iloc[0]
    assert plant["verdict"] in ("pass", "unknown")
    return run, sorted(plant["u_h_pct"])


def _bg(orders, pct=0.01):
    return "order,u_h_pct_p95\n" + "".join(f"{h},{pct}\n" for h in orders)


def _bg_row(run, csv):
    import_background_campus(run, csv, source="PQ survey", measured_on="2026-09-30")
    return harmonics_campus(run).query("level == 'poc'").iloc[1]


def test_within_limits_and_fully_measured_the_background_row_passes(tmp_path):
    run, orders = _small_run(tmp_path, (1.0, 1.0))
    assert _bg_row(run, _bg(orders))["verdict"] == "pass"


def test_within_limits_a_unit_without_input_reads_exactly_unknown(tmp_path):
    run, orders = _small_run(tmp_path, (1.0, None))  # PUMP has no rated MVA
    bg = _bg_row(run, _bg(orders))
    assert bg["verdict"] == "unknown" and "a missing unit blocks a pass" in bg["note"]


def test_within_limits_an_unmeasured_order_reads_exactly_unknown(tmp_path):
    run, orders = _small_run(tmp_path, (1.0, 1.0))
    bg = _bg_row(run, _bg(orders[1:]))  # the survey skipped the lowest plant order
    assert bg["verdict"] == "unknown"
    assert (
        f"no background measured at order(s) {orders[0]}: an unmeasured order blocks a pass"
        in bg["note"]
    )


def test_an_unmeasured_order_never_turns_a_fail_into_unknown(tmp_path):
    run, orders = _small_run(tmp_path, (1.0, 1.0))
    bg = _bg_row(run, _bg(orders[1:], pct=50.0))
    assert bg["verdict"] == "fail"
