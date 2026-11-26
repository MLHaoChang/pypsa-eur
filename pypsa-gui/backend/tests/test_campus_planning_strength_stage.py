# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The campus run's planning-criteria and grid-strength stages (grid-compliance GC-03, TRGC 15).

After the SSTI screens the run screens the planning criteria over the campus N-1 and computes SCR, ESCR and
WSCR per PoC and stage. A failure is shown with the results, reads ``error`` and never fails the run; a
normally open tie reads "not evaluated"; a run from before the stages reads unknown on the scorecard.
"""

import json

import pandas as pd
import pytest
import yaml

from services import campus_electrical_service as ce
from services.adequacy import grid_readiness as G
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures
from tests.test_campus_scan_and_grid_code_since_run import SETTINGS, _publish
from tests.test_grid_readiness_motor_start import _site

STRENGTH_IDS = ("campus_scr", "campus_escr", "campus_wscr")


def _card(project, profile="pq_test"):
    d = ce.campus_dir(project).parent
    rows = G.grid_readiness(
        d, _site(), profile, profile_dirs=(ce.grid_codes_dir(project),)
    )["rows"]
    return {r["id"]: r for r in rows}


@pytest.fixture
def ran(hub):  # noqa: F811
    _publish(hub)
    stages = []
    ce.draft(hub)
    ce.run(
        hub,
        {**SETTINGS, "profile": "pq_test"},
        progress=lambda name, done, total: stages.append(name),
    )
    return hub, stages


def test_the_run_screens_planning_then_strength_after_the_ssti_screens(ran):
    hub, stages = ran  # noqa: F811 - the hub fixture's value
    assert stages.index("planning") == stages.index("ssti_screens") + 1
    assert stages.index("strength") == stages.index("planning") + 1
    run = ce.campus_dir(hub) / "run"
    for name in (
        ce.cs.PLANNING_CSV,
        ce.cs.STRENGTH_CSV,
        ce.cs.STRENGTH_GROUPS_CSV,
        ce.PLANNING_META,
        ce.STRENGTH_META,
    ):
        assert (run / name).is_file(), name
    assert (
        not (run / ce.PLANNING_ERROR).exists()
        and not (run / ce.STRENGTH_ERROR).exists()
    )

    res = ce.get_state(hub)["results"]
    planning, strength = res["planning"], res["strength"]
    assert planning["verdict"] in ce.cs.PLANNING_VERDICTS
    assert planning["cases"] == len(planning["rows"]) > 0
    assert strength["basis"] == "static screen; binding answer is the RMS/EMT study"
    assert strength["rows"] and strength["groups"]
    # the worst outage never reads stronger than the intact grid, per PoC and stage
    for r in strength["rows"]:
        if r["scr_worst"] is not None and r["scr_intact"] is not None:
            assert r["scr_worst"] <= r["scr_intact"] + 1e-12, r

    card = _card(hub)
    assert card[G.PLANNING_ID]["verdict"] == G.planning_verdict(planning["verdict"])
    # the shipped profile states no strength minimum: each ratio is shown, never judged
    for rid in STRENGTH_IDS:
        row = card[rid]
        assert row["verdict"] == "unknown" and row["limit"] is None
        assert row["note"].count(G.STRENGTH_BASIS) == 1


def test_a_scr_minimum_in_the_profile_judges_the_campus_scr_row_only(ran):
    hub, _ = ran  # noqa: F811 - the hub fixture's value
    _publish(hub, scr_min={"value": 3.0, "clause": "demo clause 1.2", "source": "code"})
    card = _card(hub)
    row = card["campus_scr"]
    assert (
        row["limit"] == 3.0
        and row["source"].endswith("demo clause 1.2")
        and row["assumed"] is False
    )
    assert (
        row["verdict"]
        == G._judge(row["project_value"], 3.0, G.RULE_STRENGTH)
        != "unknown"
    )
    # ESCR and WSCR are never judged on the SCR minimum
    assert card["campus_escr"]["verdict"] == card["campus_wscr"]["verdict"] == "unknown"
    assert card["campus_escr"]["limit"] is None


@pytest.mark.parametrize(
    "step, call, files, meta, error",
    [
        (
            "planning",
            "planning_campus",
            ("PLANNING_CSV",),
            "PLANNING_META",
            "PLANNING_ERROR",
        ),
        (
            "strength",
            "strength_campus",
            ("STRENGTH_CSV", "STRENGTH_GROUPS_CSV"),
            "STRENGTH_META",
            "STRENGTH_ERROR",
        ),
    ],
)
def test_a_screen_error_reads_error_never_fails_the_run_and_clears_on_success(
    ran, monkeypatch, step, call, files, meta, error
):
    hub, _ = ran  # noqa: F811 - the hub fixture's value
    run = ce.campus_dir(hub) / "run"
    real = getattr(ce.cs, call)

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)  # it wrote its CSVs, then fails: none of them may stay
        raise RuntimeError("demo failure")

    monkeypatch.setattr(ce.cs, call, broken)
    ce.run(hub, {**SETTINGS, "profile": "pq_test"})
    run = ce.campus_dir(hub) / "run"
    assert json.loads((run / getattr(ce, error)).read_text()) == {
        "error": "demo failure"
    }
    for f in files:
        assert not (run / getattr(ce.cs, f)).exists()
    assert not (run / getattr(ce, meta)).exists()
    assert ce.get_state(hub)["results"][step] == {
        "error": "demo failure",
        "verdict": "error",
    }
    ids = (G.PLANNING_ID,) if step == "planning" else STRENGTH_IDS
    card = _card(hub)
    for rid in ids:
        assert (
            card[rid]["verdict"] == "unknown"
            and "could not run: demo failure" in card[rid]["note"]
        )

    monkeypatch.setattr(ce.cs, call, real)
    ce.run(hub, {**SETTINGS, "profile": "pq_test"})
    run = ce.campus_dir(hub) / "run"
    assert (
        not (run / getattr(ce, error)).exists() and (run / getattr(ce, meta)).is_file()
    )


def test_an_open_tie_reads_not_evaluated_and_an_exception_still_writes_the_error(
    ran, monkeypatch
):
    hub, _ = ran  # noqa: F811 - the hub fixture's value
    run = ce.campus_dir(hub) / "run"
    spec = yaml.safe_load((run / ce.cs.CAMPUS_YAML).read_text())
    spec["campus"]["pcc"]["tie_normally_open"] = True
    (run / ce.cs.CAMPUS_YAML).write_text(yaml.safe_dump(spec))
    ce._strength(run, "pq_test", (ce.grid_codes_dir(hub),))
    assert (
        json.loads((run / ce.STRENGTH_META).read_text())["verdict"]
        == ce.STRENGTH_NOT_EVALUATED
    )
    assert (
        not (run / ce.cs.STRENGTH_CSV).exists()
        and not (run / ce.STRENGTH_ERROR).exists()
    )
    for rid in STRENGTH_IDS:
        row = _card(hub)[rid]
        assert row["verdict"] == "unknown"
        assert row["note"].startswith(
            ce.STRENGTH_OPEN_TIE + ". Strength is not evaluated."
        )

    def boom(*a, **kw):
        raise RuntimeError("demo failure")

    monkeypatch.setattr(ce, "_tie_open", boom)
    ce._strength(run, "pq_test", ())
    assert json.loads((run / ce.STRENGTH_ERROR).read_text()) == {
        "error": "demo failure"
    }
    assert not (run / ce.STRENGTH_META).exists()


def test_a_run_from_before_the_stages_reads_unknown_and_asks_for_a_rerun(ran):
    hub, _ = ran  # noqa: F811 - the hub fixture's value
    run = ce.campus_dir(hub) / "run"
    for name in (
        ce.PLANNING_META,
        ce.STRENGTH_META,
        ce.cs.PLANNING_CSV,
        ce.cs.STRENGTH_CSV,
        ce.cs.STRENGTH_GROUPS_CSV,
    ):
        (run / name).unlink()
    res = ce.get_state(hub)["results"]
    assert res["planning"] is None and res["strength"] is None
    card = _card(hub)
    for rid in (G.PLANNING_ID, *STRENGTH_IDS):
        assert card[rid]["verdict"] == "unknown"
        assert (
            "is from before the" in card[rid]["note"]
            and "rerun the campus study" in card[rid]["note"]
        )


def test_the_campus_scr_row_matches_the_run_table(ran):
    """The row shows the least worst-outage SCR the run wrote over the PoCs and stages with a converter; an
    outage that islands a PoC (the fixture's one transformer) counts as 0 and is named."""
    hub, _ = ran  # noqa: F811 - the hub fixture's value
    table = pd.read_csv(ce.campus_dir(hub) / "run" / ce.cs.STRENGTH_CSV)
    live = table[table["p_mw"] > 0]
    islands = live[live["islanding_outages"].astype(str) != "[]"]
    row = _card(hub)["campus_scr"]
    if len(islands):
        first = sorted(G._islanding(islands["islanding_outages"].iloc[0]))[0]
        assert (
            row["project_value"] == 0.0
            and f"outage {first} islands it from the grid" in row["note"]
        )
    else:
        assert row["project_value"] == pytest.approx(float(live["scr_worst"].min()))


# ── the wrappers on their own (no run) ──────────────────────────────────────


@pytest.mark.parametrize(
    "spec",
    [None, {}, {"campus": None}, {"campus": {"pcc": None}}, {"campus": {"pcc": {}}}],
)
def test_a_spec_with_no_pcc_has_no_open_tie(monkeypatch, tmp_path, spec):
    monkeypatch.setattr(ce.cs, "_run_campus_spec", lambda run: spec)
    assert ce._tie_open(tmp_path) is False


def test_a_screen_with_no_table_reads_an_empty_list(tmp_path):
    (tmp_path / ce.PLANNING_META).write_text(json.dumps({"verdict": "pass"}))
    assert ce._gc03_results(
        tmp_path, ce.PLANNING_META, ce.PLANNING_ERROR, {"rows": "absent.csv"}
    ) == {"verdict": "pass", "rows": []}


def test_a_screen_failure_is_written_named_and_logged_unless_it_is_a_contract_refusal(
    tmp_path, caplog
):
    from gridspine.schema.contracts import ContractError

    def boom(exc):
        def call():
            raise exc

        return call

    files = ("out.csv", "meta.json", "err.json")
    with caplog.at_level("ERROR"):
        ce._gc03_screen(
            tmp_path, files, "demo", boom(RuntimeError()), "err.json", "meta.json"
        )
    assert json.loads((tmp_path / "err.json").read_text()) == {"error": "RuntimeError"}
    assert "the demo screen failed" in caplog.text
    caplog.clear()
    with caplog.at_level("ERROR"):
        ce._gc03_screen(
            tmp_path,
            files,
            "demo",
            boom(ContractError("demo refusal")),
            "err.json",
            "meta.json",
        )
    assert json.loads((tmp_path / "err.json").read_text()) == {"error": "demo refusal"}
    assert (
        "the demo screen failed" not in caplog.text
        and not (tmp_path / "meta.json").exists()
    )
