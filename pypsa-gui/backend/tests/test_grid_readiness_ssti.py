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
    assert (
        row["source"].endswith(": TRGC 19 test clause")
        and "assumed" not in row["source"].lower()
    )
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
    assert row["verdict"] == "unknown" and row["note"] == f"{note}. {SCREENING}"


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
