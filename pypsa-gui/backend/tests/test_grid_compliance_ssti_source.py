# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""GC-08's tables in the grid-compliance report pack (``SOURCES["GC-08"]``, GK A2 S1-S5).

The provider reads the run through the scorecard's own read, so each test sets the run's files as
``test_grid_readiness_ssti`` does and compares the pack against the scorecard row and against tables
written out by hand here (S3), never built from the module's constants.
"""

from __future__ import annotations

import re

import pytest

from services.adequacy import grid_readiness as G
from services.grid_compliance import sources
from services.grid_compliance import ssti_source as S
from tests.test_grid_compliance import hub  # noqa: F401 - the registered-project fixture
from tests.test_grid_readiness import PROFILE
from tests.test_grid_readiness_motor_start import _site
from tests.test_grid_readiness_off_grid import _open_tie
from tests.test_grid_readiness_ssti import (
    _card,
    _profile_dir,
    _band_clear_row,
    _check_row,
    _resonance,
    _sub_meta,
    _two_genset_run,
    _uif_meta,
    _uif_table,
    _write,
)
from tests.test_grid_readiness_voltage_stability import _template

UIF_CSV = "campus_electrical/run/campus_ssti_uif.csv"
RES_CSV = "campus_electrical/run/campus_subsync_resonance.csv"
UIF_HEAD = ["Bus", "Case", "Machine", "UIF", "Threshold", "Verdict", "Note"]
RES_HEAD = [
    "Case",
    "Machine",
    "Resonance (Hz)",
    "Complement (Hz)",
    "Mode (Hz)",
    "Margin (Hz)",
    "Verdict",
    "Note",
]


def _project(monkeypatch, d, n=None, profile=PROFILE, dirs=()):
    """A stand-in project whose directory is ``d``: only the registry lookup is replaced."""
    codes = dirs[0] if dirs else d / "codes"
    net = _site() if n is None else n
    monkeypatch.setattr(
        G,
        "_project_inputs",
        lambda project, p, source: (d, net, profile, codes, "saved"),
    )
    return object()


def _pack(project):
    state, tables = sources.read(project, "GC-08")
    return state, {t.table_id: t for t in tables or ()}


def _no_repr(tables):
    # S4: plain values only
    for t in tables.values():
        for text in [t.caption, *(c for r in t.rows for c in r)]:
            assert not re.search(
                r"np\.|\bnan\b|\bNone\b|Timestamp\(|array\(|\{'", text
            ), text


# ── present: whole tables against hand-written ones (S3, S4) ──────────────────


def test_a_judged_run_gives_both_tables_cell_for_cell(tmp_path, monkeypatch):
    uif = _uif_table()
    uif[0] = {**uif[0], "threshold": " "}  # a blank number
    uif[1] = {
        **uif[1],
        "machine": None,
        "threshold": float("inf"),
    }  # a blank and a non-finite cell
    res = _resonance((0.4, 3.0), "flag")
    res[1] = {**res[1], "note": 5}  # a note that is not text
    _write(tmp_path, subsync=_sub_meta("flag"), uif_rows=uif, resonance=res)
    rows, _ = _card(tmp_path)
    state, t = _pack(_project(monkeypatch, tmp_path))

    assert state == "present" and list(t) == ["ssti_uif", "subsync_series_resonance"]
    assert t["ssti_uif"].columns == UIF_HEAD
    assert t["ssti_uif"].rows == [
        ["MV", "max", "GAS", "0.25", "", "warn", ""],
        ["MV", "min", "", "0.125", "—", "warn", ""],
    ]
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    assert t["ssti_uif"].caption.startswith(
        "Worst UIF 0.25 at bus MV (max fault level, machine GAS), above the assumed "
        "screening threshold 0.1: an EMT SSTI study is warranted (grid-connected state only)."
    )
    assert t["ssti_uif"].source_path == UIF_CSV
    assert t["subsync_series_resonance"].columns == RES_HEAD
    assert t["subsync_series_resonance"].rows == [
        ["max", "GAS", "30", "20.4", "20", "0.4", "warn", ""],
        ["max", "GAS", "30", "23", "20", "3", "pass", "an unreadable note"],
    ]
    assert (
        t["subsync_series_resonance"].caption
        == rows["subsync_series_resonance"]["note"]
    )
    assert t["subsync_series_resonance"].source_path == RES_CSV
    _no_repr(t)


def test_a_clear_uif_at_the_threshold_passes_each_bus(tmp_path, monkeypatch):
    # the scorecard's rule: equal to the threshold clears
    _write(tmp_path, uif=_uif_meta("clear", worst=0.1), uif_rows=_uif_table(0.1))
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert [r[5] for r in t["ssti_uif"].rows] == ["pass", "pass"]


def test_a_margin_at_the_clearance_passes(tmp_path, monkeypatch):
    # the scorecard's rule: a margin equal to the clearance clears
    _write(
        tmp_path, subsync=_sub_meta("clear"), resonance=_resonance((1.0, 3.0), "clear")
    )
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert [r[6] for r in t["subsync_series_resonance"].rows] == ["pass", "pass"]


def test_a_row_the_card_skips_is_not_judged_in_the_pack(tmp_path, monkeypatch):
    # GK A2 H1 (ruling (b)): a band row with no resonance and a row turned check carry no judged margin
    _write(
        tmp_path,
        subsync=_sub_meta("clear"),
        resonance=[*_resonance((3.0,), "clear"), _band_clear_row()],
    )
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert [r[6] for r in t["subsync_series_resonance"].rows] == ["pass", "not judged"]
    _write(
        tmp_path,
        subsync=_sub_meta("flag"),
        resonance=[*_resonance((0.4,)), _check_row()],
    )
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert [r[6] for r in t["subsync_series_resonance"].rows] == ["warn", "not judged"]


@pytest.mark.parametrize(
    "bad", ["bogus", float("nan"), 7], ids=["bogus", "blank", "seven"]
)
def test_an_unknown_row_verdict_judges_nothing_in_the_pack(tmp_path, monkeypatch, bad):
    other = {**_resonance((3.0,), "clear")[0], "verdict": bad}
    _write(
        tmp_path,
        subsync=_sub_meta("clear"),
        resonance=[*_resonance((3.0,), "clear"), other],
    )
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert [r[6] for r in t["subsync_series_resonance"].rows] == [
        "not judged",
        "not judged",
    ]
    _no_repr(t)


@pytest.mark.parametrize("damage", ["missing", "no_column"])
def test_a_missing_or_malformed_table_gives_empty_tables_with_the_reason(
    tmp_path, monkeypatch, damage
):
    from tests.test_grid_readiness_ssti import _run_dir

    _write(tmp_path, subsync=_sub_meta("flag"))
    for name in ("campus_ssti_uif.csv", "campus_subsync_resonance.csv"):
        path = _run_dir(tmp_path) / name
        if damage == "missing":
            path.unlink()
        else:
            path.write_text("other\n1\n")
    rows, _ = _card(tmp_path)
    _, t = _pack(_project(monkeypatch, tmp_path))
    for rid in ("ssti_uif", "subsync_series_resonance"):
        assert t[rid].rows == [] and t[rid].caption == rows[rid]["note"]


def test_numeric_ids_print_whole_as_the_caption_prints_them(tmp_path, monkeypatch):
    # GK A2 B2: the CSV reads a numeric id as a number (a float, with the PCC row's blank bus); it is a
    # name, never "1.1e+05"
    uif = _uif_table(bus=110012, machine=123456)
    uif[1] = {**uif[1], "machine": float("inf")}  # a non-finite name
    uif[2] = {**uif[2], "bus": None}
    _write(tmp_path, uif_rows=uif)
    rows, _ = _card(tmp_path)
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert "bus 110012" in t["ssti_uif"].caption
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    assert t["ssti_uif"].rows == [
        ["110012", "max", "123456", "0.25", "0.1", "warn", ""],
        ["110012", "min", "—", "0.125", "0.1", "warn", ""],
    ]


def test_a_uif_just_over_its_threshold_never_prints_equal_to_it(tmp_path, monkeypatch):
    # GK A2 B3: four figures would print 0.10004 against 0.1 as "0.1 | 0.1 | warn"
    _write(
        tmp_path,
        uif=_uif_meta("flag", worst=0.10004),
        uif_rows=_uif_table(0.10004, machine=" GAS "),  # a padded name
        subsync=_sub_meta("flag"),
        resonance=_resonance((0.99996, 3.0), "flag"),
    )
    rows, _ = _card(tmp_path)
    assert rows["ssti_uif"]["verdict"] == "warn"
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert t["ssti_uif"].rows == [
        ["MV", "max", "GAS", "0.10004", "0.1", "warn", ""],
        ["MV", "min", "GAS", "0.05002", "0.1", "pass", ""],
    ]
    # the least margin against the run's 1 Hz clearance, which the caption states
    assert [r[5:7] for r in t["subsync_series_resonance"].rows] == [
        ["0.99996", "warn"],
        ["3", "pass"],
    ]


@pytest.mark.parametrize(
    "value, limit, shown",
    [
        (0.1, 0.1, ("0.1", "0.1")),  # equal values print alike, never as 17 figures
        (0.100039, 0.10004, ("0.100039", "0.10004")),  # the limit gains figures too
        ("x", 0.1, ("—", "0.1")),
        (None, " ", ("", "")),
    ],
)
def test_a_value_and_its_limit_print_apart_only_when_they_differ(value, limit, shown):
    assert S._apart(value, limit) == shown


# ── S1: what the scorecard refused, the pack does not judge ───────────────────


def test_a_damaged_uif_table_states_the_scorecards_reason_and_judges_nothing(
    tmp_path, monkeypatch
):
    uif = _uif_table()
    uif[0] = {**uif[0], "uif": "x"}
    _write(tmp_path, uif_rows=uif)
    rows, _ = _card(tmp_path)
    assert rows["ssti_uif"]["verdict"] == "unknown"
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    assert "not a finite number (x)" in t["ssti_uif"].caption
    assert t["ssti_uif"].rows == [
        ["MV", "max", "GAS", "—", "0.1", "not judged", ""],
        ["MV", "min", "GAS", "0.125", "0.1", "not judged", ""],
    ]


def test_a_profile_moved_since_the_run_states_the_reason_and_judges_nothing(
    tmp_path, monkeypatch
):
    dirs = _profile_dir(
        tmp_path, ssti_uif_threshold={"value": 0.2, "clause": "c", "source": "code"}
    )
    _write(tmp_path, uif=_uif_meta("flag", threshold=0.1, source="code", clause="c"))
    rows, _ = _card(tmp_path, "gc08_card", dirs)
    assert rows["ssti_uif"]["verdict"] == "unknown"
    _, t = _pack(_project(monkeypatch, tmp_path, profile="gc08_card", dirs=dirs))
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    assert "changed since the run" in t["ssti_uif"].caption
    assert {r[5] for r in t["ssti_uif"].rows} == {"not judged"}


def test_an_undecided_scan_lists_its_rows_unjudged_with_the_engines_reason(
    tmp_path, monkeypatch
):
    _write(tmp_path)  # the scan's verdict: not evaluated
    rows, _ = _card(tmp_path)
    _, t = _pack(_project(monkeypatch, tmp_path))
    table = t["subsync_series_resonance"]
    assert table.caption == rows["subsync_series_resonance"]["note"]
    assert {r[6] for r in table.rows} == {"not judged"}


# ── S2: one off-grid rule ─────────────────────────────────────────────────────


def test_off_grid_with_an_open_tie_marks_each_bus_islanded_and_drops_the_scan(
    tmp_path, monkeypatch
):
    _template(tmp_path, "off_grid")
    _write(tmp_path, uif=_uif_meta("clear", worst=0.05), uif_rows=_uif_table(0.05))
    n = _open_tie(_site())
    rows, _ = _card(tmp_path, n=n)
    _, t = _pack(_project(monkeypatch, tmp_path, n=n))
    assert list(t) == ["ssti_uif"]
    assert t["ssti_uif"].caption == (
        "The screen counts the grid's infeed; the islanded case is not evaluated. "
        "Screening; the binding answer is the EMT study."
    )
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    # islanded, the grid-counted UIF is no answer: its cell reads "—" (GK A2 Q2)
    assert t["ssti_uif"].rows == [
        ["MV", "max", "GAS", "—", "0.1", "not evaluated (islanded)", ""],
        ["MV", "min", "GAS", "—", "0.1", "not evaluated (islanded)", ""],
    ]


def test_an_islanded_project_with_a_damaged_table_gives_the_damage_and_judges_nothing(
    tmp_path, monkeypatch
):
    # the scorecard refuses the table before the island rule: its caption is the damage, never a verdict
    _template(tmp_path, "off_grid")
    uif = _uif_table()
    uif[0] = {**uif[0], "uif": "x"}
    _write(tmp_path, uif_rows=uif)
    n = _open_tie(_site())
    rows, _ = _card(tmp_path, n=n)
    _, t = _pack(_project(monkeypatch, tmp_path, n=n))
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    assert "not a finite number (x)" in t["ssti_uif"].caption
    assert [r[3:6] for r in t["ssti_uif"].rows] == [
        ["—", "0.1", "not evaluated (islanded)"]
    ] * 2


def test_a_grid_project_with_an_open_tie_stays_judged_and_unmarked(
    tmp_path, monkeypatch
):
    _template(tmp_path, "strong_grid")
    _write(tmp_path)
    _, t = _pack(_project(monkeypatch, tmp_path, n=_open_tie(_site())))
    assert list(t) == ["ssti_uif", "subsync_series_resonance"]
    assert [r[5] for r in t["ssti_uif"].rows] == ["warn", "warn"]
    assert "islanded" not in t["ssti_uif"].caption


def test_off_grid_by_label_only_stays_judged_and_drops_the_scan(tmp_path, monkeypatch):
    _template(tmp_path, "off_grid")
    _write(tmp_path)
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert list(t) == ["ssti_uif"]
    assert [r[5] for r in t["ssti_uif"].rows] == ["warn", "warn"]
    assert "(grid-connected state only)" in t["ssti_uif"].caption


@pytest.mark.parametrize(
    "case, tables, verdicts, scorecard_uif",
    [
        ("islanded", ["ssti_uif"], ["not evaluated (islanded)"] * 2, "unknown"),
        ("off_grid", ["ssti_uif"], ["warn", "warn"], "warn"),
    ],
)
def test_the_scorecard_and_the_pack_follow_the_one_rule(
    tmp_path, monkeypatch, case, tables, verdicts, scorecard_uif
):
    # S2: a grid project the rule calls off-grid; both readers follow the helper, not their own test
    _template(tmp_path, "strong_grid")
    _write(tmp_path)
    monkeypatch.setattr(G, "grid_case", lambda inp: case)
    rows, out = _card(tmp_path)
    assert rows["ssti_uif"]["verdict"] == scorecard_uif
    assert {e["id"] for e in out["excluded"]} >= set(G.SUBSYNC_PCC_ROWS)
    _, t = _pack(_project(monkeypatch, tmp_path))
    assert list(t) == tables
    assert [r[5] for r in t["ssti_uif"].rows] == verdicts


@pytest.mark.parametrize(
    "template, n, case",
    [
        ("strong_grid", _site, "grid"),
        ("strong_grid", lambda: _open_tie(_site()), "grid"),
        ("off_grid", _site, "off_grid"),
        ("off_grid", lambda: _open_tie(_site()), "islanded"),
    ],
    ids=["grid", "grid_open_tie", "off_grid_label_only", "off_grid_open_tie"],
)
def test_the_grid_case_reads_the_site_type_and_the_tie(tmp_path, template, n, case):
    _template(tmp_path, template)
    inp = G._Inputs(tmp_path, n(), None, None, None, None)
    assert G.grid_case(inp) == case


# ── pending and unavailable (S5, GK A2 (a)) ───────────────────────────────────


def test_a_real_two_genset_stage_is_present_with_the_scorecards_worst_uif(
    tmp_path, monkeypatch
):
    _two_genset_run(tmp_path)
    rows, _ = _card(tmp_path)
    state, t = _pack(_project(monkeypatch, tmp_path))
    assert state == "present"
    worst = rows["ssti_uif"]["project_value"]
    assert worst is not None
    # the worst bus's UIF cell is the scorecard's project_value, as the pack prints numbers
    assert (
        max((r for r in t["ssti_uif"].rows), key=lambda r: float(r[3]))[3]
        == f"{worst:.4g}"
    )
    assert t["ssti_uif"].caption == rows["ssti_uif"]["note"]
    _no_repr(t)


def test_a_registered_project_before_the_stage_is_pending(hub):  # noqa: F811
    assert sources.read(hub, "GC-08") == ("pending", None)


def test_a_project_with_no_run_is_pending(tmp_path, monkeypatch):
    assert _pack(_project(monkeypatch, tmp_path)) == ("pending", {})


@pytest.mark.parametrize(
    "files, reason",
    [
        (
            {"error": "the PCC has no fault level"},
            "The SSTI screens could not run: the PCC has no fault level",
        ),
        (
            {"raw_meta": "["},
            "The SSTI screens' record is unreadable: rerun the grid study",
        ),
    ],
    ids=["failed_stage", "unreadable_meta"],
)
def test_a_failed_stage_or_unreadable_record_is_unavailable(
    tmp_path, monkeypatch, files, reason
):
    _write(tmp_path, **files)
    project = _project(monkeypatch, tmp_path)
    assert sources.read(project, "GC-08") == ("unavailable", None)
    with pytest.raises(S.SstiSourceUnavailable) as caught:
        S.gc08_tables(project)
    assert str(caught.value) == reason
