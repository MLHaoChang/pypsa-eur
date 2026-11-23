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
        and row["rule"] == {"comparator": "<=", "otherwise": "warn", "tolerance": 0.0}
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
        (None, "an empty value"),
        ("0.3", "0.3"),
        (True, "True"),
        ("NaN", "an empty value"),
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
    assert rows["ssti_uif"]["note"] == f"The screen returned maybe. {SCREENING}"
    assert (
        rows["subsync_series_resonance"]["note"]
        == f"The screen returned clear-ish. {SCREENING}"
    )


def test_check_is_not_a_uif_verdict(tmp_path):
    # the UIF screen never says "check": a run that does is read as an unknown verdict, not mapped
    _write(tmp_path, uif=_uif_meta("check"))
    assert (
        _card(tmp_path)[0]["ssti_uif"]["note"]
        == f"The screen returned check. {SCREENING}"
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
    assert (
        row["verdict"] == "unknown"
        and row["note"] == f"{note[0].upper()}{note[1:]}. {SCREENING}"
    )


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
    assert (
        row["rule"] == {"comparator": ">=", "otherwise": "warn", "tolerance": 0.0}
        and row["assumed"] is True
    )
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


# --- GC-01c: margins from judged rows only (GK A2 G1), an assumed-mode pass says so (G2) ---------------


def _check_row(case="min"):
    """A series row turned ``check`` because a genset gave no modes: a resonance, no margin."""
    return {
        **_resonance((0.4,))[0],
        "case": case,
        "margin_hz": float("nan"),
        "verdict": "check",
        "note": "no torsional modes given for genset(s) GEN2; the screen does not pass on the others alone",
    }


def _band_clear_row():
    """A far-side series row with no series resonance in the scan band: clear, no f_er, no margin."""
    return {
        "case": "max",
        "kind": SERIES,
        "f_er_hz": float("nan"),
        "f_comp_hz": float("nan"),
        "machine": float("nan"),
        "mode_hz": float("nan"),
        "margin_hz": float("nan"),
        "clearance_hz": 1.0,
        "verdict": "clear",
        "assumed": True,
        "note": "clear in the 5-45 Hz scan band: series capacitor(s) ['SC1'] scanned, no series resonance",
    }


def test_a_flag_beside_an_unjudged_row_warns_with_its_margin(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("flag"),
        resonance=[*_resonance((0.4,)), _check_row()],
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert (row["verdict"], row["project_value"]) == ("warn", 0.4)


def test_a_flag_beside_a_band_clear_row_warns(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("flag"),
        resonance=[*_resonance((0.4,)), _band_clear_row()],
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert (row["verdict"], row["project_value"]) == ("warn", 0.4)


def test_a_clear_beside_a_band_clear_row_passes(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("clear"),
        resonance=[*_resonance((3.0,), "clear"), _band_clear_row()],
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert (row["verdict"], row["project_value"]) == ("pass", 3.0)


def test_a_judged_row_without_a_margin_still_reads_unknown(tmp_path):
    rows = _resonance((0.4,))
    rows[0]["margin_hz"] = float("nan")
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=rows)
    assert _card(tmp_path)[0]["subsync_series_resonance"]["verdict"] == "unknown"


def test_a_resonance_table_without_verdicts_has_no_judged_margin(tmp_path):
    rows = [{k: v for k, v in r.items() if k != "verdict"} for r in _resonance((0.4,))]
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=rows)
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run wrote no torsional margin for a series resonance. {SCREENING}"
    )


def _assumed_rows(said, margins=(1.0, 3.0), verdict="clear"):
    rows = _resonance(margins, verdict)
    rows[0]["assumed_modes"] = said
    return rows


def test_a_pass_resting_on_assumed_modes_says_so(tmp_path):
    _write(
        tmp_path, subsync=_sub_meta("clear"), resonance=_assumed_rows("GEN1 30.5 Hz")
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "pass"
    assert row["note"] == (
        "Least margin 1 Hz: the electrical resonance's complement 21 Hz against the torsional mode 20 Hz of GAS "
        "(max), at or above the clearance 1 Hz. Clear rests on assumed torsional mode(s): GEN1 30.5 Hz. "
        f"{SCREENING}"
    )


def test_a_pass_on_two_gensets_assumed_modes_names_both(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("clear"),
        resonance=_assumed_rows("GEN1 30.5 Hz; GEN2 20, 35 Hz"),
    )
    note = _card(tmp_path)[0]["subsync_series_resonance"]["note"]
    assert (
        "at or above the clearance 1 Hz. Clear rests on assumed torsional mode(s): GEN1 30.5 Hz; GEN2 20, 35 Hz. "
        f"{SCREENING}" in note
    )


def test_the_assumed_sentence_skips_rows_without_one(tmp_path):
    rows = _assumed_rows("GEN1 30.5 Hz")
    rows[0]["assumed_modes"], rows[1]["assumed_modes"] = float("nan"), "GEN1 30.5 Hz"
    _write(tmp_path, subsync=_sub_meta("clear"), resonance=rows)
    note = _card(tmp_path)[0]["subsync_series_resonance"]["note"]
    assert "Clear rests on assumed torsional mode(s): GEN1 30.5 Hz. " in note


def test_a_blank_assumed_modes_cell_adds_no_sentence(tmp_path):
    _write(tmp_path, subsync=_sub_meta("clear"), resonance=_assumed_rows("   "))
    assert (
        "assumed torsional"
        not in _card(tmp_path)[0]["subsync_series_resonance"]["note"]
    )


def test_a_resonance_table_without_margins_has_no_judged_margin(tmp_path):
    rows = [
        {k: v for k, v in r.items() if k != "margin_hz"} for r in _resonance((0.4,))
    ]
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=rows)
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run wrote no torsional margin for a series resonance. {SCREENING}"
    )


def test_a_warn_never_carries_the_assumed_clear_sentence(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("flag"),
        resonance=_assumed_rows("GEN1 30.5 Hz", (0.4, 3.0), "flag"),
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "warn" and "assumed torsional" not in row["note"]


def test_a_row_worse_than_the_runs_verdict_reads_unknown(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("clear"),
        resonance=[*_resonance((3.0,), "clear"), _check_row()],
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run's resonance table has a check row, worse than its verdict clear. {SCREENING}"
    )


def test_a_check_row_beside_a_flag_is_not_worse(tmp_path):
    _write(
        tmp_path,
        subsync=_sub_meta("flag"),
        resonance=[*_resonance((0.4,)), _check_row()],
    )
    assert _card(tmp_path)[0]["subsync_series_resonance"]["verdict"] == "warn"


def test_a_pass_on_given_modes_adds_no_assumed_sentence(tmp_path):
    _write(
        tmp_path, subsync=_sub_meta("clear"), resonance=_resonance((1.0, 3.0), "clear")
    )
    assert (
        "assumed torsional"
        not in _card(tmp_path)[0]["subsync_series_resonance"]["note"]
    )


_TSP = "not evaluated: grid-side series compensation needs the TSP's data"
_NO_NORMAL = "no normal case given (pcc.sk_normal_mva), so it is not scanned"


def _undecided(verdict, notes):
    """Series rows as the scan writes them when it cannot judge: one per case, each with its note."""
    return [
        {
            "case": c,
            "kind": SERIES,
            "verdict": verdict,
            "note": n,
            "margin_hz": float("nan"),
        }
        for c, n in zip(("max", "min"), notes)
    ]


@pytest.mark.parametrize(
    "verdict, rows, meta_notes, reason",
    [
        ("check", ["no torsional modes given"] * 2, [], "No torsional modes given"),
        ("check", [], [], "No torsional modes given"),
        ("not evaluated", [_TSP, _TSP], [], _TSP[0].upper() + _TSP[1:]),
        ("not evaluated", [], [_NO_NORMAL], _NO_NORMAL[0].upper() + _NO_NORMAL[1:]),
        (
            "not evaluated",
            ["a reason the reader has never seen"] * 2,
            [],
            "A reason the reader has never seen",
        ),
        ("not evaluated", ["first", None], [], "First"),
        ("not evaluated", [], [], "The scan was not evaluated"),
    ],
    ids=[
        "check_note",
        "check_bare",
        "tsp",
        "no_normal_case",
        "unknown_reason",
        "blank_note",
        "nothing",
    ],
)
def test_a_series_resonance_the_scan_could_not_judge_gives_the_engines_reason(
    tmp_path, verdict, rows, meta_notes, reason
):
    # R3, GK A2 B7 (GC-TEXT-1): the note follows the engine's own reason, never a stock sentence
    meta = dict(_sub_meta(verdict), notes=meta_notes)
    _write(
        tmp_path,
        subsync=meta,
        resonance=_undecided(verdict, rows) or [{"kind": "SSCI"}],
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"{reason}. {SCREENING}"


def test_an_undecided_scan_with_no_resonance_table_gives_the_meta_reason(tmp_path):
    # mutants.py survivor grid_readiness.py:1456: with the resonance CSV gone the reader must not
    # look into a missing table; the meta's own note is the reason
    from gridspine.drivers.campus_study import SUBSYNC_RESONANCE_CSV

    _write(tmp_path, subsync=dict(_sub_meta("not evaluated"), notes=[_NO_NORMAL]))
    (_run_dir(tmp_path) / SUBSYNC_RESONANCE_CSV).unlink()
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"{_NO_NORMAL[0].upper()}{_NO_NORMAL[1:]}. {SCREENING}"


def test_a_resonance_table_with_only_the_reason_columns_gives_its_reason(tmp_path):
    # mutants.py survivor grid_readiness.py:1456 (<= -> <): exactly kind, verdict and note is enough
    rows = [{"kind": SERIES, "verdict": "not evaluated", "note": _TSP}]
    _write(
        tmp_path,
        subsync=dict(_sub_meta("not evaluated"), notes=[_NO_NORMAL]),
        resonance=rows,
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["note"] == f"{_TSP[0].upper()}{_TSP[1:]}. {SCREENING}"


def test_a_real_run_says_why_its_scan_was_not_evaluated(tmp_path):
    # the stage's own notes on a campus with no series capacitor and no normal case
    from gridspine.drivers.campus_study import SUBSYNC_RESONANCE_CSV

    run = _two_genset_run(tmp_path)
    res = pd.read_csv(run / SUBSYNC_RESONANCE_CSV)
    notes = res[(res["kind"] == SERIES) & (res["verdict"] == "not evaluated")]["note"]
    engine = notes.iloc[0]
    assert set(notes) == {engine} and "TSP's data" in engine
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["note"] == f"{engine[0].upper()}{engine[1:]}. {SCREENING}"
    assert "(pcc.sk_normal_mva)" in row["note"]
    assert "synchronous machine impedance is not in the scan" in row["note"]


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


@pytest.mark.parametrize("raw", ["{not json", json.dumps([1])])
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
    assert registered["ssti_uif"] == {
        "comparator": "<=",
        "otherwise": "warn",
        "tolerance": 0.0,
    } and registered["subsync_series_resonance"] == {
        "comparator": ">=",
        "otherwise": "warn",
        "tolerance": 0.0,
    }


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


# ── GC-01c end to end: a real stage run with a series capacitor and genset modes, read by the card (G5) ──


def _moded_run(d, modes):
    """The two-genset hub with a series capacitor between the grid transformer and MV, the gensets' torsional
    modes ``{unit: [(Hz, source)]}`` entered, prepared and screened by the app's stage. Seen from MV the
    capacitor's resonance has its complement at about 11.51 Hz (max) and 11.05 Hz (min)."""
    from gridspine.drivers.campus_study import draft_from_project, prepare_campus
    from tests.test_campus_electrical_service import hub_network

    n = hub_network()
    for name, p_nom in (("gas_a", 6.0), ("gas_b", 15.0)):
        n.add("Generator", name, bus="mv", carrier="gas", p_nom=p_nom)
        n.generators_t.p[name] = 0.0
    path = d / "network.nc"
    n.export_to_netcdf(str(path))
    spec = draft_from_project(path).spec
    c = spec["campus"]
    c["buses"]["MVT"] = {"vn_kv": 20.0}
    c["transformers"]["GRID_IMPORT"]["lv_bus"] = "MVT"
    c["series_capacitors"] = {
        "SC1": {
            "from_bus": "MVT",
            "to_bus": "MV",
            "xc_ohm": {"value": 0.5, "source": "datasheet"},
        }
    }
    for unit, given in modes.items():
        c["units"][unit]["torsional_modes_hz"] = [
            {"value": f, "source": src} for f, src in given
        ]
    run = _run_dir(d)
    prepare_campus(run, spec, path)
    ce._ssti_screens(run, PROFILE, ())
    return run


def test_a_real_flag_beside_an_unmoded_genset_warns_on_the_card(tmp_path):
    from gridspine.drivers.campus_study import SUBSYNC_RESONANCE_CSV

    run = _moded_run(tmp_path, {"GAS_A": [(11.3, "datasheet")]})
    res = pd.read_csv(run / SUBSYNC_RESONANCE_CSV)
    series = res[res["kind"] == SERIES]
    assert set(series["verdict"]) == {"flag"} and set(series["machine"]) == {"GAS_A"}
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "warn"
    assert row["project_value"] == pytest.approx(
        float(series["margin_hz"].min()), abs=1e-9
    )
    assert row["project_value"] < 1.0


def test_a_clear_on_one_genset_reads_unknown_while_the_other_has_no_modes(tmp_path):
    _moded_run(tmp_path, {"GAS_A": [(14.0, "datasheet")]})
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"].startswith(
        "No torsional modes given for genset(s) GAS_B; the screen does not pass on the others alone; "
    )


def test_a_real_pass_on_an_assumed_mode_says_so_on_the_card(tmp_path):
    _moded_run(tmp_path, {"GAS_A": [(14.0, "datasheet")], "GAS_B": [(20.0, "assumed")]})
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "pass"
    assert (
        "at or above the clearance 1 Hz. Clear rests on assumed torsional mode(s): GAS_B 20 Hz. "
        in row["note"]
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
    assert (
        row["verdict"] == "unknown"
        and row["note"] == f"{note[0].upper()}{note[1:]}. {SCREENING}"
    )


def test_a_not_evaluated_uif_without_notes_says_so(tmp_path):
    _write(tmp_path, uif=_uif_meta("not evaluated", worst=None, notes=[]), uif_rows=[])
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["note"] == f"The screen was not evaluated. {SCREENING}"


def test_a_threshold_that_is_not_a_number_reads_unknown(tmp_path):
    _write(tmp_path, uif=_uif_meta("flag", threshold=None))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["limit"] is None
    assert (
        row["note"]
        == f"The run's threshold is not a finite number (an empty value). {SCREENING}"
    )


def test_a_clearance_that_is_not_a_number_reads_unknown(tmp_path):
    _write(tmp_path, subsync=_sub_meta("flag", clearance="1"))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run's torsional clearance is not a finite number (1). {SCREENING}"
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


@pytest.mark.parametrize(
    "meta, broken",
    [
        ({"uif": _uif_meta()}, "subsync_series_resonance"),
        ({"uif": _uif_meta(), "subsync": "x"}, "subsync_series_resonance"),
        ({"uif": "x", "subsync": _sub_meta()}, "ssti_uif"),
    ],
    ids=["no_subsync", "subsync_not_a_dict", "uif_not_a_dict"],
)
def test_a_broken_screen_record_blanks_its_own_row_only(tmp_path, meta, broken):
    # GK A2 N1: one screen's contract error reads unknown for that screen alone
    _write(tmp_path, raw_meta=json.dumps(meta))
    rows = _card(tmp_path)[0]
    assert rows[broken]["note"] == (
        f"The SSTI screens' record is unreadable: rerun the grid study. {SCREENING}"
    )
    other = ({"ssti_uif", "subsync_series_resonance"} - {broken}).pop()
    assert "record is unreadable" not in rows[other]["note"]


@pytest.mark.parametrize(
    "source, clause, want, assumed_note",
    [
        ("code", "TRGC 19 c", "TRGC 19 c", False),
        ("assumed", "TRGC 19 c", "TRGC 19 c (assumed)", True),
        (None, "TRGC 19 c", "TRGC 19 c (assumed)", True),
        ("extracted", "TRGC 19 c", "TRGC 19 c (extracted)", True),
        (
            "code",
            None,
            "The grid code's threshold, its clause not recorded by the run (assumed)",
            True,
        ),
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
    _write(
        tmp_path,
        uif=_uif_meta("clear", worst=0.05, source="code", clause="TRGC 19 c"),
        uif_rows=_uif_table(0.05),
    )
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=t))[0][
        "ssti_uif"
    ]
    assert row["source"] == "TRGC 19 c"


# ── the profile moved since the run (GK #1386 A3, #1390 F2) ───────────────────


@pytest.mark.parametrize(
    "key",
    [
        0.1,
        "0.1",
        [0.1],
        {"clause": "c", "source": "code"},
        {"value": "0.1", "clause": "c", "source": "code"},
    ],
    ids=["bare_number", "string", "list", "no_value", "string_value"],
)
def test_a_malformed_profile_threshold_reads_unknown(tmp_path, key):
    # the loader refuses the profile, so today's limit is unknown: never the run's verdict beside it
    _write(tmp_path, uif=_uif_meta("flag", threshold=0.1))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=key))[
        0
    ]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"].startswith("Today's grid-code profile is unreadable (")
    assert row["note"].endswith(f"): its limit is unknown. {SCREENING}")


@pytest.mark.parametrize(
    "key", [0.1, {"value": None}, {"value": "0.1"}], ids=["bare", "null", "string"]
)
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
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=t))[0][
        "ssti_uif"
    ]
    assert row["verdict"] == "unknown"


def test_a_clearance_equal_only_after_rounding_has_moved(tmp_path):
    from gridspine.static.subsync_scan import PROFILE_KEY

    t = {"value": 1.0 + 1e-13, "clause": "c", "source": "code"}
    _write(
        tmp_path, subsync=_sub_meta("flag", clearance=1.0, source="code", clause="c")
    )
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, **{PROFILE_KEY: t}))[0][
        "subsync_series_resonance"
    ]
    assert row["note"] == (
        f"The torsional clearance changed since the run: rerun the grid study. {SCREENING}"
    )


def test_an_unchanged_code_threshold_stays_judged(tmp_path):
    t = {"value": 0.1, "clause": "c", "source": "code"}
    _write(tmp_path, uif=_uif_meta("flag", threshold=0.1, source="code", clause="c"))
    row = _card(tmp_path, "gc08_card", _profile_dir(tmp_path, ssti_uif_threshold=t))[0][
        "ssti_uif"
    ]
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
        (_damaged(uif="high"), 0.25, " has a UIF that is not a finite number (high)"),
        (
            _damaged(uif=None),
            0.25,
            " has a UIF that is not a finite number (an empty value)",
        ),
        (_damaged(uif=True), 0.25, " has a UIF that is not a finite number (True)"),
        (_ALL_TRUE, 1.0, " has a UIF that is not a finite number (True)"),
        (_damaged(bus=None), 0.25, "'s worst row has no bus"),
        (_damaged(case=" "), 0.25, "'s worst row has no case"),
        (_damaged(machine=None), 0.25, "'s worst row has no machine"),
        (
            _damaged(uif=0.3),
            0.25,
            "'s worst UIF 0.3 is not the worst UIF 0.25 its record gives",
        ),
    ],
    ids=[
        "text_uif",
        "missing_uif",
        "text_bool_uif",
        "bool_column",
        "no_bus",
        "blank_case",
        "no_machine",
        "max_disagrees",
    ],
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


# ── a grid project with an open tie: the grid's infeed is on the counted path (GK A2 R4a) ──


def _grid_run_with_open_tie(d, sk_mva):
    """The two-genset hub on a grid template with its import Link held at 0 MW (an open tie in the
    saved network), prepared and screened by the real stage."""
    from gridspine.drivers.campus_study import draft_from_project, prepare_campus
    from tests.test_campus_electrical_service import hub_network

    _template(d, "strong_grid")
    n = hub_network()
    n.buses.at["grid", "eh_sk_mva"] = sk_mva
    for name, p_nom in (("gas_a", 6.0), ("gas_b", 15.0)):
        n.add("Generator", name, bus="mv", carrier="gas", p_nom=p_nom)
        n.generators_t.p[name] = 0.0
    _open_tie(n)
    n.links_t.p0["grid_import"] = 0.0
    n.links_t.p1["grid_import"] = 0.0
    path = d / "network.nc"
    n.export_to_netcdf(str(path))
    run = d / "campus_electrical" / "run"
    prepare_campus(run, draft_from_project(path).spec, path)
    ce._ssti_screens(run, PROFILE, ())
    return run, n


def test_a_grid_project_with_an_open_tie_screens_with_the_grids_infeed(tmp_path):
    # The archetype says the PoC is closed: a Link at 0 MW limits the energy bought, not the fault
    # current. The campus run seats the grid at the PoC from its entered S_k'' (the ext_grid), so the
    # UIF's SC_tot counts a real infeed and the row stays judged.
    from gridspine.drivers.campus_study import SSTI_UIF_CSV, load_run_campus

    run, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    inp = SimpleNamespace(n=n, links=list(n.links.index))
    assert G._tie_open(inp)  # the open-tie case, not a closed one
    grid = load_run_campus(run).net.ext_grid
    assert len(grid) == 1 and float(grid.iloc[0]["s_sc_max_mva"]) == 2000.0
    table = pd.read_csv(run / SSTI_UIF_CSV)
    pcc = table[table["role"] != "converter bus"]
    # at the PoC the grid's 2000 MVA dominates: the machines alone could not reach it
    assert (pcc["sc_tot_mva"] > 1000.0).all()
    row = _card(tmp_path, n=n)[0]["ssti_uif"]
    assert row["verdict"] in ("pass", "warn") and row["project_value"] is not None
    assert "(grid-connected state only)" in row["note"]


# ── the resonance table's least-margin row, and verdicts the tables contradict (GK A2 R2) ──


def _bad_resonance(**changes):
    rows = _resonance((0.4, 3.0), "flag")
    rows[0] = {**rows[0], **changes}  # the least-margin series row
    return rows


@pytest.mark.parametrize(
    "rows, why",
    [
        (
            _bad_resonance(margin_hz="x"),
            " has a torsional margin that is not a finite number (x)",
        ),
        (
            _bad_resonance(margin_hz=None),
            " has a torsional margin that is not a finite number (an empty value)",
        ),
        (_bad_resonance(machine=None), "'s least-margin row has no machine"),
        (_bad_resonance(case=" "), "'s least-margin row has no case"),
        (
            [{k: v for k, v in r.items() if k != "machine"} for r in _resonance()],
            "'s least-margin row has no machine",
        ),
        (
            _bad_resonance(f_comp_hz=None, mode_hz=None),
            "'s least-margin row has no f_comp_hz, mode_hz",
        ),
    ],
    ids=[
        "text_margin",
        "missing_margin",
        "no_machine",
        "blank_case",
        "no_machine_column",
        "no_frequencies",
    ],
)
def test_a_damaged_least_margin_row_reads_unknown(tmp_path, rows, why):
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=rows)
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"The run's resonance table{why}. {SCREENING}"


def test_a_resonance_table_with_no_margin_column_reads_unknown(tmp_path):
    rows = [{k: v for k, v in r.items() if k != "margin_hz"} for r in _resonance()]
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=rows)
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == (
        f"The run wrote no torsional margin for a series resonance. {SCREENING}"
    )


@pytest.mark.parametrize(
    "verdict, margins, words",
    [
        ("clear", (0.4, 3.0), "0.4 Hz contradicts its verdict clear"),
        ("flag", (1.0, 3.0), "1 Hz contradicts its verdict flag"),
    ],
    ids=["clear_below", "flag_at_edge"],
)
def test_a_least_margin_that_contradicts_the_verdict_reads_unknown(
    tmp_path, verdict, margins, words
):
    _write(tmp_path, subsync=_sub_meta(verdict), resonance=_resonance(margins, verdict))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert row["note"] == (
        f"The run's least margin {words} against the clearance 1 Hz. {SCREENING}"
    )


def test_a_margin_just_below_the_clearance_flags(tmp_path):
    m = math.nextafter(1.0, 0.0)
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=_resonance((m, 3.0), "flag"))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "warn" and row["project_value"] == m
    assert G._judge(row["project_value"], row["limit"], row["rule"]) == "warn"


@pytest.mark.parametrize(
    "verdict, worst, words",
    [
        ("clear", 0.25, "0.25 contradicts its verdict clear"),
        ("flag", 0.1, "0.1 contradicts its verdict flag"),
    ],
    ids=["clear_above", "flag_at_edge"],
)
def test_a_worst_uif_that_contradicts_the_verdict_reads_unknown(
    tmp_path, verdict, worst, words
):
    _write(tmp_path, uif=_uif_meta(verdict, worst=worst), uif_rows=_uif_table(worst))
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown"
    assert (
        row["note"]
        == f"The run's worst UIF {words} against the threshold 0.1. {SCREENING}"
    )


@pytest.mark.parametrize(
    "text, want",
    [
        ("", "No reason given"),
        ("ends with a period.", "Ends with a period"),
        ("  ", "No reason given"),
    ],
)
def test_a_note_is_one_sentence_whatever_the_engine_wrote(text, want):
    assert G._ssti_say(text) == f"{want}. {SCREENING}"


# ── GK A2 (6) review: B1-B5, N2, N4 ──────────────────────────────────────────


def test_the_verdict_maps_are_pinned_whole():
    # B5 (#1767/#1790): the map compared whole against a hand-written one, so an added or changed
    # entry fails here
    assert G.SSTI_VERDICTS == {
        "ssti_uif": {"flag": "warn", "clear": "pass", "not evaluated": "unknown"},
        "subsync_series_resonance": {
            "flag": "warn",
            "clear": "pass",
            "check": "unknown",
            "not evaluated": "unknown",
        },
    }


_VERDICT_CASES = [
    (["flag"], "The screen's verdict is unreadable"),
    ({"x": 1}, "The screen's verdict is unreadable"),
    (1, "The screen's verdict is unreadable"),
    (None, "The screen gave no verdict"),
    ("", "The screen gave no verdict"),
    ("  ", "The screen gave no verdict"),
]
_VERDICT_IDS = ["list", "dict", "number", "none", "empty", "blank"]


@pytest.mark.parametrize("verdict, why", _VERDICT_CASES, ids=_VERDICT_IDS)
def test_a_uif_verdict_that_is_not_a_word_reads_unknown(tmp_path, verdict, why):
    # B1: never a TypeError that takes the whole scorecard down
    _write(tmp_path, uif=_uif_meta(verdict))
    rows, out = _card(tmp_path)
    assert rows["ssti_uif"]["verdict"] == "unknown"
    assert rows["ssti_uif"]["note"] == f"{why}. {SCREENING}"
    assert (
        rows["subsync_series_resonance"]["note"]
        == f"The scan was not evaluated. {SCREENING}"
    )


@pytest.mark.parametrize("verdict, why", _VERDICT_CASES, ids=_VERDICT_IDS)
def test_a_subsync_verdict_that_is_not_a_word_reads_unknown(tmp_path, verdict, why):
    _write(tmp_path, subsync=_sub_meta(verdict))
    rows = _card(tmp_path)[0]
    assert rows["subsync_series_resonance"]["verdict"] == "unknown"
    assert rows["subsync_series_resonance"]["note"] == f"{why}. {SCREENING}"
    assert rows["ssti_uif"]["verdict"] == "warn"


def test_an_all_bool_margin_column_reads_unknown(tmp_path):
    # B2: True is not 1 Hz
    rows = [
        {**r, "margin_hz": True} if r["kind"] == SERIES else r
        for r in _resonance((0.4, 3.0), "clear")
    ]
    rows = [r for r in rows if r["kind"] == SERIES]
    _write(tmp_path, subsync=_sub_meta("clear"), resonance=rows)
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == (
        "The run's resonance table has a torsional margin that is not a finite number"
        f" (True). {SCREENING}"
    )


@pytest.mark.parametrize(
    "changes, shown",
    [
        ({"f_comp_hz": "abc"}, "abc"),
        ({"mode_hz": "xyz"}, "xyz"),
        ({"mode_hz": "inf"}, "inf"),
    ],
    ids=["text_complement", "text_mode", "infinite_mode"],
)
def test_a_frequency_that_is_not_a_number_reads_unknown(tmp_path, changes, shown):
    # B3
    _write(tmp_path, subsync=_sub_meta("flag"), resonance=_bad_resonance(**changes))
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["verdict"] == "unknown"
    assert row["note"] == (
        "The run's resonance table's least-margin row has a frequency that is not a finite"
        f" number ({shown}). {SCREENING}"
    )


def test_a_judged_subsync_note_shows_the_frequencies_as_numbers(tmp_path):
    _write(
        tmp_path, subsync=_sub_meta("flag"), resonance=_resonance((0.4, 3.0), "flag")
    )
    row = _card(tmp_path)[0]["subsync_series_resonance"]
    assert row["note"] == (
        "Least margin 0.4 Hz: the electrical resonance's complement 20.4 Hz against the"
        " torsional mode 20 Hz of GAS (max), below the clearance 1 Hz. " + SCREENING
    )


@pytest.mark.parametrize(
    "error, why",
    [
        ({"a": [1]}, "its error is unreadable"),
        (["x"], "its error is unreadable"),
        (7, "its error is unreadable"),
        ("  ", "no reason given"),
        ("KeyError", "KeyError"),
    ],
    ids=["dict", "list", "number", "blank", "text"],
)
def test_an_error_that_is_not_text_never_shows_its_repr(tmp_path, error, why):
    # B4
    _write(tmp_path)
    (_run_dir(tmp_path) / ce.SSTI_SCREENS_ERROR).write_text(
        json.dumps({"error": error})
    )
    for row in _three(_card(tmp_path)[0]):
        assert row["note"] == f"The SSTI screens could not run: {why}. {SCREENING}"


@pytest.mark.parametrize(
    "notes, why",
    [
        ("one note", "One note"),
        ([{"k": None}, "no genset"], "An unreadable note; no genset"),
        ({"k": 1}, "The screen's notes are unreadable"),
        (7, "The screen's notes are unreadable"),
    ],
    ids=["string", "dict_entry", "dict", "number"],
)
def test_the_engines_notes_never_show_a_repr(tmp_path, notes, why):
    # B4: a string is one note, never split into letters
    uif = dict(_uif_meta("not evaluated", worst=None), notes=notes)
    _write(tmp_path, uif=uif, uif_rows=[])
    row = _card(tmp_path)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["note"] == f"{why}. {SCREENING}"


def test_an_error_path_that_is_a_directory_reads_unknown(tmp_path):
    # N4: the rows never stay judged beside an error entry they cannot read
    _write(tmp_path)
    (_run_dir(tmp_path) / ce.SSTI_SCREENS_ERROR).mkdir()
    for row in _three(_card(tmp_path)[0]):
        assert row["note"] == (
            f"The SSTI screens could not run: its error file is unreadable. {SCREENING}"
        )


def test_a_non_scalar_value_in_a_note_is_unreadable():
    assert G._ssti_shown({"a": 1}) == "an unreadable value"
    assert G._ssti_shown([1]) == "an unreadable value"
    assert G._ssti_shown("0.3") == "0.3"
