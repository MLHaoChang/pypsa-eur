# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The SSTI and sub-synchronous screening rows on the grid-readiness scorecard (grid-compliance GC-08,
TRGC 19, 20; GK A2 R1-R9).

The rows read what the campus run's ``ssti_screens`` stage wrote and judge it as the run judged it: the
threshold and clearance come from the run, a flag warns, and whatever the screens could not decide reads
unknown with its reason. Most tests write the run's files by hand (never the engine's output); the last one
runs the real stage on a two-genset campus and checks the row against a UIF computed here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
import math

import pandas as pd
import pytest

from services import campus_electrical_service as ce
from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _rows
from tests.test_grid_readiness_motor_start import _site
from tests.test_grid_readiness_off_grid import _open_tie
from tests.test_grid_readiness_voltage_stability import _template
from tests.test_grid_readiness_voltage_stability import _write as _write_vs

IDS = ("ssti_uif", "subsync_series_resonance", "ssci")
SCREENING = "Screening; the binding answer is the EMT study."
SERIES = "series resonance"


def _uif_table(worst=0.25, bus="MV", case="max", machine="GAS"):
    """The UIF table as the stage writes it: one converter bus row per case, and the PCC row (information)."""
    row = {
        "bus": bus,
        "role": "converter bus",
        "case": case,
        "c": 1.1,
        "machine": machine,
        "s_plant_mva": 80.0,
        "s_machine_mva": 10.0,
        "sc_without_mva": 500.0,
        "sc_tot_mva": 600.0,
        "uif": worst,
        "threshold": 0.1,
        "verdict": "flag",
        "assumed": True,
        "note": "",
        "label": "screening; binding answer is the EMT study",
    }
    return [
        row,
        {**row, "case": "min", "uif": worst / 2},
        {**row, "bus": "GRID", "role": "PCC (information)", "uif": worst * 10},
    ]


def _resonance(margins=(0.4, 3.0), verdict="flag"):
    rows = [
        {
            "case": "max",
            "kind": SERIES,
            "f_er_hz": 30.0,
            "f_comp_hz": 20.0 + m,
            "r_ohm": 1.0,
            "machine": "GAS",
            "mode_hz": 20.0,
            "margin_hz": m,
            "clearance_hz": 1.0,
            "verdict": verdict,
            "assumed": True,
            "note": "",
            "label": "",
        }
        for m in margins
    ]
    return rows + [
        {
            "case": "max",
            "kind": "SSCI",
            "verdict": "not evaluated",
            "margin_hz": float("nan"),
        }
    ]


def _uif_meta(
    verdict="flag", worst=0.25, threshold=0.1, source=None, clause=None, notes=None
):
    return {
        "verdict": verdict,
        "worst_uif": worst,
        "threshold": threshold,
        "source": source,
        "clause": clause,
        "notes": ["S_plant is the sum ..."] if notes is None else notes,
    }


def _sub_meta(verdict="not evaluated", clearance=1.0, source=None, clause=None):
    return {
        "verdict": verdict,
        "clearance_hz": clearance,
        "source": source,
        "clause": clause,
        "notes": [],
    }


def _write(
    d, uif=None, subsync=None, uif_rows=None, resonance=None, error=None, raw_meta=None
):
    """The stage's files in the project's run directory, written by hand."""
    from gridspine.drivers.campus_study import SSTI_UIF_CSV, SUBSYNC_RESONANCE_CSV

    run = d / "campus_electrical" / "run"
    run.mkdir(parents=True, exist_ok=True)
    if error is not None:
        (run / ce.SSTI_SCREENS_ERROR).write_text(json.dumps({"error": error}))
        return
    meta = {
        "uif": _uif_meta() if uif is None else uif,
        "subsync": _sub_meta() if subsync is None else subsync,
        "label": "screening; binding answer is the EMT study",
    }
    (run / ce.SSTI_SCREENS_META).write_text(
        raw_meta if raw_meta is not None else json.dumps(meta)
    )
    pd.DataFrame(
        _uif_table() if uif_rows is None else uif_rows, columns=list(_uif_table()[0])
    ).to_csv(run / SSTI_UIF_CSV, index=False)
    pd.DataFrame(_resonance() if resonance is None else resonance).to_csv(
        run / SUBSYNC_RESONANCE_CSV, index=False
    )


def _profile_dir(tmp_path, **keys):
    import yaml

    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    p.pop("name", None)
    p.update(keys)
    d = tmp_path / "codes"
    d.mkdir(exist_ok=True)
    (d / "gc08_card.yaml").write_text(yaml.safe_dump(p, sort_keys=False))
    return (d,)


def _card(d, profile=PROFILE, dirs=(), n=None):
    out = G.grid_readiness(d, _site() if n is None else n, profile, profile_dirs=dirs)
    return _rows(out), out


# ── the UIF row (TRGC 19) ─────────────────────────────────────────────────────


def test_a_flag_warns_with_the_worst_uif_its_bus_and_the_assumed_threshold(tmp_path):
    _write(tmp_path)
    row = _card(tmp_path)[0]["ssti_uif"]
    assert (row["verdict"], row["project_value"], row["limit"], row["unit"]) == (
        "warn",
        0.25,
        0.1,
        "UIF",
    )
    assert row["source"] == "Assumed screening threshold (the grid code sets none)"
    assert (
        row["assumed"] is True
        and row["section"] == "connection"
        and row["rule"] == G.RULE_UIF
    )
    # the worst converter bus, not the PCC row (information only, its UIF is larger here)
    assert row["note"] == (
        "Worst UIF 0.25 at bus MV (max fault level, machine GAS), above the assumed screening "
        f"threshold 0.1: an EMT SSTI study is warranted (grid-connected state only). {SCREENING}"
    )


def test_an_assumed_clear_passes_and_says_the_threshold_is_assumed(tmp_path):
    # R9
    _write(tmp_path, uif=_uif_meta("clear", worst=0.05), uif_rows=_uif_table(0.05))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert (
        row["verdict"] == "pass"
        and row["project_value"] == 0.05
        and row["assumed"] is True
    )
    assert (
        "at or below the assumed screening threshold 0.1: no EMT SSTI study indicated (grid-connected state only)."
        in row["note"]
    )


def test_a_code_threshold_cites_the_clause_the_run_judged_on(tmp_path):
    t = {"value": 0.2, "clause": "TRGC 19 test clause", "source": "code"}
    dirs = _profile_dir(tmp_path, ssti_uif_threshold=t)
    _write(
        tmp_path,
        uif=_uif_meta(
            "clear",
            worst=0.15,
            threshold=0.2,
            source="code",
            clause="TRGC 19 test clause",
        ),
        uif_rows=_uif_table(0.15),
    )
    row = _card(tmp_path, "gc08_card", dirs)[0]["ssti_uif"]
    assert row["verdict"] == "pass" and row["limit"] == 0.2
    assert row["source"] == "TRGC 19 test clause"  # the run's clause alone (R1)
    assert (
        "at or below the threshold 0.2" in row["note"] and "assumed" not in row["note"]
    )
    assert row["assumed"] is True  # every screening row stays assumed (R9)


@pytest.mark.parametrize(
    "profile_keys, run_threshold",
    [
        ({"ssti_uif_threshold": {"value": 0.2, "clause": "c", "source": "code"}}, 0.1),
        ({}, 0.2),  # the profile dropped its threshold
    ],
    ids=["profile_raised", "profile_dropped"],
)
def test_a_threshold_changed_since_the_run_reads_unknown(
    tmp_path, profile_keys, run_threshold
):
    # R1: the limit shown is the run's; a profile that moved since never sits beside the old verdict.
    dirs = _profile_dir(tmp_path, **profile_keys)
    _write(
        tmp_path,
        uif=_uif_meta("flag", threshold=run_threshold, source="code", clause="c"),
    )
    row = _card(tmp_path, "gc08_card", dirs)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert (
        row["note"]
        == f"The UIF threshold changed since the run: rerun the grid study. {SCREENING}"
    )


def test_the_row_follows_the_engine_at_the_threshold_edge(tmp_path):
    # Equal clears (the engine's >); just above flags. The declared rule recomputes both, exactly.
    _write(tmp_path, uif=_uif_meta("clear", worst=0.1), uif_rows=_uif_table(0.1))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert (
        row["verdict"] == "pass"
        and G._judge(row["project_value"], row["limit"], row["rule"]) == "pass"
    )
    above = 0.1 + 1e-12
    _write(tmp_path, uif=_uif_meta("flag", worst=above), uif_rows=_uif_table(above))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "warn" and row["project_value"] == above
    assert G._judge(row["project_value"], row["limit"], row["rule"]) == "warn"


@pytest.mark.parametrize(
    "worst, shown",
    [
        (None, "None"),
        ("0.3", "'0.3'"),
        (True, "True"),
        ("NaN", "nan"),
        ("inf", "inf"),
    ],
    ids=["blank", "string", "bool", "nan", "inf"],
)
def test_a_worst_uif_that_is_not_a_finite_number_reads_unknown(tmp_path, worst, shown):
    # R2: never NaN, never a pass
    value = float(worst) if worst in ("NaN", "inf") else worst
    _write(tmp_path, uif=_uif_meta("clear", worst=value))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert (
        row["note"]
        == f"The run's worst UIF is not a finite number ({shown}). {SCREENING}"
    )


def test_an_unknown_engine_verdict_reads_unknown_with_the_raw_string(tmp_path):
    _write(tmp_path, uif=_uif_meta("maybe"), subsync=_sub_meta("clear-ish"))
    rows = _card(tmp_path)[0]
    assert (
        rows["ssti_uif"]["verdict"]
        == rows["subsync_series_resonance"]["verdict"]
        == "unknown"
    )
    assert rows["ssti_uif"]["note"] == f"The screen returned 'maybe'. {SCREENING}"
    assert (
        rows["subsync_series_resonance"]["note"]
        == f"The screen returned 'clear-ish'. {SCREENING}"
    )


def test_check_is_not_a_uif_verdict(tmp_path):
    # the UIF screen never says "check": a run that does is read as an unknown verdict, not mapped
    _write(tmp_path, uif=_uif_meta("check"))
    assert (
        _card(tmp_path)[0]["ssti_uif"]["note"]
        == f"The screen returned 'check'. {SCREENING}"
    )


@pytest.mark.parametrize(
    "rows",
    [[], [r for r in _uif_table() if r["role"] != "converter bus"]],
    ids=["empty", "pcc_only"],
)
def test_a_uif_table_without_a_converter_bus_reads_unknown(tmp_path, rows):
    _write(tmp_path, uif_rows=rows)
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown"
    assert row["note"] == f"The run wrote no UIF rows at a converter bus. {SCREENING}"


@pytest.mark.parametrize(
    "note",
    [
        "no synchronous machine (genset) in the campus: no SSTI screen",
        "no converter-interfaced unit or load in the campus: no SSTI screen",
    ],
    ids=["no_genset", "no_converter"],
)
def test_a_screen_not_evaluated_gives_the_engines_reason(tmp_path, note):
    # R3
    _write(
        tmp_path, uif=_uif_meta("not evaluated", worst=None, notes=[note]), uif_rows=[]
    )
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["note"] == f"{note[0].upper()}{note[1:]}. {SCREENING}"


# ── the series-resonance row and SSCI (TRGC 20) ───────────────────────────────


def test_a_series_resonance_flag_warns_with_the_least_margin(tmp_path):
    _write(tmp_path, subsync=_sub_meta("flag"))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert (row["verdict"], row["project_value"], row["limit"], row["unit"]) == (
        "warn",
        0.4,
        1.0,
        "Hz",
    )
    assert row["rule"] == G.RULE_SUBSYNC and row["assumed"] is True
    assert (
        row["source"]
        == "Assumed screening torsional clearance (the grid code sets none)"
    )
    assert (
        "Least margin 0.4 Hz" in row["note"]
        and "below the clearance 1 Hz" in row["note"]
    )
    assert G._judge(row["project_value"], row["limit"], row["rule"]) == "warn"


def test_a_series_resonance_clear_passes(tmp_path):
    _write(
        tmp_path, subsync=_sub_meta("clear"), resonance=_resonance((1.0, 3.0), "clear")
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    # a margin equal to the clearance clears (the engine's <)
    assert row["verdict"] == "pass" and row["project_value"] == 1.0
    assert G._judge(row["project_value"], row["limit"], row["rule"]) == "pass"


@pytest.mark.parametrize(
    "verdict, reason",
    [
        ("check", "No torsional modes given"),
        (
            "not evaluated",
            "Not evaluated: grid-side series compensation needs the TSP's data",
        ),
    ],
)
def test_a_series_resonance_the_scan_could_not_judge_gives_its_reason(
    tmp_path, verdict, reason
):
    # R3
    _write(tmp_path, subsync=_sub_meta(verdict))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"{reason}. {SCREENING}"


def test_a_series_resonance_without_a_margin_reads_unknown(tmp_path):
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=_resonance(()))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run wrote no torsional margin for a series resonance. {SCREENING}"
    )


def test_a_clearance_changed_since_the_run_reads_unknown(tmp_path):
    dirs = _profile_dir(
        tmp_path,
        subsync_torsional_clearance_hz={"value": 2.0, "clause": "c", "source": "code"},
    )
    _write(tmp_path, subsync=_sub_meta("clear", clearance=1.0))
    row = _card(tmp_path, "gc08_card", dirs)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The torsional clearance changed since the run: rerun the grid study. {SCREENING}"
    )


def test_ssci_is_never_evaluated(tmp_path):
    _write(
        tmp_path,
        uif=_uif_meta("clear", worst=0.05),
        subsync=_sub_meta("clear"),
        resonance=_resonance((3.0,), "clear"),
    )
    row = _card(tmp_path)[0]["ssci"]
    assert (
        row["verdict"] == "unknown"
        and row["project_value"] is None
        and row["limit"] is None
    )
    assert row["note"] == f"Not evaluated: converter damping not modelled. {SCREENING}"


# ── a run that did not screen ─────────────────────────────────────────────────


def _three(rows):
    return [rows[i] for i in IDS]


def test_a_failed_stage_reads_unknown_with_its_error(tmp_path):
    _write(tmp_path, error="the PCC has no fault level")
    for row in _three(_card(tmp_path)[0]):
        assert row["verdict"] == "unknown"
        assert (
            row["note"]
            == f"The SSTI screens could not run: the PCC has no fault level. {SCREENING}"
        )


def test_no_run_reads_unknown(tmp_path):
    for row in _three(_card(tmp_path)[0]):
        assert row["verdict"] == "unknown"
        assert (
            row["note"]
            == f"No grid study with the SSTI screens yet: run the grid study. {SCREENING}"
        )


def test_a_run_from_before_the_stage_reads_unknown(tmp_path):
    _write_vs(tmp_path)  # the voltage-stability files, no SSTI meta
    for row in _three(_card(tmp_path)[0]):
        assert row["verdict"] == "unknown"
        assert row["note"] == (
            f"The grid study ran before the SSTI screens were added: rerun the grid study. "
            f"{SCREENING}"
        )


@pytest.mark.parametrize(
    "raw", ["{not json", json.dumps({"uif": "x", "subsync": {}}), json.dumps([1])]
)
def test_an_unreadable_record_reads_unknown(tmp_path, raw):
    _write(tmp_path, raw_meta=raw)
    for row in _three(_card(tmp_path)[0]):
        assert row["verdict"] == "unknown"
        assert (
            row["note"]
            == f"The SSTI screens' record is unreadable: rerun the grid study. {SCREENING}"
        )


# ── off-grid (R4) ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("verdict, worst", [("clear", 0.05), ("flag", 0.25)])
def test_off_grid_with_an_open_tie_keeps_the_uif_row_unknown_and_drops_the_pcc_rows(
    tmp_path, verdict, worst
):
    # R4a: neither a clear nor a lucky flag is shown as the island's judged result
    _template(tmp_path, "off_grid")
    _write(tmp_path, uif=_uif_meta(verdict, worst=worst), uif_rows=_uif_table(worst))
    rows, out = _card(tmp_path, n=_open_tie(_site()))
    # the screen counted the grid's infeed, which understates the islanded UIF: never a pass
    row = rows["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert (
        row["note"]
        == f"The screen counts the grid's infeed; the islanded case is not evaluated. {SCREENING}"
    )
    excluded = {e["id"]: e["reason"] for e in out["excluded"]}
    assert {i: excluded.get(i) for i in G.SUBSYNC_PCC_ROWS} == {
        "subsync_series_resonance": "off-grid, so there is no grid connection to scan from",
        "ssci": "off-grid, so there is no grid connection to scan from",
    }
    assert not set(G.SUBSYNC_PCC_ROWS) & set(rows)


def test_off_grid_by_label_only_keeps_the_uif_row_judged(tmp_path):
    # the connection can still import (SH 41): the grid's infeed is real, so the row stays judged
    _template(tmp_path, "off_grid")
    _write(tmp_path)
    rows, _ = _card(tmp_path)
    assert rows["ssti_uif"]["verdict"] == "warn"
    assert "(grid-connected state only)" in rows["ssti_uif"]["note"]


def test_a_grid_project_keeps_all_three_rows(tmp_path):
    _template(tmp_path, "strong_grid")
    _write(tmp_path)
    rows, out = _card(tmp_path)
    assert set(IDS) <= set(rows) and not set(IDS) & {e["id"] for e in out["excluded"]}


# ── registries keyed by row id (R6) ───────────────────────────────────────────


def test_the_ids_are_unique_and_listed_where_their_siblings_are(tmp_path):
    from harness import gate_words, grid_readiness_calculations as calc
    from services import run_profiles

    _write(tmp_path)
    _, out = _card(tmp_path)
    ids = [r["id"] for r in out["rows"]] + [e["id"] for e in out["excluded"]]
    assert len(ids) == len(set(ids)) and set(IDS) <= set(ids)
    gated = {row: severity for row, severity, *_ in run_profiles.DC_SCORECARD}
    assert {i: gated.get(i) for i in IDS} == dict.fromkeys(IDS, "warn")
    assert all(gate_words.RULE_WORDS[f"dc.{i}"] for i in IDS)
    registered = calc._registered()
    assert (
        registered["ssti_uif"] == G.RULE_UIF
        and registered["subsync_series_resonance"] == G.RULE_SUBSYNC
    )


# ── end to end: the real stage on a two-genset campus (R5) ────────────────────


def _two_genset_run(d):
    """The test hub with two gas generators of different ratings on the MV bus, prepared and screened."""
    from gridspine.drivers.campus_study import draft_from_project, prepare_campus
    from tests.test_campus_electrical_service import hub_network

    n = hub_network()
    for name, p_nom in (("gas_a", 6.0), ("gas_b", 15.0)):
        n.add("Generator", name, bus="mv", carrier="gas", p_nom=p_nom)
        n.generators_t.p[name] = 0.0
    path = d / "network.nc"
    n.export_to_netcdf(str(path))
    run = d / "campus_electrical" / "run"
    prepare_campus(run, draft_from_project(path).spec, path)
    ce._ssti_screens(run, PROFILE, ())
    return run


def test_the_row_matches_a_hand_computed_uif_on_a_two_genset_campus(tmp_path):
    from gridspine.drivers.campus_study import load_run_campus
    from gridspine.static.campus_sc import campus_fault_levels

    run = _two_genset_run(tmp_path)
    campus = load_run_campus(run)
    u = campus.units
    machines = {
        uid: float(u.at[uid, "s_mva"])
        for uid in u.index
        if u.at[uid, "kind"] == "genset"
    }
    assert len(machines) == 2 and len(set(machines.values())) == 2
    # S_plant at each converter bus: every converter unit's s_mva, and the load's p / pf
    plant: dict = {}
    for uid, r in u.iterrows():
        if r["kind"] in ("bess", "pv", "wind", "fuel_cell", "ups"):
            plant[str(r["bus"])] = plant.get(str(r["bus"]), 0.0) + float(r["s_mva"])
        elif r["kind"] == "load":
            plant[str(r["bus"])] = plant.get(str(r["bus"]), 0.0) + float(
                r["p_mw"]
            ) / float(r["pf"])

    def s_k(levels, bus, case):
        row = levels[levels["bus"] == bus].iloc[0]
        return math.sqrt(3.0) * float(row["vn_kv"]) * float(row[f"ikss_{case}_ka"])

    every = set(machines)
    full = campus_fault_levels(campus, every)
    without = {uid: campus_fault_levels(campus, every - {uid}) for uid in machines}
    oracle = []
    for bus, s_plant in plant.items():
        for case in ("max", "min"):
            tot = s_k(full, bus, case)
            for uid, s_i in machines.items():
                share = 1.0 - s_k(without[uid], bus, case) / tot
                oracle.append(s_plant / s_i * share * share)
    worst = max(oracle)
    # the two machines give different UIFs, so a swapped ratio or a dropped square moves the worst
    assert len({round(x, 9) for x in oracle}) > 1

    rows = _card(tmp_path)[0]
    row = rows["ssti_uif"]
    assert row["project_value"] == pytest.approx(worst, abs=1e-6)
    assert row["verdict"] == ("warn" if worst > 0.1 else "pass")
    # the campus enters no series capacitor: the scan cannot judge
    assert rows["subsync_series_resonance"]["verdict"] == "unknown"
    assert (
        "series compensation needs the TSP's data"
        in rows["subsync_series_resonance"]["note"]
    )


# ── guards the mutant run found unfed (R7) ────────────────────────────────────


def _run_dir(d):
    return d / "campus_electrical" / "run"


def test_a_grid_project_with_an_open_tie_keeps_the_uif_row_judged(tmp_path):
    # the island override is for off-grid projects only; an open tie alone does not trigger it
    _template(tmp_path, "strong_grid")
    _write(tmp_path)
    rows, _ = _card(tmp_path, n=_open_tie(_site()))
    assert rows["ssti_uif"]["verdict"] == "warn"


def test_an_open_tie_keeps_the_engines_own_reason_when_it_could_not_evaluate(tmp_path):
    _template(tmp_path, "off_grid")
    note = "no synchronous machine (genset) in the campus: no SSTI screen"
    _write(
        tmp_path, uif=_uif_meta("not evaluated", worst=None, notes=[note]), uif_rows=[]
    )
    row = _card(tmp_path, n=_open_tie(_site()))[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["note"] == f"{note[0].upper()}{note[1:]}. {SCREENING}"


def test_a_not_evaluated_uif_without_notes_says_so(tmp_path):
    _write(tmp_path, uif=_uif_meta("not evaluated", worst=None, notes=[]), uif_rows=[])
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["note"] == f"The screen was not evaluated. {SCREENING}"


def test_a_threshold_that_is_not_a_number_reads_unknown(tmp_path):
    _write(tmp_path, uif=_uif_meta("flag", threshold=None))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["limit"] is None
    assert (
        row["note"] == f"The run's threshold is not a finite number (None). {SCREENING}"
    )


def test_a_clearance_that_is_not_a_number_reads_unknown(tmp_path):
    _write(tmp_path, subsync=_sub_meta("flag", clearance="1"))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run's torsional clearance is not a finite number ('1'). {SCREENING}"
    )


@pytest.mark.parametrize("damage", ["missing", "no_column"])
def test_a_missing_or_malformed_table_reads_unknown(tmp_path, damage):
    from gridspine.drivers.campus_study import SSTI_UIF_CSV, SUBSYNC_RESONANCE_CSV

    _write(tmp_path, subsync=_sub_meta("flag"))
    for name in (SSTI_UIF_CSV, SUBSYNC_RESONANCE_CSV):
        path = _run_dir(tmp_path) / name
        if damage == "missing":
            path.unlink()
        else:
            path.write_text("other\n1\n")
    rows = _card(tmp_path)[0]
    assert (
        rows["ssti_uif"]["note"]
        == f"The run wrote no UIF rows at a converter bus. {SCREENING}"
    )
    assert rows["subsync_series_resonance"]["note"] == (
        f"The run wrote no torsional margin for a series resonance. {SCREENING}"
    )


@pytest.mark.parametrize(
    "content, why",
    [
        ("{not json", "its error file is unreadable"),
        ("[1]", "its error file is unreadable"),
        (json.dumps({"error": None}), "no reason given"),
        (json.dumps({}), "no reason given"),
    ],
    ids=["not_json", "not_a_dict", "null_error", "no_error_key"],
)
def test_any_error_file_reads_unknown(tmp_path, content, why):
    # an error file of any shape wins over a meta beside it: never a judged row
    _write(tmp_path)
    (_run_dir(tmp_path) / ce.SSTI_SCREENS_ERROR).write_text(content)
    for row in _three(_card(tmp_path)[0]):
        assert row["verdict"] == "unknown"
        assert row["note"] == f"The SSTI screens could not run: {why}. {SCREENING}"


def test_an_empty_run_directory_is_no_run(tmp_path):
    _run_dir(tmp_path).mkdir(parents=True)
    for row in _three(_card(tmp_path)[0]):
        assert (
            row["note"]
            == f"No grid study with the SSTI screens yet: run the grid study. {SCREENING}"
        )


def test_a_record_without_the_subsync_screen_is_unreadable(tmp_path):
    _write(tmp_path, raw_meta=json.dumps({"uif": _uif_meta()}))
    for row in _three(_card(tmp_path)[0]):
        assert (
            row["note"]
            == f"The SSTI screens' record is unreadable: rerun the grid study. {SCREENING}"
        )


@pytest.mark.parametrize(
    "source, clause, want, assumed_note",
    [
        ("code", "TRGC 19 c", "TRGC 19 c", False),
        ("assumed", "TRGC 19 c", "TRGC 19 c (assumed)", True),
        (None, "TRGC 19 c", "TRGC 19 c (assumed)", True),
        ("extracted", "TRGC 19 c", "TRGC 19 c (extracted)", True),
        ("code", None, "Assumed screening threshold (the grid code sets none)", True),
    ],
    ids=["code", "assumed_clause", "no_source", "extracted", "code_without_clause"],
)
def test_the_limit_source_follows_the_runs_citation(
    tmp_path, source, clause, want, assumed_note
):
    # today's profile holds the same value under its own citation: the row shows the run's (R1)
    keys = (
        {"ssti_uif_threshold": {"value": 0.1, "clause": "today", "source": "code"}}
        if clause
        else {}
    )
    dirs = _profile_dir(tmp_path, **keys)
    _write(
        tmp_path,
        uif=_uif_meta("clear", worst=0.05, source=source, clause=clause),
        uif_rows=_uif_table(0.05),
    )
    row = _card(tmp_path, "gc08_card", dirs)[0]["ssti_uif"]
    assert row["verdict"] == "pass" and row["source"] == want
    assert ("the assumed screening threshold" in row["note"]) is assumed_note


def test_a_not_evaluated_uif_with_null_notes_says_so(tmp_path):
    uif = dict(_uif_meta("not evaluated", worst=None), notes=None)
    _write(tmp_path, uif=uif, uif_rows=[])
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["note"] == f"The screen was not evaluated. {SCREENING}"


def test_the_source_never_carries_todays_profile_title(tmp_path):
    # R1: the run's clause only; today's profile title may not be the one the run judged on
    t = {"value": 0.1, "clause": "TRGC 19 c", "source": "code"}
    _write(tmp_path, uif=_uif_meta("clear", worst=0.05, source="code", clause="TRGC 19 c"),
           uif_rows=_uif_table(0.05))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=t))[0]["ssti_uif"]
    assert row["source"] == "TRGC 19 c"


# ── the profile moved since the run (GK #1386 A3, #1390 F2) ───────────────────


@pytest.mark.parametrize(
    "key",
    [0.1, "0.1", [0.1], {"clause": "c", "source": "code"}, {"value": "0.1", "clause": "c", "source": "code"}],
    ids=["bare_number", "string", "list", "no_value", "string_value"],
)
def test_a_malformed_profile_threshold_reads_unknown(tmp_path, key):
    # the loader refuses the profile, so today's limit is unknown: never the run's verdict beside it
    _write(tmp_path, uif=_uif_meta("flag", threshold=0.1))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=key))[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"].startswith("Today's grid-code profile is unreadable (")
    assert row["note"].endswith(f"): its limit is unknown. {SCREENING}")


@pytest.mark.parametrize("key", [0.1, {"value": None}, {"value": "0.1"}], ids=["bare", "null", "string"])
def test_a_malformed_key_the_loader_let_through_reads_as_moved(key):
    # belt and braces (GK #1390 F2): _profile_moved never reads a malformed key as the default
    inp = SimpleNamespace(profile={"ssti_uif_threshold": key}, profile_error=None)
    assert G._profile_moved(inp, "ssti_uif_threshold", 0.1, 0.1, "moved") == "moved"


def test_an_absent_key_judges_against_the_default():
    inp = SimpleNamespace(profile={}, profile_error=None)
    assert G._profile_moved(inp, "ssti_uif_threshold", 0.1, 0.1, "moved") is None
    assert G._profile_moved(inp, "ssti_uif_threshold", 0.1, None, "moved") == "moved"


def test_a_threshold_equal_only_after_rounding_has_moved(tmp_path):
    # exact compare: 0.1 + 1e-15 is not the 0.1 the run judged on
    t = {"value": 0.1 + 1e-15, "clause": "c", "source": "code"}
    _write(tmp_path, uif=_uif_meta("flag", threshold=0.1, source="code", clause="c"))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=t))[0]["ssti_uif"]
    assert row["verdict"] == "unknown"


def test_a_clearance_equal_only_after_rounding_has_moved(tmp_path):
    from gridspine.static.subsync_scan import PROFILE_KEY

    t = {"value": 1.0 + 1e-13, "clause": "c", "source": "code"}
    _write(tmp_path, subsync=_sub_meta("flag", clearance=1.0, source="code", clause="c"))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, **{PROFILE_KEY: t}))[0][
        "subsync_series_resonance"
    ]
    assert row["note"] == (
        f"The torsional clearance changed since the run: rerun the grid study. {SCREENING}"
    )


def test_an_unchanged_code_threshold_stays_judged(tmp_path):
    t = {"value": 0.1, "clause": "c", "source": "code"}
    _write(tmp_path, uif=_uif_meta("flag", threshold=0.1, source="code", clause="c"))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=t))[0]["ssti_uif"]
    assert row["verdict"] == "warn"


# ── the UIF table's worst row (GK A2 R2) ──────────────────────────────────────


def _damaged(**changes):
    rows = _uif_table()
    rows[0] = {**rows[0], **changes}  # the worst converter-bus row
    return rows


_ALL_TRUE = [{**r, "uif": True} for r in _uif_table()]


@pytest.mark.parametrize(
    "rows, worst, why",
    [
        (_damaged(uif="high"), 0.25, " has a UIF that is not a finite number ('high')"),
        (_damaged(uif=None), 0.25, " has a UIF that is not a finite number (nan)"),
        (_damaged(uif=True), 0.25, " has a UIF that is not a finite number ('True')"),
        (_ALL_TRUE, 1.0, " has a UIF that is not a finite number (True)"),
        (_damaged(bus=None), 0.25, "'s worst row has no bus"),
        (_damaged(case=" "), 0.25, "'s worst row has no case"),
        (_damaged(machine=None), 0.25, "'s worst row has no machine"),
        (_damaged(uif=0.3), 0.25, "'s worst UIF 0.3 is not the worst UIF 0.25 its record gives"),
    ],
    ids=["text_uif", "missing_uif", "text_bool_uif", "bool_column", "no_bus", "blank_case", "no_machine",
         "max_disagrees"],
)
def test_a_damaged_worst_uif_row_reads_unknown(tmp_path, rows, worst, why):
    _write(tmp_path, uif=_uif_meta("flag", worst=worst), uif_rows=rows)
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"The run's UIF table{why}. {SCREENING}"


@pytest.mark.parametrize("column", ["uif", "bus", "case", "machine"])
def test_a_uif_table_without_a_column_reads_unknown(tmp_path, column):
    from gridspine.drivers.campus_study import SSTI_UIF_CSV

    _write(tmp_path)
    path = _run_dir(tmp_path) / SSTI_UIF_CSV
    pd.read_csv(path).drop(columns=[column]).to_csv(path, index=False)
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown"
    assert row["note"] == f"The run's UIF table has no {column} column. {SCREENING}"


def test_a_worst_uif_that_only_round_trips_exactly_stays_judged(tmp_path):
    # the CSV is read round-trip exact, so the table's max equals the meta's worst UIF bit for bit
    worst = 0.1 + 0.2  # 0.30000000000000004
    _write(tmp_path, uif=_uif_meta("flag", worst=worst), uif_rows=_uif_table(worst))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "warn" and row["project_value"] == worst
