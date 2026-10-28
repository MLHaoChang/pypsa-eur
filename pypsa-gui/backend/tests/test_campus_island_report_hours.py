"""
I9b of the campus island plan: the island report reads the island-hours
results (I4, I5, I5b and I6) of the campus run.

The drivers write them beside the ranked run (``gridspine.drivers.
campus_study``): ``island_static_campus`` the ``ISLAND_STATIC_CSV`` tables
and ``campus_island_summary.json``, ``island_frequency_campus`` the
``campus_freq_*`` tables and ``campus_freq_summary.json``. The report shows
each as a table under the plan's rules (I9): the screening caveat, the model
behind it (the summary's ``models``, the frequency ``label``), and the rows
that rest on an ``assumed`` input marked (the static tables' ``assumed``
column). What the run does not hold is listed in ``missing``; a file whose
columns are not the contract's is listed there too, never shown.

The files here are written by hand, to the contracts' column tuples, so
each expectation is exact.
"""
import json

import pandas as pd
import pytest

from gridspine.drivers import campus_study as cs
from gridspine.static import frequency as F
from gridspine.static import island_hours as IH
from services import campus_electrical_service as ce
from services.campus_island_report import CAVEAT, island_report
from tests.test_campus_island_service import _config, project, user_and_db  # noqa: F401

STATIC_MISSING = "island steady state and short circuit at the island hours"
FREQ_MISSING = "island frequency check at the island hours"


def _tables(report):
    return {t["id"]: t for t in report["tables"]}


def _run_dir(project):
    run = ce.campus_dir(project) / "run"
    run.mkdir(parents=True, exist_ok=True)
    return run


def _write(run, fname, columns, rows=()):
    pd.DataFrame([dict(zip(columns, r)) for r in rows], columns=list(columns)).to_csv(run / fname, index=False)


def _row(columns, **values):
    return tuple(values.get(c) for c in columns)


def _static(run, flow_rows=(), relay_rows=(), summary=None):
    """Every static table, header only unless given rows, and the summary."""
    for name, columns in IH.TABLES:
        rows = {"flow": flow_rows, "relays": relay_rows}.get(name, ())
        _write(run, cs.ISLAND_STATIC_CSV[name], columns, rows)
    s = {"label": IH.LABEL, "ledger": ["sc: a ledger line", IH.CASE_NOTE],
         "models": {cs.ISLAND_STATIC_CSV[n]: IH.MODELS[n] for n, _ in IH.TABLES},
         "island_hours": [[0, 3]], "not_viable_hours": [], "gate_failing_hours": [],
         "blind_relay_hours": [[0, 3, "islanding", "R1"]], "blind_earth_relay_hours": [],
         "earth_lost_hours": [], "not_determinable_hours": []}
    s.update(summary or {})
    (run / cs.ISLAND_STATIC_SUMMARY_JSON).write_text(json.dumps(s))
    return s


_FREQ = ((cs.FREQ_E1_HOURLY_CSV, F.E1_HOURLY_COLUMNS), (cs.FREQ_EVENTS_CSV, F.EVENT_COLUMNS),
         (cs.FREQ_LOSSES_CSV, F.LOSSES_COLUMNS), (cs.FREQ_BRIDGE_CSV, F.BRIDGE_COLUMNS),
         (cs.FREQ_PLANNED_CSV, F.PLANNED_COLUMNS), (cs.FREQ_UPS_CSV, F.UPS_COLUMNS))


def _freq(run, e1_rows=(), event_rows=(), summary=None):
    for fname, columns in _FREQ:
        rows = {cs.FREQ_E1_HOURLY_CSV: e1_rows, cs.FREQ_EVENTS_CSV: event_rows}.get(fname, ())
        _write(run, fname, columns, rows)
    s = {"label": F.LABEL, "ledger": ["frequency: a ledger line"], "scenarios": ["seamless"],
         "island_hours": [[0, 3]], "every_hour_e1": True, "failing_hours": [[0, 3]],
         "bridge_failing_hours": [], "planned_infeasible_hours": [], "e1_failing_hours": [[0, 3], [0, 7]],
         "e1_failing_not_selected": [[0, 7]]}
    s.update(summary or {})
    (run / cs.FREQ_SUMMARY_JSON).write_text(json.dumps(s))
    return s


@pytest.fixture
def saved(project):  # noqa: F811
    ce.save_island(project, _config())
    return project


def test_without_the_island_hours_files_the_report_says_so(saved):
    report = island_report(saved)
    assert STATIC_MISSING in report["missing"]
    assert FREQ_MISSING in report["missing"]
    assert not [t for t in report["tables"] if t["id"].startswith(("static_", "freq_"))]


def test_every_static_table_is_shown_with_the_contract_columns_and_its_model(saved):
    _static(_run_dir(saved))
    tables = _tables(island_report(saved))
    for name, columns in IH.TABLES:
        t = tables[f"static_{name}"]
        assert t["columns"] == list(columns), name
        assert t["model"] == IH.MODELS[name], name
        assert t["caveat"] == CAVEAT
        assert t["group"] == "static", name
        assert t["rows"] == [] and t["assumed_rows"] == []
    assert STATIC_MISSING not in island_report(saved)["missing"]


def test_a_static_row_resting_on_an_assumed_input_is_marked(saved):
    cols = IH.FLOW_COLUMNS
    _static(_run_dir(saved), flow_rows=[
        _row(cols, period=0, hour=3, viable=True, reason="", df_hz=-0.1, gate_ok=True,
             assumed="g.droop_pct; bess.i_max_pu"),
        _row(cols, period=0, hour=5, viable=False, reason="no grid-forming unit", gate_ok=True, assumed=""),
    ])
    flow = _tables(island_report(saved))["static_flow"]
    assert flow["assumed_rows"] == [0]
    at = {c: i for i, c in enumerate(flow["columns"])}
    assert flow["rows"][0][at["assumed"]] == "g.droop_pct; bess.i_max_pu"
    assert flow["rows"][1][at["viable"]] is False
    assert flow["rows"][1][at["reason"]] == "no grid-forming unit"
    assert flow["rows"][1][at["df_hz"]] is None             # an empty cell reads as None, not NaN


def test_a_file_off_the_contract_is_listed_missing_and_not_shown(saved):
    run = _run_dir(saved)
    _static(run)
    _write(run, cs.ISLAND_STATIC_CSV["relays"], ("period", "hour", "relay"), [(0, 3, "R1")])
    report = island_report(saved)
    assert "static_relays" not in _tables(report)
    assert f"{cs.ISLAND_STATIC_CSV['relays']} (columns differ from the contract)" in report["missing"]
    assert "static_flow" in _tables(report)


@pytest.mark.parametrize("fname, columns, table", [
    (cs.FREQ_EVENTS_CSV, F.EVENT_COLUMNS, "freq_events"),
    (cs.FREQ_E1_HOURLY_CSV, F.E1_HOURLY_COLUMNS, "freq_e1_hourly"),
])
def test_a_frequency_table_written_before_trip_s_still_shows_with_it_empty(saved, fname, columns, table):
    # a run made before the trip floor (#269) has no trip_s, which sat next to collapse_s mid-header: it reads as
    # empty, and the report says the run predates the floor so an empty trip_s is not read as "no trip"
    run = _run_dir(saved)
    _freq(run)
    old = [c for c in columns if c != "trip_s"]
    assert old.index("collapse_s") + 1 < len(old)                      # trip_s was in the middle, not at the end
    _write(run, fname, old, [_row(old, period=0, hour=3, scenario="seamless", event="E1", ok=False)])
    report = island_report(saved)
    shown = _tables(report)[table]
    assert shown["columns"] == list(columns) and not any("columns differ" in m for m in report["missing"])
    assert shown["rows"][0][list(columns).index("trip_s")] is None
    assert f"{fname} written before the trip floor: re-run for trip times" in report["missing"]
    # a current file carries no such note, and any other difference is still off the contract
    _freq(run)
    assert not any("trip floor" in m for m in island_report(saved)["missing"])
    _write(run, fname, old[:-1], [])
    assert f"{fname} (columns differ from the contract)" in island_report(saved)["missing"]


def test_the_static_summary_names_what_fails_and_carries_the_ledger_and_case_note(saved):
    _static(_run_dir(saved), summary={"not_viable_hours": [[0, 5, "no grid-forming unit"]]})
    t = _tables(island_report(saved))["static_summary"]
    assert t["model"] == IH.LABEL
    assert t["columns"] == ["item", "value"]
    rows = {r[0]: r[1] for r in t["rows"] if r[0] != "ledger"}
    assert rows["island_hours"] == "p0 h3"
    assert rows["not_viable_hours"] == "p0 h5 no grid-forming unit"
    assert rows["blind_relay_hours"] == "p0 h3 islanding R1"
    assert rows["gate_failing_hours"] == "none"
    assert "models" not in rows and "label" not in rows
    ledger = [r[1] for r in t["rows"] if r[0] == "ledger"]
    assert ledger == ["sc: a ledger line", IH.CASE_NOTE]


def test_every_frequency_table_is_shown_with_the_frequency_model(saved):
    _freq(_run_dir(saved))
    report = island_report(saved)
    tables = _tables(report)
    for fname, columns in _FREQ:
        tid = "freq_" + fname.removeprefix("campus_freq_").removesuffix(".csv")
        t = tables[tid]
        assert t["columns"] == list(columns), fname
        assert t["model"] == F.LABEL, fname
        assert t["caveat"] == CAVEAT
        assert t["group"] == "frequency", fname
    assert FREQ_MISSING not in report["missing"]


def test_the_every_hour_e1_table_holds_its_failing_rows_only(saved):
    cols = F.E1_HOURLY_COLUMNS
    _freq(_run_dir(saved), e1_rows=[
        _row(cols, period=0, hour=h, scenario="seamless", event="E1", direction="up", ok=ok, nadir_hz=nadir)
        for h, ok, nadir in ((1, True, 49.6), (3, False, 47.1), (7, False, 47.3), (9, True, 49.8))])
    t = _tables(island_report(saved))["freq_e1_hourly"]
    at = {c: i for i, c in enumerate(t["columns"])}
    assert [r[at["hour"]] for r in t["rows"]] == [3, 7]
    assert all(r[at["ok"]] is False for r in t["rows"])
    assert "2 of 4" in t["title"]


def test_the_every_hour_e1_table_shows_the_worst_failing_rows_of_a_year(saved, monkeypatch):
    import services.campus_island_report as R

    monkeypatch.setattr(R, "E1_ROWS_SHOWN", 3)
    cols = F.E1_HOURLY_COLUMNS
    worst = {1: 0.1, 3: 0.5, 5: 0.2, 7: float("inf"), 9: 0.3, 11: 0.05}
    _freq(_run_dir(saved), e1_rows=[
        _row(cols, period=0, hour=h, scenario="seamless", event="E1", direction="up", ok=h % 2 == 0, nadir_hz=47.0,
             worst=worst.get(h, 0.0))
        for h in range(12)])
    t = _tables(island_report(saved))["freq_e1_hourly"]
    at = {c: i for i, c in enumerate(t["columns"])}
    assert [r[at["hour"]] for r in t["rows"]] == [7, 3, 9]
    assert "6 of 12 rows; the 3 worst by exceedance shown, all of them in the run's campus_freq_e1_hourly.csv" \
        in t["title"]


def test_the_frequency_summary_names_what_fails(saved):
    _freq(_run_dir(saved))
    t = _tables(island_report(saved))["freq_summary"]
    assert t["model"] == F.LABEL
    rows = {r[0]: r[1] for r in t["rows"] if r[0] != "ledger"}
    assert rows["e1_failing_not_selected"] == "p0 h7"
    assert rows["e1_failing_hours"] == "p0 h3; p0 h7"
    assert rows["scenarios"] == "seamless"
    assert rows["every_hour_e1"] is True
    assert [r[1] for r in t["rows"] if r[0] == "ledger"] == ["frequency: a ledger line"]


def test_every_table_names_its_group_in_the_plan_order(saved):
    from gridspine.ranking.campus import ISLAND_CRITERIA

    run = _run_dir(saved)
    _static(run)
    _freq(run)
    reason = ISLAND_CRITERIA[0][0]
    pd.DataFrame({"period": [0], "hour": [3], "reasons": [reason]}).to_csv(run / cs.SELECTED_CSV, index=False)
    tables = island_report(saved)["tables"]
    groups = [t["group"] for t in tables]
    order = ["inputs", "study", "hours", "static", "frequency"]
    assert [order.index(g) for g in groups] == sorted(order.index(g) for g in groups)
    assert {t["id"]: t["group"] for t in tables}["island_hours"] == "hours"
    assert {"inputs", "hours", "static", "frequency"} <= set(groups)


def test_an_infinite_value_reads_as_inf_text_so_the_payload_is_strict_json(saved):
    """A bridge with no end to its autonomy writes ``inf``; the route's JSON
    allows no Infinity, so the report says ``inf`` as text."""
    run = _run_dir(saved)
    _freq(run)
    cols = F.BRIDGE_COLUMNS
    _write(run, cs.FREQ_BRIDGE_CSV, cols, [_row(cols, period=0, hour=3, scenario="seamless", unit="ups",
                                                    kind="ups", ok=True, autonomy_s=float("inf"),
                                                    needed_s=-float("inf"))])
    report = island_report(saved)
    json.dumps(report, allow_nan=False)                    # what the route does
    t = _tables(report)["freq_bridge"]
    at = {c: i for i, c in enumerate(t["columns"])}
    assert t["rows"][0][at["autonomy_s"]] == "inf"
    assert t["rows"][0][at["needed_s"]] == "-inf"


def test_no_title_model_or_missing_item_carries_an_internal_plan_tag(saved):
    import re

    run = _run_dir(saved)
    _static(run)
    _freq(run)
    report = island_report(saved)
    shown = [t["title"] for t in report["tables"]] + [t["model"] for t in report["tables"]] + report["missing"]
    assert not [s for s in shown if re.search(r"\((I\d|Q\d)", s)]
