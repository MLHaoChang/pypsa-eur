# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The planning-criteria and grid-strength rows of the grid-readiness scorecard (grid-compliance GC-03,
TRGC 15): they read what the campus run's ``planning`` and ``strength`` stages wrote. The planning row
fails closed: a pass needs the summary's "pass" and counts that agree with it. Each strength ratio is judged
on its own profile minimum only; with none stated it reads unknown with its value shown. Hand-written run
files with generic demo figures."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from services import campus_electrical_service as ce
from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _project, _rows
from tests.test_grid_readiness_motor_start import _site

STRENGTH_IDS = ("campus_scr", "campus_escr", "campus_wscr")
BASIS = "Static screen: the RMS or EMT study gives the final answer."


def _run_dir(d):
    run = d / "campus_electrical" / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "campus.yaml").write_text("campus: {}\n")
    return run


def _card(d, profile=PROFILE, dirs=()):
    return _rows(G.grid_readiness(d, _site(), profile, profile_dirs=dirs))


def _profile_with(tmp_path, **limits):
    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    for k, v in limits.items():
        p[k] = {"value": v, "clause": "demo clause 1.2", "source": "code"}
    codes = tmp_path / "codes"
    codes.mkdir(exist_ok=True)
    (codes / "with_strength.yaml").write_text(json.dumps(p))
    return "with_strength", (codes,)


# ── the planning row ────────────────────────────────────────────────────────


def _planning(
    d,
    verdict="pass",
    cases=4,
    failures=0,
    not_converged=(),
    unscreened=(),
    excluded=(),
    table_fails=None,
):
    run = _run_dir(d)
    meta = {
        "verdict": verdict,
        "cases": cases,
        "failures": failures,
        "excluded": list(excluded),
        "not_converged": list(not_converged),
        "unscreened": list(unscreened),
        "source": "campus N-1 (branches and units)",
        "profile": "demo",
        "notes": [],
    }
    (run / ce.PLANNING_META).write_text(json.dumps(meta))
    fails = failures if table_fails is None else table_fails
    rows = [
        {
            "period": 2030,
            "hour_kind": "max_import",
            "contingency_id": f"c{i}",
            "kind": "branch",
            "hour": 3,
            "verdict": "fail" if i < fails else "pass",
        }
        for i in range(cases)
    ]
    pd.DataFrame(
        rows,
        columns=["period", "hour_kind", "contingency_id", "kind", "hour", "verdict"],
    ).to_csv(run / "campus_planning.csv", index=False)


def test_the_planning_verdict_map_is_pinned_whole():
    seen = {
        v: G.planning_verdict(v)
        for v in (
            "pass",
            "fail",
            "incomplete",
            "not_evaluated",
            "error",
            "",
            "PASS",
            None,
            1,
        )
    }
    assert seen == {
        "pass": "pass",
        "fail": "fail",
        "incomplete": "unknown",
        "not_evaluated": "unknown",
        "error": "unknown",
        "": "unknown",
        "PASS": "unknown",
        None: "unknown",
        1: "unknown",
    }


def test_a_consistent_pass_passes(tmp_path):
    _planning(tmp_path)
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "pass" and row["project_value"] == 0
    assert row["source"].endswith(
        ": thermal, voltage and angle limits after each contingency"
    )
    assert row["note"] == (
        "Contingencies from campus N-1 (branches and units). 4 cases screened at each period's "
        "maximum-import and maximum-export hour. 0 fail."
    )


def test_a_failing_case_fails(tmp_path):
    _planning(tmp_path, verdict="fail", failures=1)
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "fail" and row["project_value"] == 1


def test_a_non_converged_case_is_a_violation(tmp_path):
    _planning(
        tmp_path, verdict="fail", not_converged=["period 2030 hour 3 (max_import)"]
    )
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "fail" and row["project_value"] == 1
    assert (
        "1 load flow did not solve, and each counts as a violation (period 2030 hour 3 (max_import))."
        in row["note"]
    )


def test_a_pass_with_a_non_converged_case_disagrees_with_itself_and_is_unknown(
    tmp_path,
):
    _planning(
        tmp_path, verdict="pass", not_converged=["period 2030 hour 3 (max_import)"]
    )
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert (
        row["note"]
        == "The run's planning record disagrees with its own counts: rerun the campus study"
    )


@pytest.mark.parametrize(
    "over",
    [
        {"excluded": ["TSO_7"]},
        {"unscreened": ["period 2030 hour 3"]},
        {"failures": 1},
        {"table_fails": 1},
        {"cases": 0},
    ],
)
def test_a_pass_whose_counts_or_table_disagree_is_unknown(tmp_path, over):
    _planning(tmp_path, verdict="pass", **over)
    assert _card(tmp_path)[G.PLANNING_ID]["verdict"] == "unknown"


def test_an_incomplete_list_never_passes_and_shows_the_count(tmp_path):
    _planning(tmp_path, verdict="incomplete", excluded=["TSO_7", "line 12"])
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "unknown"
    assert row["note"].endswith(
        "2 contingencies of the list were left out, so the screen cannot pass "
        "(TSO_7, line 12)."
    )


def test_nothing_screened_never_passes(tmp_path):
    _planning(
        tmp_path, verdict="not_evaluated", cases=0, unscreened=["period 2030 hour 3"]
    )
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "unknown"
    assert row["note"] == (
        "Contingencies from campus N-1 (branches and units). No contingency was screened, so the "
        "screen cannot pass. 1 hour had no contingency to screen."
    )


@pytest.mark.parametrize(
    "bad", [{"cases": True}, {"cases": -1}, {"failures": "0"}, {"excluded": "TSO_7"}]
)
def test_an_unreadable_record_is_unknown(tmp_path, bad):
    _planning(tmp_path)
    run = tmp_path / "campus_electrical" / "run"
    meta = {**json.loads((run / ce.PLANNING_META).read_text()), **bad}
    (run / ce.PLANNING_META).write_text(json.dumps(meta))
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "unknown" and row["note"].startswith(
        "The run's planning record is unreadable"
    )


def test_a_planning_table_without_a_verdict_column_is_unknown_never_a_crash(tmp_path):
    _planning(tmp_path)
    run = tmp_path / "campus_electrical" / "run"
    pd.read_csv(run / "campus_planning.csv").drop(columns="verdict").to_csv(
        run / "campus_planning.csv", index=False
    )
    assert _card(tmp_path)[G.PLANNING_ID]["verdict"] == "unknown"


def test_a_planning_error_and_no_run_read_unknown(tmp_path):
    assert (
        _card(tmp_path)[G.PLANNING_ID]["note"]
        == "No campus run with the planning screen yet: run the campus study"
    )
    run = _run_dir(tmp_path)
    assert _card(tmp_path)[G.PLANNING_ID]["note"] == (
        "This campus run is from before the planning screen: rerun the campus study"
    )
    (run / ce.PLANNING_ERROR).write_text(json.dumps({"error": "demo failure"}))
    row = _card(tmp_path)[G.PLANNING_ID]
    assert (
        row["verdict"] == "unknown"
        and row["note"] == "The planning screen could not run: demo failure"
    )


# ── the strength rows ───────────────────────────────────────────────────────


def _poc(
    stage="2030",
    poc="MV",
    p=10.0,
    scr=4.0,
    escr=3.5,
    scr_i=6.0,
    escr_i=5.5,
    outage="L1",
    islands="[]",
):
    return {
        "stage": stage,
        "poc": poc,
        "bus": poc,
        "group": "campus",
        "p_mw": p,
        "q_c_mvar": 0.0,
        "sk_intact_mva": scr_i * p,
        "scr_intact": scr_i,
        "escr_intact": escr_i,
        "worst_outage": outage,
        "sk_worst_mva": scr * p,
        "scr_worst": scr,
        "escr_worst": escr,
        "islanding_outages": islands,
        "outage_method": "exact",
        "note": "",
        "label": "static screen; binding answer is the RMS/EMT study",
    }


def _group(stage="2030", p=10.0, wscr=4.0, wscr_i=6.0, outage="L1", islands="[]"):
    return {
        "stage": stage,
        "group": "campus",
        "pocs": "['MV']",
        "p_mw": p,
        "wscr_intact": wscr_i,
        "worst_outage": outage,
        "wscr_worst": wscr,
        "islanding_outages": islands,
        "outage_method": "exact",
        "note": "",
        "label": "static screen; binding answer is the RMS/EMT study",
    }


def _strength(d, pocs=None, groups=None, meta=None):
    run = _run_dir(d)
    (run / ce.STRENGTH_META).write_text(
        json.dumps(meta or {"pocs": ["MV"], "stages": ["2030"], "notes": []})
    )
    pd.DataFrame(pocs if pocs is not None else [_poc()]).to_csv(
        run / "campus_strength.csv", index=False
    )
    pd.DataFrame(groups if groups is not None else [_group()]).to_csv(
        run / "campus_strength_groups.csv", index=False
    )


def test_with_no_minimum_each_ratio_is_shown_and_unknown(tmp_path):
    _strength(tmp_path)
    card = _card(tmp_path)
    for rid, value in zip(STRENGTH_IDS, (4.0, 3.5, 4.0)):
        row = card[rid]
        assert (
            row["verdict"] == "unknown"
            and row["project_value"] == value
            and row["limit"] is None
        )
        assert row["source"].endswith(f"states no {rid.split('_')[1].upper()} minimum")
        assert row["note"].count(BASIS) == 1 and row["note"].endswith(BASIS)
    assert card["campus_scr"]["note"] == (
        "Lowest SCR 4 at PoC MV, stage 2030 (worst outage L1 · intact 6): the "
        "profile states no SCR minimum, so it is shown, not judged. " + BASIS
    )


def test_each_ratio_is_judged_on_its_own_minimum_only(tmp_path):
    _strength(tmp_path)
    profile, dirs = _profile_with(tmp_path, scr_min=3.0, escr_min=3.6)
    card = _card(tmp_path, profile, dirs)
    scr, escr, wscr = (card[r] for r in STRENGTH_IDS)
    assert (scr["verdict"], scr["limit"], scr["assumed"]) == ("pass", 3.0, False)
    assert scr["note"].endswith(f"at or above the minimum 3. {BASIS}")
    assert (escr["verdict"], escr["limit"]) == ("warn", 3.6)
    assert escr["source"].endswith(": demo clause 1.2")
    # no wscr_min: never judged on the SCR minimum
    assert (wscr["verdict"], wscr["limit"]) == ("unknown", None)
    for row in (scr, escr, wscr):
        assert row["note"].count(BASIS) == 1


def test_a_value_just_below_the_minimum_never_prints_equal_to_it(tmp_path):
    _strength(tmp_path, pocs=[_poc(scr=2.99996)])
    profile, dirs = _profile_with(tmp_path, scr_min=3.0)
    row = _card(tmp_path, profile, dirs)["campus_scr"]
    assert row["verdict"] == "warn" and row["project_value"] == 2.99996
    assert (
        "Lowest SCR 2.99996 at PoC MV" in row["note"]
        and "below the minimum 3." in row["note"]
    )


def test_the_least_ratio_over_pocs_and_stages_is_shown_and_ties_go_to_the_first(
    tmp_path,
):
    pocs = [
        _poc("2031", "LV2", scr=2.5, outage="L9"),
        _poc("2030", "MV", scr=3.0),
        _poc("2030", "LV1", scr=2.5, outage="L4"),
        _poc("2030", "LV3", p=0.0, scr=float("nan")),
    ]
    _strength(tmp_path, pocs=pocs)
    row = _card(tmp_path)["campus_scr"]
    assert (
        row["project_value"] == 2.5
        and "at PoC LV1, stage 2030 (worst outage L4 · intact 6)" in row["note"]
    )


def test_a_stage_with_no_converter_connected_is_left_out(tmp_path):
    _strength(
        tmp_path,
        pocs=[_poc(p=0.0, scr=float("nan"), escr=float("nan")), _poc("2031", scr=5.0)],
    )
    row = _card(tmp_path)["campus_scr"]
    assert row["project_value"] == 5.0 and "stage 2031" in row["note"]


def test_a_connected_poc_with_no_ratio_is_unknown_and_named_never_dropped(tmp_path):
    _strength(
        tmp_path,
        pocs=[
            _poc(scr=4.0),
            _poc("2031", "LV1", scr=float("nan"), outage="L7"),
            _poc("2032", "LV2", scr=float("nan"), outage=float("nan")),
        ],
    )
    profile, dirs = _profile_with(tmp_path, scr_min=3.0)
    row = _card(tmp_path, profile, dirs)["campus_scr"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == (
        "No SCR at PoC LV1, stage 2031 (worst outage L7): the fault level under its worst outage "
        "was not found. " + BASIS
    )


def test_a_gap_never_hides_a_ratio_found_below_the_minimum(tmp_path):
    _strength(
        tmp_path,
        pocs=[
            _poc("2030", "LV1", scr=float("nan"), outage=float("nan")),
            _poc("2031", scr=2.0),
        ],
    )
    profile, dirs = _profile_with(tmp_path, scr_min=3.0)
    row = _card(tmp_path, profile, dirs)["campus_scr"]
    assert row["verdict"] == "warn" and row["project_value"] == 2.0
    assert row["note"] == (
        "Lowest SCR 2 at PoC MV, stage 2031 (worst outage L1 · intact 6): below the minimum 3. "
        "No SCR at PoC LV1, stage 2030: the fault level under its worst outage was not found. "
        + BASIS
    )


def test_an_islanding_outage_reads_zero_and_is_named(tmp_path):
    _strength(
        tmp_path,
        pocs=[
            _poc(
                scr=float("nan"),
                escr=float("nan"),
                outage=float("nan"),
                islands="['T2', 'T1']",
            )
        ],
        groups=[_group(islands="['T1']")],
    )
    profile, dirs = _profile_with(tmp_path, scr_min=3.0)
    card = _card(tmp_path, profile, dirs)
    scr = card["campus_scr"]
    assert scr["verdict"] == "warn" and scr["project_value"] == 0.0
    assert (
        "(outage T1 islands it from the grid, so the SCR is 0 · intact 6)"
        in scr["note"]
    )
    assert card["campus_wscr"]["project_value"] == 0.0


def test_no_converter_at_any_stage_is_unknown(tmp_path):
    _strength(tmp_path, pocs=[_poc(p=0.0)], groups=[_group(p=0.0)])
    for rid in STRENGTH_IDS:
        assert _card(tmp_path)[rid]["note"].startswith(
            "No converter is connected at any stage"
        )


def test_an_open_tie_reads_not_evaluated(tmp_path):
    run = _run_dir(tmp_path)
    (run / ce.STRENGTH_META).write_text(
        json.dumps({"verdict": ce.STRENGTH_NOT_EVALUATED, "note": ce.STRENGTH_OPEN_TIE})
    )
    for rid in STRENGTH_IDS:
        row = _card(tmp_path)[rid]
        assert row["verdict"] == "unknown"
        assert row["note"] == (
            "The tie to the grid is normally open, so there is no grid infeed to judge strength "
            f"against. Strength is not evaluated. {BASIS}"
        )


def test_a_strength_error_an_older_run_and_a_missing_column_read_unknown(tmp_path):
    run = _run_dir(tmp_path)
    assert (
        "is from before the strength screen: rerun the campus study"
        in _card(tmp_path)["campus_scr"]["note"]
    )
    (run / ce.STRENGTH_ERROR).write_text(json.dumps({"error": "demo failure"}))
    assert _card(tmp_path)["campus_escr"]["note"].startswith(
        "The strength screen could not run: demo failure"
    )
    (run / ce.STRENGTH_ERROR).unlink()
    _strength(tmp_path, pocs=[{k: v for k, v in _poc().items() if k != "scr_worst"}])
    row = _card(tmp_path)["campus_scr"]
    assert row["verdict"] == "unknown" and "has no scr_worst column" in row["note"]


def test_the_scr_row_is_unchanged(tmp_path):
    """The network SCR row stays as it was before the campus strength rows (hash of its whole output)."""
    import hashlib

    d, n = _project(tmp_path, "eh_datacenter")
    row = _rows(G.grid_readiness(d, n, PROFILE))["scr"]
    digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    assert digest == "0715ecd9ff23a1fd9fc1848703ea3adc4c71220e206ebee1159b8f817f6e3925"
    assert not math.isnan(row["project_value"])


# ── edge cases the mutants found ────────────────────────────────────────────


def test_failures_with_no_case_and_a_missing_table_disagree(tmp_path):
    _planning(tmp_path, verdict="fail", cases=0, failures=1)
    assert _card(tmp_path)[G.PLANNING_ID]["note"].startswith(
        "The run's planning record disagrees"
    )
    _planning(tmp_path)
    (tmp_path / "campus_electrical" / "run" / "campus_planning.csv").unlink()
    row = _card(tmp_path)[G.PLANNING_ID]
    assert row["verdict"] == "unknown" and row["note"].startswith(
        "The run's planning record disagrees"
    )


def test_three_names_are_listed_whole_and_a_fourth_is_elided(tmp_path):
    _planning(tmp_path, verdict="fail", not_converged=["a", "b", "c"])
    assert "(a, b, c)." in _card(tmp_path)[G.PLANNING_ID]["note"]
    _planning(tmp_path, verdict="incomplete", excluded=["a", "b", "c"])
    assert _card(tmp_path)[G.PLANNING_ID]["note"].endswith("(a, b, c).")
    _planning(tmp_path, verdict="incomplete", excluded=["a", "b", "c", "d"])
    assert _card(tmp_path)[G.PLANNING_ID]["note"].endswith("(a, b, c, …).")


def test_an_error_with_no_reason_says_so(tmp_path):
    run = _run_dir(tmp_path)
    (run / ce.PLANNING_ERROR).write_text("{}")
    (run / ce.STRENGTH_ERROR).write_text("{}")
    card = _card(tmp_path)
    assert (
        card[G.PLANNING_ID]["note"]
        == "The planning screen could not run: no reason given"
    )
    assert (
        card["campus_scr"]["note"]
        == f"The strength screen could not run: no reason given. {BASIS}"
    )


def test_a_profile_error_is_the_source_of_every_new_row(tmp_path):
    _planning(tmp_path)
    _strength(tmp_path)
    card = _card(tmp_path, "no_such_code")
    for rid in (G.PLANNING_ID, *STRENGTH_IDS):
        assert "unknown grid-code profile" in card[rid]["source"]
    assert (
        card["campus_scr"]["verdict"] == "unknown"
        and card["campus_scr"]["project_value"] == 4.0
    )


def test_an_open_tie_with_no_note_still_says_why(tmp_path):
    run = _run_dir(tmp_path)
    (run / ce.STRENGTH_META).write_text(
        json.dumps({"verdict": ce.STRENGTH_NOT_EVALUATED})
    )
    assert (
        _card(tmp_path)["campus_scr"]["note"]
        == f"There is no grid infeed. Strength is not evaluated. {BASIS}"
    )


def test_the_least_ratio_is_found_when_it_comes_last(tmp_path):
    _strength(
        tmp_path,
        pocs=[_poc("2030", "MV", scr=4.0), _poc("2031", "MV", scr=2.0, outage="L5")],
    )
    row = _card(tmp_path)["campus_scr"]
    assert row["project_value"] == 2.0 and "stage 2031 (worst outage L5" in row["note"]


def test_a_row_with_no_converter_size_is_a_gap(tmp_path):
    _strength(
        tmp_path,
        pocs=[_poc(scr=4.0), _poc("2031", "LV1", p=float("nan"), scr=5.0, outage="L7")],
    )
    row = _card(tmp_path)["campus_scr"]
    assert row["verdict"] == "unknown" and row["note"].startswith(
        "No SCR at PoC LV1, stage 2031 (worst outage L7)"
    )


def test_no_outage_named_reads_so_in_a_value_and_in_a_gap(tmp_path):
    _strength(tmp_path, pocs=[_poc(scr=4.0, outage=float("nan"))])
    assert "(no outage lowers it · intact 6)" in _card(tmp_path)["campus_scr"]["note"]
    _strength(tmp_path, pocs=[_poc(scr=float("nan"), outage=float("nan"))])
    assert _card(tmp_path)["campus_scr"]["note"].startswith(
        "No SCR at PoC MV, stage 2030: the fault level under its worst outage"
    )


def test_a_numeric_outage_name_reads_as_written(tmp_path):
    _strength(
        tmp_path,
        pocs=[_poc(scr=4.0, outage="7"), _poc("2031", scr=5.0, outage=float("nan"))],
    )
    assert "(worst outage 7 · intact 6)" in _card(tmp_path)["campus_scr"]["note"]


def test_a_lone_gap_with_a_minimum_and_a_gap_beside_a_value_with_none_read_unknown(
    tmp_path,
):
    _strength(tmp_path, pocs=[_poc(scr=float("nan"), outage="L7")])
    profile, dirs = _profile_with(tmp_path, scr_min=3.0)
    row = _card(tmp_path, profile, dirs)["campus_scr"]
    assert row["verdict"] == "unknown" and row["limit"] == 3.0
    _strength(
        tmp_path,
        pocs=[
            _poc("2030", "LV1", scr=float("nan"), outage="L7"),
            _poc("2031", scr=2.0),
        ],
    )
    row = _card(tmp_path)["campus_scr"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == (
        "No SCR at PoC LV1, stage 2030 (worst outage L7): the fault level under its worst outage "
        f"was not found. {BASIS}"
    )


@pytest.mark.parametrize(
    "cell, expect",
    [
        (["T1"], ["T1"]),
        ("['T2', 'T1']", ["T2", "T1"]),
        ("('T1',)", ["T1"]),
        ("'T1'", ["T1"]),
        ("T1 (", ["T1 ("]),
        ("", []),
        ("  ", []),
        (float("nan"), []),
        (None, []),
    ],
)
def test_an_islanding_cell_is_read_as_a_list(cell, expect):
    assert G._islanding(cell) == expect


def test_the_strength_rows_point_at_the_fault_level_bus_like_the_scr_row(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    _strength(d)
    rows = _rows(G.grid_readiness(d, n, PROFILE))
    assert rows["scr"]["target"] is not None
    for rid in STRENGTH_IDS:
        assert rows[rid]["target"] == rows["scr"]["target"]


def test_a_table_with_more_rows_than_cases_disagrees(tmp_path):
    _planning(tmp_path)
    path = tmp_path / "campus_electrical" / "run" / "campus_planning.csv"
    frame = pd.read_csv(path)
    pd.concat([frame, frame.tail(1)]).to_csv(path, index=False)
    assert _card(tmp_path)[G.PLANNING_ID]["note"].startswith(
        "The run's planning record disagrees"
    )
