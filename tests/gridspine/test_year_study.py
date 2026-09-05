"""Driver v2: the year study, end to end.

ONE module-scoped solve. `run_year_study` at hours=336 / window=168 /
overlap=24 is a two-window rolling MILP followed by a load flow per selected
hour; every assertion that can read artifacts reads the ones that single run
produced, because a per-test study would put this file far over its runtime
budget. Only the two tests that cannot use it run their own studies, and both
are deliberately small: the stage-failure test never reaches the solver, and
the non-convergence test runs a 24 h single-window study.

THE RAW CHECKS ARE THE ONES THAT SEE THE STAGE. A year study that never
called `apply_snapshot` would still write every artifact, still converge, and
still name every bus — the exports would simply carry case39's native peak
instead of the hour they claim. `test_raw_carries_each_selected_hours_snapshot`
is the assertion that catches that, and it has to compare the GENERATOR
records and not only the LOAD total: `max_load_mw` puts the annual peak in
every selection, and at the peak hour the scaled demand IS the native demand,
so a load-only check stays green under that mutation for exactly the hour a
reader would look at first.
"""
import json

import pandas as pd
import pytest

from gridspine.drivers import year_study
from gridspine.drivers.year_study import run_year_study
from gridspine.ingest.pandapower_source import RES_LEDGER, load_case39_res
from gridspine.ranking.select import CRITERIA
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.loadflow import LFResult
from gridspine.templates.unit_params import load_unit_params

HOURS = 336
K = 2
WINDOW = 168
OVERLAP = 24

# case39_res: 9 committable gens + 1 ext_grid + 5 sgen. The RAW writer emits
# generator records in exactly that order, which is what lets the per-hour
# check below slice thermal from RES without a name lookup.
N_GEN = 9
N_SLACK = 1
N_RES = 5
UNITS = N_GEN + N_SLACK + N_RES

METRIC_COLUMNS = (
    "load_mw", "import_mw", "inertia_mws", "inertia_excl_equiv_mws", "ibr_share",
    "n1_severity_dc",
)


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    out = tmp_path_factory.mktemp("year_study")
    return run_year_study(out, hours=HOURS, k=K, window=WINDOW, overlap=OVERLAP)


# --------------------------------------------------------------------------
# RAW section readers — the handoff artifact parsed as PSS/E v33 sees it
# --------------------------------------------------------------------------

def _section(raw_text, begin, end):
    lines = raw_text.splitlines()
    start = next(i for i, ln in enumerate(lines) if begin in ln) + 1
    stop = next(i for i, ln in enumerate(lines) if end in ln)
    return lines[start:stop]


def _raw_load_p(raw_text):
    """Field 5 of a v33 LOAD record is PL [MW]."""
    return [
        float(ln.split(",")[5])
        for ln in _section(raw_text, "BEGIN LOAD DATA", "END OF LOAD DATA")
    ]


def _raw_gen_p(raw_text):
    """Field 2 of a v33 GENERATOR record is PG [MW]."""
    return [
        float(ln.split(",")[2])
        for ln in _section(raw_text, "BEGIN GENERATOR DATA", "END OF GENERATOR DATA")
    ]


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def test_study_writes_every_artifact(study):
    for key in ("loads", "dispatch", "metrics", "selected", "manifest", "dc_sensitivities"):
        assert study.artifacts[key].exists(), key
    for hour in study.selected["hour"]:
        hour = int(hour)
        bus = study.artifacts[f"lf_{hour}_bus"]
        raw = study.artifacts[f"raw_{hour}"]
        assert bus.exists() and bus.name == f"lf_{hour}_bus.csv"
        assert raw.exists() and raw.name == f"case39_h{hour}.raw"


def test_dispatch_and_loads_artifacts_validate_and_cover_the_horizon(study):
    dispatch = validate_dispatch(pd.read_csv(study.artifacts["dispatch"]))
    loads = validate_loads(pd.read_csv(study.artifacts["loads"]))
    assert len(dispatch) == UNITS * HOURS
    assert sorted(dispatch["hour"].unique()) == list(range(HOURS))
    assert sorted(loads["hour"].unique()) == list(range(HOURS))


def test_metrics_artifact_has_one_row_per_hour(study):
    metrics = pd.read_csv(study.artifacts["metrics"])
    assert metrics["hour"].tolist() == list(range(HOURS))
    for col in METRIC_COLUMNS:
        assert col in metrics.columns, col


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def test_selection_is_a_union_of_criteria_and_not_exactly_k(study):
    sel = study.selected
    assert K <= len(sel) <= 5 * K  # five criteria since increment 3
    assert sel["hour"].is_unique
    fired = set()
    for reasons in sel["reasons"]:
        assert reasons, "a selected hour with no reason is unreportable"
        assert set(reasons) <= set(CRITERIA)
        fired |= set(reasons)
    assert fired == set(CRITERIA)


def test_selected_csv_carries_the_reasons_and_the_convergence_flag(study):
    csv = pd.read_csv(study.artifacts["selected"])
    assert list(csv.columns[:3]) == ["hour", "reasons", "converged"]
    assert csv["hour"].tolist() == [int(h) for h in study.selected["hour"]]
    for text, reasons in zip(csv["reasons"], study.selected["reasons"]):
        assert text.split("|") == list(reasons)
    assert csv["converged"].dtype == bool


def test_lf_results_are_keyed_by_selected_hour_and_agree_with_the_flag(study):
    assert sorted(study.lf_results) == [int(h) for h in study.selected["hour"]]
    flag = dict(zip(study.selected["hour"], study.selected["converged"]))
    for hour, lf in study.lf_results.items():
        assert isinstance(lf, LFResult)
        assert bool(lf.converged) == bool(flag[hour])


# --------------------------------------------------------------------------
# the stage-order guard
# --------------------------------------------------------------------------

def test_raw_carries_each_selected_hours_snapshot(study):
    """Every export must be the hour it is named for — see the module docstring.

    Both halves matter. The LOAD total catches an export taken at the wrong
    demand level; the GENERATOR records catch one taken at the wrong
    commitment, which is the half that still bites at the annual peak.
    """
    dispatch = pd.read_csv(study.artifacts["dispatch"])
    loads = pd.read_csv(study.artifacts["loads"])

    for hour in study.selected["hour"]:
        hour = int(hour)
        raw = study.artifacts[f"raw_{hour}"].read_text()
        at_hour = dispatch[dispatch["hour"] == hour]

        want_load = float(loads.loc[loads["hour"] == hour, "p_mw"].sum())
        assert sum(_raw_load_p(raw)) == pytest.approx(want_load, abs=0.05), hour

        pg = _raw_gen_p(raw)
        assert len(pg) == UNITS, hour

        thermal = at_hour.loc[at_hour["unit_id"].str.startswith("G_"), "p_mw"]
        assert len(thermal) == N_GEN
        assert sorted(pg[:N_GEN]) == pytest.approx(sorted(thermal), abs=1e-3), hour

        res = at_hour.loc[at_hour["unit_id"].str.match(r"^[WS]_"), "p_mw"]
        assert len(res) == N_RES
        assert sorted(pg[N_GEN + N_SLACK:]) == pytest.approx(sorted(res), abs=1e-3), hour


def test_the_load_total_alone_would_be_vacuous_at_the_selected_peak(study):
    """Why the check above reads GENERATOR records and not only the LOAD total.

    `max_load_mw` always pulls the horizon's peak hour into the selection, and
    `year_load_shape` is normalised so its maximum is 1.0 — so at that hour the
    scaled demand IS case39's native demand. A RAW export taken with
    `apply_snapshot` skipped would carry exactly the same load total there, and
    a load-only assertion could not tell the two apart. This test asserts that
    blind spot exists rather than leaving it as a claim in a docstring; if a
    future profile change removes it, this goes red and the reasoning above can
    be revisited instead of being quietly wrong.
    """
    loads = pd.read_csv(study.artifacts["loads"])
    by_hour = loads.groupby("hour")["p_mw"].sum()
    peak = int(by_hour.idxmax())

    assert peak in [int(h) for h in study.selected["hour"]]
    native = float(load_case39_res().load["p_mw"].sum())
    assert float(by_hour.loc[peak]) == pytest.approx(native, abs=0.05)


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def test_manifest_records_the_run_parameters_and_the_selected_hours(study):
    m = json.loads(study.artifacts["manifest"].read_text())
    assert (m["hours"], m["k"], m["window"], m["overlap"]) == (HOURS, K, WINDOW, OVERLAP)
    assert m["selected_hours"] == [int(h) for h in study.selected["hour"]]
    assert m["stages"] == ["ingest", "dispatch", "ranking", "loadflow", "screening", "handoff"]
    assert m["load_consistency"] == "per-snapshot loads artifact (increment 2)"


def test_manifest_ledger_carries_every_provenance_source(study):
    """The ledger IS the report appendix: profiles, RES siting, unit params."""
    text = " ".join(json.loads(study.artifacts["manifest"].read_text())["ledger"])

    for profile in ("year_load_shape", "wind_cf", "solar_cf"):
        assert profile in text, profile
    for entry in RES_LEDGER:
        assert entry["name"] in text, entry["name"]
    assert "f_hz=60.0" in text  # the increment-1 driver ledger, carried forward

    counts = load_unit_params()["source"].value_counts()
    for source in ("measured", "datasheet", "assumed"):
        assert f"{int(counts.get(source, 0))} {source}" in text, source


# --------------------------------------------------------------------------
# res_cf is built by prefix, and covers every sgen
# --------------------------------------------------------------------------

def test_res_cf_names_every_sgen():
    net = load_case39_res()
    cf = year_study.res_cf_for(net, 48)
    assert set(cf) == set(net.sgen["name"])
    assert all(len(series) == 48 for series in cf.values())


def test_an_sgen_with_an_unknown_prefix_is_a_contract_error_not_a_zero():
    """A silent default would model a new technology as permanently curtailed."""
    import pandapower as pp

    net = load_case39_res()
    pp.create_sgen(net, bus=0, p_mw=1.0, q_mvar=0.0, name="X_BUS_01")
    with pytest.raises(ContractError, match="X_BUS_01"):
        year_study.res_cf_for(net, 48)


# --------------------------------------------------------------------------
# failure paths — both run their own small studies
# --------------------------------------------------------------------------

def test_stage_failure_writes_the_error_artifact_and_reraises(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(year_study, "load_case39_res", boom)
    with pytest.raises(RuntimeError, match="ingest exploded"):
        run_year_study(tmp_path, hours=24, k=1, window=24, overlap=0)

    err = json.loads((tmp_path / "error_ingest.json").read_text())
    assert err["stage"] == "ingest"
    assert err["element_ids"] == []
    assert "ingest exploded" in err["cause"]


def test_a_non_convergent_selected_hour_is_recorded_and_the_run_continues(
    tmp_path, monkeypatch
):
    """Non-convergence is a RESULT. A 24 h single-window study keeps it cheap."""
    monkeypatch.setattr(year_study, "run_lf", lambda net: LFResult(converged=False))

    res = run_year_study(tmp_path, hours=24, k=1, window=24, overlap=0)

    assert len(res.selected) >= 1
    assert not res.selected["converged"].any()
    for hour in res.selected["hour"]:
        hour = int(hour)
        assert res.artifacts[f"raw_{hour}"].exists()
        assert res.artifacts[f"lf_{hour}_bus"].exists()

    m = json.loads(res.artifacts["manifest"].read_text())
    assert m["non_converged_hours"] == [int(h) for h in res.selected["hour"]]


# ===========================================================================
# Increment 3, task 13: screening and bundles in the year study
# ===========================================================================
#
# Per selected hour, after the load flow: N-1 (lightsim2grid + pandapower for
# units), N-2 (DC-LODF estimate, AC verify; threshold 0 by default because the
# measured lossless threshold on case39 prunes nothing), IEC 60909 fault levels
# for both cases, the SCR pre-check, and a stand-alone handoff bundle. The two
# numbers the ledger README declared as "not yet measured" are measured HERE,
# on the UC-dispatched hours the study actually selected, and written into the
# bundle ledger — so the placeholder count in every bundle must be zero.

from gridspine.drivers.year_study import STAGES
from gridspine.handoff.bundle import BUNDLE_FILES
from gridspine.schema.contingency import validate_contingency_results, validate_fault_levels

N1_ROWS = 46 + 14           # branch + unit contingencies on case39_res
N1_ISLANDING = 11
N2_ROWS = 1035
N2_ISLANDED = 473           # topology, so identical in every hour


def test_screening_is_a_stage():
    assert STAGES == ["ingest", "dispatch", "ranking", "loadflow", "screening", "handoff"]


def test_every_selected_hour_is_screened_and_bundled(study):
    hours = [int(h) for h in study.selected["hour"]]
    assert sorted(study.screening) == sorted(hours)
    assert sorted(study.bundles) == sorted(hours)
    assert sorted(study.fault_levels) == sorted(hours)
    assert sorted(study.scr) == sorted(hours)
    for hour in hours:
        validate_contingency_results(study.screening[hour])
        validate_fault_levels(study.fault_levels[hour])
        bundle = study.bundles[hour]
        assert bundle.is_dir() and bundle.name == f"bundle_h{hour}"
        for name in BUNDLE_FILES + ("screening.csv", "fault_levels.csv"):
            assert (bundle / name).exists(), (hour, name)
        assert study.artifacts[f"bundle_{hour}"] == bundle


def test_screening_rows_cover_n1_and_n2_with_the_known_topology_facts(study):
    for hour, rows in study.screening.items():
        n1 = rows[~rows["contingency_id"].str.contains("--")]
        n2 = rows[rows["contingency_id"].str.contains("--")]
        assert len(n1) == N1_ROWS and len(n2) == N2_ROWS, hour
        assert int(n1["islanded"].sum()) == N1_ISLANDING, hour
        assert int(n2["islanded"].sum()) == N2_ISLANDED, hour
        assert (rows["hour"] == hour).all()


def test_fault_levels_carry_both_cases_and_scr_covers_the_res_buses(study):
    for hour in study.fault_levels:
        fl = study.fault_levels[hour]
        assert set(fl["case"]) == {"max", "min"} and len(fl) == 2 * 39
        s = study.scr[hour]
        assert len(s) == 5 and set(s["band"]) <= {"very_weak", "weak", "moderate", "strong"}
        assert (s["case"] == "min").all(), "SCR is taken at the minimum fault level"


def test_manifest_records_per_hour_violations_and_the_two_measurements(study):
    m = json.loads(study.artifacts["manifest"].read_text())
    assert m["screen"] is True
    for hour in study.selected["hour"]:
        row = m["screening"][str(int(hour))]
        for key in ("n1_rows", "n1_islanded", "n1_diverged", "n1_worst_severity",
                    "n2_rows", "n2_islanded", "n2_diverged", "violations_total", "n2_prune_threshold_measured"):
            assert key in row, (hour, key)
        assert row["n1_rows"] == N1_ROWS and row["n2_rows"] == N2_ROWS
    bs = m["dc_severity_blind_spot"]
    assert set(bs) >= {"hours", "spearman_rho", "worst_rank_gap"}
    assert bs["hours"] == len(study.selected)
    assert -1.0 <= bs["spearman_rho"] <= 1.0


def test_bundle_ledgers_carry_measured_numbers_not_placeholders(study):
    for hour, bundle in study.bundles.items():
        text = (bundle / "ledger.md").read_text()
        assert "not yet measured" not in text, hour
        assert "n2_prune_threshold" in text and "dc_severity_blind_spot" in text
        assert "islanded" in text.lower()


def test_screen_false_skips_screening_and_bundles_and_says_so(tmp_path):
    """The increment-2 behaviour stays reachable — as a tested flag, not a way
    to quietly skip screening in production (default is True)."""
    res = run_year_study(tmp_path, hours=24, k=1, window=24, overlap=0, screen=False)
    assert res.screening == {} and res.bundles == {} and res.fault_levels == {} and res.scr == {}
    m = json.loads(res.artifacts["manifest"].read_text())
    assert m["screen"] is False
    assert "screening" not in m and "dc_severity_blind_spot" not in m
    assert not any(k.startswith("bundle_") for k in res.artifacts)
    assert not list(tmp_path.glob("bundle_h*"))
