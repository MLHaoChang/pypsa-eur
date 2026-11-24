# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""GC-08e: the scorecard's UIF row and the pack judge the SSTI UIF screen's island rows (GC-08b). A campus the
run screened islanded is judged on its grid and island rows together (a grid project) or on its island rows
alone (an islanded project); the note names the case of the worst row; a run whose record has no islanded
verdict reads unknown "rerun", never judged on its grid rows alone."""

import json
import math

import pandas as pd
import pytest
from gridspine.drivers.campus_study import SSTI_UIF_CSV
from gridspine.static import ssti_uif as su

from services import campus_electrical_service as ce
from services.adequacy import grid_readiness as G
from services.grid_compliance import ssti_source as S
from tests.test_grid_compliance_ssti_source import _pack, _project
from tests.test_grid_readiness_motor_start import _site
from tests.test_grid_readiness_off_grid import _open_tie
from tests.test_grid_readiness_ssti import (
    _card,
    _grid_run_with_open_tie,
    _two_genset_run,
    _uif_meta,
    _uif_table,
    _write,
)
from tests.test_grid_readiness_voltage_stability import _template

SCREENING = "Screening; the binding answer is the EMT study."
#: The open-tie hub's run (``_grid_run_with_open_tie`` at 2000 MVA), recorded from the engine: the grid cases'
#: worst UIF (GAS_B, max) and the island cases' (GAS_B, the island's SC_i from GAS_A alone).
GRID_WORST = 0.13671203327453138
ISLAND_WORST = 1.9699394258240364


def _meta(run):
    return json.loads((run / ce.SSTI_SCREENS_META).read_text())["uif"]


def _rows_of(table):
    return [(r[0], r[1], r[2]) for r in table.rows]


# ── the real run: an open-tie grid project ────────────────────────────────────


def test_the_meta_records_the_island_verdict_and_worst(tmp_path):
    run, _ = _grid_run_with_open_tie(tmp_path, 2000.0)
    meta = _meta(run)
    assert meta["verdict"] == "flag" and meta["worst_uif"] == GRID_WORST
    assert meta["island_verdict"] == "flag" and meta["island_worst_uif"] == ISLAND_WORST


def test_a_campus_that_does_not_island_records_no_island_verdict(tmp_path):
    run = _two_genset_run(tmp_path)
    meta = _meta(run)
    assert meta["island_verdict"] == "not evaluated" and meta["island_worst_uif"] is None
    table = pd.read_csv(run / SSTI_UIF_CSV)
    assert not G.uif_island(table).any()


def test_an_open_tie_grid_project_warns_on_its_island_uif(tmp_path):
    _, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    row = _card(tmp_path, n=n)[0]["ssti_uif"]
    assert row["verdict"] == "warn" and row["project_value"] == ISLAND_WORST
    assert row["note"] == (
        "Worst UIF 1.97 at bus MV (islanded, max fault level, machine GAS_B), above the assumed screening "
        "threshold 0.1: an EMT SSTI study is warranted (grid-connected and islanded states). " + SCREENING
    )


def test_the_pack_judges_the_island_rows_and_holds_the_cards_worst_row(tmp_path, monkeypatch):
    run, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    card = _card(tmp_path, n=n)[0]["ssti_uif"]
    _, pack = _pack(_project(monkeypatch, tmp_path, n=n))
    t = pack["ssti_uif"]
    assert t.caption == card["note"]
    printed = [r for r in t.rows if r[1] in su.ISLAND_CASES]
    assert [(r[1], r[2], r[3], r[5]) for r in printed] == [
        ("island max", "GAS_A", "0.788", "warn"),
        ("island max", "GAS_B", "1.97", "warn"),
        ("island min", "GAS_A", "0.788", "warn"),
        ("island min", "GAS_B", "1.97", "warn"),
    ]
    # GK A2 C1: the card's worst row is one of the rows the pack prints judged
    assert ("MV", "island max", "GAS_B") in _rows_of(t)
    assert next(r for r in t.rows if r[1] == "island max" and r[2] == "GAS_B")[5] == "warn"
    # the grid rows print first and judged as before
    assert [(r[1], r[2], r[5]) for r in t.rows[:4]] == [
        ("max", "GAS_A", "pass"),
        ("max", "GAS_B", "warn"),
        ("min", "GAS_A", "pass"),
        ("min", "GAS_B", "warn"),
    ]


def test_a_run_from_before_the_island_verdict_reads_rerun_on_the_card_and_not_judged_in_the_pack(
    tmp_path, monkeypatch
):
    # GK A2 C3, C4: a saved GC-08b run, its island rows under the old role and no island verdict in its record
    run, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    meta = json.loads((run / ce.SSTI_SCREENS_META).read_text())
    del meta["uif"]["island_verdict"], meta["uif"]["island_worst_uif"]
    (run / ce.SSTI_SCREENS_META).write_text(json.dumps(meta))
    text = (run / SSTI_UIF_CSV).read_text()
    assert f",{su.ROLE_ISLAND}," in text
    (run / SSTI_UIF_CSV).write_text(text.replace(f",{su.ROLE_ISLAND},", ",island converter bus (not judged),"))
    row = _card(tmp_path, n=n)[0]["ssti_uif"]
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"The run predates the islanded UIF verdict: rerun the grid study. {SCREENING}"
    _, pack = _pack(_project(monkeypatch, tmp_path, n=n))
    t = pack["ssti_uif"]
    printed = [r for r in t.rows if r[1] in su.ISLAND_CASES]
    assert len(printed) == 4 and {r[5] for r in printed} == {S.NOT_JUDGED}
    assert {r[5] for r in t.rows} == {S.NOT_JUDGED}


def test_the_old_island_role_still_reads_as_island_rows(tmp_path):
    # a run under the GC-08b role whose record has the island verdict is judged as today's
    run, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    text = (run / SSTI_UIF_CSV).read_text()
    (run / SSTI_UIF_CSV).write_text(text.replace(f",{su.ROLE_ISLAND},", ",island converter bus (not judged),"))
    row = _card(tmp_path, n=n)[0]["ssti_uif"]
    assert row["verdict"] == "warn" and row["project_value"] == ISLAND_WORST


def test_the_island_roles_are_the_engines():
    # GK A2 C4: the reader's roles, whole, against a hand-written literal; the first is the engine's own
    assert G.UIF_ROLES_ISLAND == ("island converter bus", "island converter bus (not judged)")
    assert G.UIF_ROLE_ISLAND == su.ROLE_ISLAND
    assert G.UIF_ROLE_GRID == su.ROLE_CONVERTER


def test_without_gridspine_the_saved_island_rows_still_print(tmp_path, monkeypatch):
    import builtins

    run, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    project = _project(monkeypatch, tmp_path, n=n)
    _, with_engine = _pack(project)
    real = builtins.__import__

    def no_ssti_uif(name, *args, **kwargs):
        if name == "gridspine.static.ssti_uif":
            raise ImportError("no gridspine in this build")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_ssti_uif)
    rows = S._uif_table(
        {"verdict": "unknown", "note": ""},
        pd.read_csv(run / SSTI_UIF_CSV),
        "grid",
        "uif.csv",
    ).rows
    monkeypatch.setattr(builtins, "__import__", real)
    printed = [r for r in rows if r[1] in su.ISLAND_CASES]
    assert printed and {r[5] for r in printed} == {S.NOT_JUDGED}
    assert len(printed) == sum(r[1] in su.ISLAND_CASES for r in with_engine["ssti_uif"].rows)


# ── hand-written runs: which rows the card judges, and what it refuses ────────


def _island(uif=0.5, bus="MV", machine="GAS", role=G.UIF_ROLE_ISLAND):
    """The island rows as the stage writes them: one per island case, the min at half the max's UIF."""
    base = _uif_table()[0]
    out = []
    for case, c, u in (("island max", 1.1, uif), ("island min", 1.0, uif / 2)):
        verdict = "not evaluated" if math.isnan(u) else "flag" if u > 0.1 else "clear"
        out.append({**base, "bus": bus, "machine": machine, "role": role, "case": case, "c": c, "uif": u,
                    "verdict": verdict})
    return out


def _island_meta(verdict="flag", worst=0.25, island_verdict="flag", island_worst=0.5):
    return {**_uif_meta(verdict, worst=worst), "island_verdict": island_verdict, "island_worst_uif": island_worst}


def _grid(tmp_path, meta, rows):
    _template(tmp_path, "strong_grid")
    _write(tmp_path, uif=meta, uif_rows=rows)
    return _card(tmp_path)[0]["ssti_uif"]


def _islanded(tmp_path, meta, rows):
    _template(tmp_path, "off_grid")
    _write(tmp_path, uif=meta, uif_rows=rows)
    return _card(tmp_path, n=_open_tie(_site()))[0]["ssti_uif"]


def test_a_closed_tie_campus_whose_worst_uif_is_islanded_says_so(tmp_path):
    # a closed tie that declares island requirements: grid cases clear, the island flags
    row = _grid(tmp_path, _island_meta("clear", 0.08, "flag", 0.5), _uif_table(0.08) + _island(0.5, bus="LV"))
    assert row["verdict"] == "warn" and row["project_value"] == 0.5
    assert row["note"] == (
        "Worst UIF 0.5 at bus LV (islanded, max fault level, machine GAS), above the assumed screening "
        f"threshold 0.1: an EMT SSTI study is warranted (grid-connected and islanded states). {SCREENING}"
    )


def test_a_campus_that_islands_with_both_states_clear_passes(tmp_path):
    row = _grid(tmp_path, _island_meta("clear", 0.08, "clear", 0.06), _uif_table(0.08) + _island(0.06))
    assert row["verdict"] == "pass" and row["project_value"] == 0.08
    assert row["note"] == (
        "Worst UIF 0.08 at bus MV (max fault level, machine GAS), at or below the assumed screening threshold "
        f"0.1: no EMT SSTI study indicated (grid-connected and islanded states). {SCREENING}"
    )


def test_on_a_tie_the_grid_row_is_named(tmp_path):
    row = _grid(tmp_path, _island_meta("flag", 0.5, "flag", 0.5), _uif_table(0.5) + _island(0.5, bus="LV"))
    assert row["verdict"] == "warn" and "at bus MV (max fault level" in row["note"]


def test_an_islanded_project_is_judged_on_its_island_rows_alone(tmp_path, monkeypatch):
    # the grid rows flag, the island clears: the island is the state the project runs in
    row = _islanded(tmp_path, _island_meta("flag", 0.25, "clear", 0.06), _uif_table(0.25) + _island(0.06))
    assert row["verdict"] == "pass" and row["project_value"] == 0.06
    assert row["note"] == (
        "Worst UIF 0.06 at bus MV (islanded, max fault level, machine GAS), at or below the assumed screening "
        f"threshold 0.1: no EMT SSTI study indicated (islanded state only). {SCREENING}"
    )
    _, pack = _pack(_project(monkeypatch, tmp_path, n=_open_tie(_site())))
    t = pack["ssti_uif"]
    # the grid rows leave the pack; the island rows print judged under the card's limit
    assert [r[1:] for r in t.rows] == [
        ["island max", "GAS", "0.06", "0.1", "pass", ""],
        ["island min", "GAS", "0.03", "0.1", "pass", ""],
    ]
    assert t.caption == row["note"]


def test_an_islanded_project_warns_on_its_island_uif(tmp_path):
    row = _islanded(tmp_path, _island_meta("clear", 0.08, "flag", 0.5), _uif_table(0.08) + _island(0.5))
    assert row["verdict"] == "warn" and row["project_value"] == 0.5
    assert row["note"].endswith(f"an EMT SSTI study is warranted (islanded state only). {SCREENING}")


def test_an_islanded_project_without_island_rows_keeps_its_override(tmp_path, monkeypatch):
    # a run before the island case: the grid rows count an infeed the island lacks
    row = _islanded(tmp_path, _uif_meta("flag", worst=0.25), _uif_table(0.25))
    assert row["verdict"] == "unknown"
    assert row["note"] == f"The screen counts the grid's infeed; the islanded case is not evaluated. {SCREENING}"
    _, pack = _pack(_project(monkeypatch, tmp_path, n=_open_tie(_site())))
    assert {r[5] for r in pack["ssti_uif"].rows} == {S.ISLANDED}


def test_an_island_bus_no_machine_feeds_keeps_a_clear_grid_unknown(tmp_path, monkeypatch):
    rows = _uif_table(0.08) + _island(math.nan, bus="LV")
    row = _grid(tmp_path, _island_meta("clear", 0.08, "not evaluated", None), rows)
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == (
        "The islanded UIF is not evaluated at bus LV: no synchronous machine feeds it with the grid out. "
        + SCREENING
    )
    _, pack = _pack(_project(monkeypatch, tmp_path))
    assert {r[5] for r in pack["ssti_uif"].rows} == {S.NOT_JUDGED}


def test_a_flag_warns_beside_an_island_bus_no_machine_feeds(tmp_path, monkeypatch):
    rows = _uif_table(0.08) + _island(0.5, bus="MV") + _island(math.nan, bus="LV") + _island(math.nan, bus="HV")
    row = _grid(tmp_path, _island_meta("clear", 0.08, "flag", 0.5), rows)
    assert row["verdict"] == "warn" and row["project_value"] == 0.5
    assert row["note"] == (
        "Worst UIF 0.5 at bus MV (islanded, max fault level, machine GAS), above the assumed screening "
        "threshold 0.1: an EMT SSTI study is warranted (grid-connected and islanded states). The islanded UIF "
        f"is not evaluated at buses LV, HV: no synchronous machine feeds them with the grid out. {SCREENING}"
    )
    _, pack = _pack(_project(monkeypatch, tmp_path))
    by_bus = {(r[0], r[1]): (r[3], r[5]) for r in pack["ssti_uif"].rows}
    assert by_bus[("MV", "island max")] == ("0.5", "warn")
    assert by_bus[("LV", "island max")] == (S.NON_FINITE, S.NOT_EVALUATED)
    assert by_bus[("MV", "max")] == ("0.08", "pass")


@pytest.mark.parametrize(
    "meta, rows, why",
    [
        # GK A2 C7: the exact-equality check on the islanded worst UIF, as on the grid's
        (
            _island_meta("clear", 0.08, "flag", 0.5001),
            _uif_table(0.08) + _island(0.5),
            "The run's UIF table's worst islanded UIF 0.5 is not the worst islanded UIF 0.5001 its record "
            "gives",
        ),
        (
            _island_meta("clear", 0.08, "flag", None),
            _uif_table(0.08) + _island(0.5),
            "The run's UIF table's worst islanded UIF 0.5 is not the worst islanded UIF no value its record gives",
        ),
        (
            _island_meta("clear", 0.08, "clear", None),
            _uif_table(0.08) + _island(0.06),
            "The run's UIF table's worst islanded UIF 0.06 is not the worst islanded UIF no value its record "
            "gives",
        ),
        (
            _island_meta("clear", 0.08, "flag", None),
            _uif_table(0.08) + _island(math.nan),
            "The run's worst islanded UIF no value contradicts its islanded verdict flag against the threshold 0.1",
        ),
        (
            _island_meta("clear", 0.08, "clear", None),
            _uif_table(0.08) + _island(math.nan),
            "The run's worst islanded UIF no value contradicts its islanded verdict clear against the threshold "
            "0.1",
        ),
        (
            _island_meta("clear", 0.08, "clear", 0.5),
            _uif_table(0.08) + _island(0.5),
            "The run's worst islanded UIF 0.5 contradicts its islanded verdict clear against the threshold 0.1",
        ),
        (
            _island_meta("clear", 0.08, "flag", 0.06),
            _uif_table(0.08) + _island(0.06),
            "The run's worst islanded UIF 0.06 contradicts its islanded verdict flag against the threshold 0.1",
        ),
        (
            # a clear beside an unfed bus: the engine never clears then
            _island_meta("clear", 0.08, "clear", 0.06),
            _uif_table(0.08) + _island(0.06) + _island(math.nan, bus="LV"),
            "The run's worst islanded UIF 0.06 contradicts its islanded verdict clear against the threshold 0.1",
        ),
        (
            _island_meta("clear", 0.08, "maybe", 0.5),
            _uif_table(0.08) + _island(0.5),
            "The screen returned maybe for the islanded case",
        ),
        (
            _island_meta("clear", 0.08, None, 0.5),
            _uif_table(0.08) + _island(0.5),
            "The screen returned an empty value for the islanded case",
        ),
        (
            _island_meta("clear", 0.08, "flag", "0.5"),
            _uif_table(0.08) + _island(0.5),
            "The run's islanded worst UIF is not a finite number (0.5)",
        ),
        (
            _island_meta("clear", 0.08, "flag", 0.5),
            _uif_table(0.08),
            "The run's record gives the islanded verdict flag, but its UIF table has no islanded rows",
        ),
        (
            # an island row with no UIF that does not read "not evaluated"
            _island_meta("clear", 0.08, "flag", 0.5),
            _uif_table(0.08) + _island(0.5) + [{**_island(math.nan, bus="LV")[0], "verdict": "flag"}],
            "The run's UIF table has a UIF that is not a finite number (an empty value)",
        ),
        (
            {k: v for k, v in _island_meta("clear", 0.08).items() if k not in ("island_verdict", "island_worst_uif")},
            _uif_table(0.08) + _island(0.5),
            "The run predates the islanded UIF verdict: rerun the grid study",
        ),
    ],
)
def test_an_island_record_the_table_contradicts_reads_unknown(tmp_path, monkeypatch, meta, rows, why):
    row = _grid(tmp_path, meta, rows)
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"] == f"{why}. {SCREENING}"
    if any(r["role"] == G.UIF_ROLE_ISLAND for r in rows):
        # GK A2 C3: the pack never judges a row the card did not
        _, pack = _pack(_project(monkeypatch, tmp_path))
        assert {r[5] for r in pack["ssti_uif"].rows} == {S.NOT_JUDGED}


def test_uif_judged_picks_the_rows_by_case():
    table = pd.DataFrame(_uif_table(0.25) + _island(0.5))
    grid = table["role"] == G.UIF_ROLE_GRID
    island = table["role"] == G.UIF_ROLE_ISLAND
    assert G.uif_judged(table, "grid").tolist() == (grid | island).tolist()
    assert G.uif_judged(table, "off_grid").tolist() == (grid | island).tolist()
    assert G.uif_judged(table, "islanded").tolist() == island.tolist()
    alone = pd.DataFrame(_uif_table(0.25))
    assert G.uif_judged(alone, "islanded").tolist() == (alone["role"] == G.UIF_ROLE_GRID).tolist()
    assert not G.uif_judged(None, "grid").any()
