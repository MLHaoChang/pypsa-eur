"""
Results read an assumed rating as no stated rating (psse-import 05).

Imported PSS/E case14 has no unrated line, so the marker is set by hand on one
line of the imported network; every view must label that row and leave it out of
its overload, at-limit and congested counts, and read the same as before without
the marker.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from gridspine.ingest.psse_import import read_psse
from harness import branch_calculations
from services import assumed_rating, run_grid_pf
from services.asset_results import compute as C
from services.asset_schema import provenance
from services.checked_charts import series_table

CASE14 = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "gridspine"
    / "fixtures"
    / "psse"
    / "case14.raw"
)
SNAPSHOTS = pd.date_range("2030-01-01", periods=3, freq="h")


def _imported():
    n = read_psse([CASE14], {}).network
    n.set_snapshots(SNAPSHOTS)
    return n


def _overloaded(n, name):
    """Every snapshot of one line at 150 % of its rating (P only; Q is zero)."""
    rating = float(n.lines.at[name, "s_nom"])
    for t, p in (("p0", 1.5 * rating), ("p1", -1.5 * rating), ("q0", 0.0), ("q1", 0.0)):
        frame = getattr(n.lines_t, t)
        for line in n.lines.index:
            frame[line] = p if line == name else 0.0
    return rating


def _evidence(n):
    frames = {
        f"lines_t.{t}": getattr(n.lines_t, t).copy() for t in ("p0", "p1", "q0", "q1")
    }
    for t in ("p0", "p1", "q0", "q1"):  # the transformers carry no flow
        frames[f"transformers_t.{t}"] = pd.DataFrame(
            0.0, index=n.snapshots, columns=n.transformers.index
        )
    return run_grid_pf.Evidence(
        converged=np.ones(len(n.snapshots), bool), unknown=0, failed=0, frames=frames
    )


def test_the_imported_case_marks_which_ratings_were_assumed():
    n = _imported()
    assert "s_nom_source" in n.lines.columns
    rows = n.lines.iloc[0]
    assert assumed_rating.is_assumed(rows) == (rows["s_nom_source"] == "assumed")


def test_is_assumed_reads_only_the_exact_marker():
    assert assumed_rating.is_assumed({"s_nom_source": "assumed"})
    for stated in ("file", "", None, float("nan")):
        assert not assumed_rating.is_assumed({"s_nom_source": stated})
    assert not assumed_rating.is_assumed({})  # no column
    assert not assumed_rating.is_assumed(
        {"s_nom_source": "assumed", "s_nom_extendable": True}
    )
    assert assumed_rating.is_assumed({"p_nom_source": "assumed"}, "p_nom")


# ── AC power flow ────────────────────────────────────────────────────────


def _zero_flows(n):
    for t in ("p0", "p1", "q0", "q1"):
        setattr(n.lines_t, t, pd.DataFrame(0.0, index=SNAPSHOTS, columns=n.lines.index))


def _grid_loading(n):
    ctx = SimpleNamespace(network=n)
    return run_grid_pf.loading(ctx, _evidence(n))


def test_ac_pf_overload_rule_skips_an_assumed_branch():
    n = _imported()
    name = n.lines.index[0]
    _overloaded(n, name)
    n.lines["s_nom_source"] = "file"
    peak, over = _grid_loading(n)
    assert any(f"Line {name} " in o for o in over)  # a stated rating at 150 % is over
    n.lines.loc[name, "s_nom_source"] = "assumed"
    peak2, over2 = _grid_loading(n)
    assert not any(f"Line {name} " in o for o in over2)
    assert peak2 < 150.0


def test_ac_pf_with_only_assumed_branches_has_no_stated_rating_to_judge():
    n = _imported()
    n.transformers.drop(n.transformers.index, inplace=True)
    n.lines["s_nom_source"] = "assumed"
    for t in ("p0", "p1", "q0", "q1"):
        setattr(n.lines_t, t, pd.DataFrame(0.0, index=SNAPSHOTS, columns=n.lines.index))
    out = _grid_loading(n)
    assert isinstance(out, run_grid_pf.Missing) and out.code == "rating_assumed"


def test_ac_pf_without_the_marker_is_unchanged():
    n = _imported()
    name = n.lines.index[0]
    _overloaded(n, name)
    n.lines["s_nom_source"] = "file"
    with_file = _grid_loading(n)
    n.lines.drop(columns="s_nom_source", inplace=True)
    assert _grid_loading(n) == with_file


# ── per-asset results ────────────────────────────────────────────────────


def _ctx(n, name):
    return C.build_ctx(n, "Line", name, source="lopf", sns=n.snapshots)


def test_asset_results_label_the_assumed_branch_and_count_no_congested_hour(
    monkeypatch,
):
    n = _imported()
    name = n.lines.index[0]
    _overloaded(n, name)
    n.lines["s_nom_opt"] = n.lines["s_nom"]
    n.lines["s_nom_source"] = "file"
    monkeypatch.setattr(
        C, "series_for", lambda ctx, attr: getattr(n.lines_t, attr)[name]
    )
    stated = C.br_congested_hours(_ctx(n, name))
    assert stated > 0 and C.rating_assumed_flags(_ctx(n, name)) == {}
    n.lines.loc[name, "s_nom_source"] = "assumed"
    ctx = _ctx(n, name)
    assert C.br_congested_hours(ctx) is None  # not evaluated, never a 0 h pass
    assert float(C.br_loading(ctx).max()) == pytest.approx(150.0)  # the loading % stays
    assert C.rating_assumed_flags(ctx) == {
        "rating_assumed": True,
        "rating_label": "rating assumed",
    }


# ── checked branch-loading chart ─────────────────────────────────────────


def _chart_series(n, name):
    raw = {
        "index": [t.isoformat() for t in n.snapshots],
        "energy_weight": [1.0] * len(n.snapshots),
        "branches": {
            f"Line:{name}": {
                "class": "Line",
                "name": name,
                **branch_calculations._branch(n.components["Line"], name, n.snapshots),
            }
        },
    }
    record = {
        "request_scope": {"profile": "branch-loading/1", "entity_id": name},
        "raw": raw,
    }
    return raw, series_table(record)[1]["loading"]


def test_the_branch_loading_chart_marks_an_assumed_rating():
    n = _imported()
    name = n.lines.index[0]
    _overloaded(n, name)
    n.lines["s_nom_source"] = "file"
    raw, (unit, label, _) = _chart_series(n, name)
    assert (
        "rating_assumed" not in raw["branches"][f"Line:{name}"]
        and "assumed" not in label
    )
    n.lines.loc[name, "s_nom_source"] = "assumed"
    raw, (unit, label, values) = _chart_series(n, name)
    assert raw["branches"][f"Line:{name}"]["rating_assumed"] is True
    assert label.endswith("(rating assumed)") and values[0] == pytest.approx(150.0)


# ── entering a rating ────────────────────────────────────────────────────


def test_a_rating_the_person_sets_reads_as_stated():
    n = _imported()
    n.lines["s_nom_source"] = "assumed"
    a, b = n.lines.index[:2]
    provenance.mark_customised(n, "Line", a, ["s_nom"])
    provenance.mark_customised(n, "Line", b, ["x"])  # another field leaves it assumed
    assert n.lines.at[a, "s_nom_source"] == "file"
    assert n.lines.at[b, "s_nom_source"] == "assumed"
    assert not assumed_rating.is_assumed(n.lines.loc[a])


def test_setting_the_rating_through_the_edit_route_reads_as_stated_on_the_next_run(
    monkeypatch,
):
    from services import network_crud
    from services.pypsa_service import PyPSAService

    n = _imported()
    name = n.lines.index[0]
    n.lines["s_nom_source"] = "assumed"
    monkeypatch.setattr(PyPSAService, "get_network", classmethod(lambda cls: n))
    monkeypatch.setattr(network_crud, "_refuse_edit_during_live_study", lambda: None)
    network_crud._update_component("Line", "lines", name, {"s_nom": 321.0})
    assert n.lines.at[name, "s_nom"] == 321.0
    assert n.lines.at[name, "s_nom_source"] == "file"
    _overloaded(n, name)
    assert any(
        name in o for o in _grid_loading(n)[1]
    )  # the next run reads it as a real rating


def test_a_stated_line_with_no_congestion_still_reads_zero_hours(monkeypatch):
    n = _imported()
    name = n.lines.index[0]
    n.lines["s_nom_opt"] = n.lines["s_nom"]
    n.lines["s_nom_source"] = "file"
    idle = pd.Series(0.0, index=n.snapshots)
    monkeypatch.setattr(C, "series_for", lambda ctx, attr: idle)
    assert C.br_congested_hours(_ctx(n, name)) == 0.0


def test_build_response_carries_the_assumed_flags(monkeypatch):
    from services.asset_results import service

    n = _imported()
    name = n.lines.index[0]
    n.lines["s_nom_source"] = "file"
    n.lines.loc[name, "s_nom_source"] = "assumed"
    monkeypatch.setattr("routers.simulation._state_snapshot", lambda: {})

    def respond(line):
        return service.build_response(
            n,
            "Line",
            line,
            category="loadflow",
            metric_ids=["congested_hours"],
            source="lopf",
            from_iso=None,
            to_iso=None,
            period=None,
            mode="raw",
        )

    out = respond(name)
    assert (
        out["asset"]["rating_assumed"] is True
        and out["asset"]["rating_label"] == "rating assumed"
    )
    other = respond(n.lines.index[1])
    assert "rating_assumed" not in other["asset"]


# ── the AC power flow names what it did not judge ────────────────────────


def test_the_ac_pf_gate_names_the_assumed_branches_and_their_total():
    from services import run_gates

    n = _imported()
    n.lines["s_nom_source"] = "file"
    n.lines.loc[n.lines.index[0], "s_nom_source"] = "assumed"
    _zero_flows(n)
    assert run_grid_pf.assumed_branches(n) == [f"Line {n.lines.index[0]}"]
    detailed = run_gates._grid_loading(SimpleNamespace(network=n), _evidence(n))
    assert (
        f"Line {n.lines.index[0]}" in detailed.basis
        and "1 with an assumed rating" in detailed.basis
    )
    assert "of any line" not in detailed.basis


def test_the_assumed_list_carries_the_true_total_past_the_cap():
    n = _imported()
    n.transformers.drop(n.transformers.index, inplace=True)
    n.lines["s_nom_source"] = "assumed"
    for t in ("p0", "p1", "q0", "q1"):
        setattr(n.lines_t, t, pd.DataFrame(0.0, index=SNAPSHOTS, columns=n.lines.index))
    out = _grid_loading(n)
    assert f"({len(n.lines)} in all)" in out.reason and len(n.lines) > 5


def test_the_unrated_list_carries_the_true_total_past_the_cap():
    n = _imported()
    n.lines["s_nom_source"] = "file"
    n.lines["s_nom"] = 0.0
    _zero_flows(n)
    out = _grid_loading(n)
    assert out.code == "branch_unrated" and f"({len(n.lines)} in all)" in out.reason


def test_the_summary_headline_says_why_an_assumed_line_has_no_congested_hours(
    monkeypatch,
):
    from services.asset_results import service

    n = _imported()
    name = n.lines.index[0]
    n.lines["s_nom_opt"] = n.lines["s_nom"]
    n.lines["s_nom_source"] = "file"
    _overloaded(n, name)
    monkeypatch.setattr("routers.simulation._state_snapshot", lambda: {})
    monkeypatch.setattr(
        C, "series_for", lambda ctx, attr: getattr(n.lines_t, attr)[ctx.name]
    )
    monkeypatch.setattr(C, "_dispatch_status", lambda net: "fresh")
    monkeypatch.setattr(type(n), "is_solved", True, raising=False)

    def headline(assumed):
        n.lines.loc[name, "s_nom_source"] = "assumed" if assumed else "file"
        out = service.build_response(
            n,
            "Line",
            name,
            category="summary",
            metric_ids=[],
            source="lopf",
            from_iso=None,
            to_iso=None,
            period=None,
            mode="raw",
        )
        return {h["id"]: h for h in out["headline"]}["congested_hours"]

    row = headline(True)
    assert (row["status"], row["reason"]) == (
        "blocked",
        "rating assumed: not evaluated",
    )
    stated = headline(False)
    assert stated["status"] == "ok" and stated["value"] > 0


# --- psse-import 06: sealed facts and the what-if comparison ---------------------------


def _raw(*, assumed_second):
    """Two lines over three snapshots: both at 150 % of a rating of 10 MVA."""

    def branch(assumed):
        flow = [15.0, 15.0, 15.0]
        return {
            "class": "Line",
            "rating": 10.0,
            "rating_field": "s_nom",
            "s_max_pu": [1.0] * 3,
            "p0": flow,
            "p1": [-f for f in flow],
            **({"rating_assumed": True} if assumed else {}),
        }

    return {
        "version": 1,
        "profile": branch_calculations.NETWORK_PROFILE,
        "entity_id": "network",
        "valid_solve": True,
        "index": ["a", "b", "c"],
        "energy_weight": [1.0, 1.0, 1.0],
        "branches": {"L1": branch(False), "L2": branch(assumed_second)},
        "links_excluded": 0,
    }


def _scope(profile=branch_calculations.NETWORK_PROFILE, entity="network"):
    return {
        "profile": profile,
        "entity_id": entity,
        "metrics": ["max_branch_utilisation", "branches_above_rating"],
    }


def _facts(raw, scope):
    facts, _ = branch_calculations.calculate(raw, scope)
    return {f["metric"]: f["value"] for f in facts}


def test_sealed_above_rating_count_leaves_an_assumed_branch_out_and_says_so(
    monkeypatch,
):
    from harness import calculations

    monkeypatch.setattr(
        calculations, "requested_metrics", lambda scope: scope["metrics"]
    )
    assert _facts(_raw(assumed_second=False), _scope())["branches_above_rating"] == 2
    assumed = _raw(assumed_second=True)
    facts = _facts(assumed, _scope())
    assert facts["branches_above_rating"] == 1
    assert facts["max_branch_utilisation"] == pytest.approx(150)  # loading stays
    assert any(
        "1 branch(es) with an assumed rating" in line
        for line in branch_calculations.notes(assumed)
    )
    assert not any(
        "assumed" in line
        for line in branch_calculations.notes(_raw(assumed_second=False))
    )


def test_sealed_hours_above_rating_of_an_assumed_branch_is_unavailable(monkeypatch):
    from harness import calculations

    monkeypatch.setattr(
        calculations, "requested_metrics", lambda scope: scope["metrics"]
    )
    scope = {
        **_scope(branch_calculations.PROFILE, "L2"),
        "metrics": ["peak_branch_utilisation", "hours_above_rating"],
    }
    for name, hours_available in (("stated", True), ("assumed", False)):
        raw = _raw(assumed_second=name == "assumed")
        raw["profile"] = branch_calculations.PROFILE
        raw["entity_id"] = "L2"
        raw["branches"] = {"L2": raw["branches"]["L2"]}
        facts, slots = branch_calculations.calculate(raw, scope)
        got = {f["metric"]: f["value"] for f in facts}
        assert ("hours_above_rating" in got) is hours_available
        assert got["peak_branch_utilisation"] == pytest.approx(150)
        if not hours_available:
            assert [
                s["reason"] for s in slots if s["disposition"] == "unavailable"
            ] == ["rating_assumed"]


def test_what_if_loading_comparison_claims_no_binding_hours_for_an_assumed_line():
    from services.compare.loading import _compute_loading_summary

    n = _imported()
    name = next(i for i in n.lines.index if n.lines.at[i, "s_nom_source"] == "assumed")
    _overloaded(n, name)
    n.lines["s_nom_opt"] = n.lines["s_nom"]  # as after a solve
    result = _compute_loading_summary(n, [], False, True)
    rows = {e.name: e for e in result.lines}
    assert rows[name].rating_assumed is True
    assert (
        rows[name].binding_hours is None
    )  # not evaluated, never a 0 that reads as a pass
    assert rows[name].peak_loading.total == pytest.approx(1.5)
    assert (
        result.assumed_rating_count == sum(e.rating_assumed for e in result.lines) >= 1
    )
    assert result.assumed_rating_count == int(
        (n.lines["s_nom_source"] == "assumed").sum()
    )


def test_what_if_loading_comparison_without_the_marker_is_unchanged():
    from services.compare.loading import _compute_loading_summary

    n = _imported()
    name = n.lines.index[0]
    n.lines["s_nom_source"] = "file"
    _overloaded(n, name)
    n.lines["s_nom_opt"] = n.lines["s_nom"]
    result = _compute_loading_summary(n, [], False, True)
    row = next(e for e in result.lines if e.name == name)
    assert row.rating_assumed is False and row.binding_hours.total > 0
    assert result.assumed_rating_count == 0


def test_every_branch_assumed_leaves_the_network_count_unavailable_not_zero(
    monkeypatch,
):
    from harness import calculations

    monkeypatch.setattr(
        calculations, "requested_metrics", lambda scope: scope["metrics"]
    )
    raw = _raw(assumed_second=True)
    raw["branches"]["L1"]["rating_assumed"] = True
    facts, slots = branch_calculations.calculate(raw, _scope())
    got = {f["metric"]: f["value"] for f in facts}
    assert "branches_above_rating" not in got and got[
        "max_branch_utilisation"
    ] == pytest.approx(150)
    assert [s["reason"] for s in slots if s["disposition"] == "unavailable"] == [
        "rating_assumed"
    ]
    assert any(
        "2 branch(es) with an assumed rating" in n
        for n in branch_calculations.notes(raw)
    )


def test_marker_less_sealed_facts_equal_the_literal_master_output(monkeypatch):
    from harness import calculations

    monkeypatch.setattr(
        calculations, "requested_metrics", lambda scope: scope["metrics"]
    )
    raw = _raw(assumed_second=False)
    facts, slots = branch_calculations.calculate(raw, _scope())
    assert facts == [
        {
            "path": "/max_branch_utilisation",
            "entity_id": "network",
            "metric": "max_branch_utilisation",
            "value": 150.0,
            "unit": "%",
            "formula_id": "network-max-branch-utilisation/1",
        },
        {
            "path": "/branches_above_rating",
            "entity_id": "network",
            "metric": "branches_above_rating",
            "value": 2,
            "unit": "branches",
            "formula_id": "network-branches-above-rating/1",
        },
    ]
    assert slots == [
        {
            "metric": "max_branch_utilisation",
            "disposition": "answered",
            "requested_snapshots": 3,
        },
        {
            "metric": "branches_above_rating",
            "disposition": "answered",
            "requested_snapshots": 3,
        },
    ]
    assert branch_calculations.notes(raw) == [
        "2 Lines and 0 Transformers; 0 Links excluded."
    ]


def test_what_if_loading_reads_the_link_p_nom_marker():
    from services.compare.loading import _compute_loading_summary

    n = _imported()
    name = n.lines.index[0]
    _overloaded(n, name)
    n.lines["s_nom_opt"] = n.lines["s_nom"]
    n.add("Bus", "far")
    n.add("Link", "lk", bus0=n.buses.index[0], bus1="far", p_nom=10.0, p_nom_opt=10.0)
    n.links["p_nom_source"] = "assumed"
    n.links_t.p0["lk"] = 15.0
    row = next(
        e for e in _compute_loading_summary(n, [], False, True).lines if e.name == "lk"
    )
    assert row.rating_assumed is True and row.binding_hours is None
    n.links["p_nom_source"] = "file"
    row = next(
        e for e in _compute_loading_summary(n, [], False, True).lines if e.name == "lk"
    )
    assert row.rating_assumed is False and row.binding_hours.total > 0


def _slots(raw, scope, monkeypatch):
    from harness import calculations

    monkeypatch.setattr(calculations, "requested_metrics", lambda s: s["metrics"])
    return {
        s["metric"]: s.get("reason")
        for s in branch_calculations.calculate(raw, scope)[1]
    }


def test_a_stated_branch_under_its_rating_is_not_counted_beside_an_assumed_one(
    monkeypatch,
):
    raw = _raw(assumed_second=True)
    raw["branches"]["L1"]["p0"] = [5.0] * 3
    raw["branches"]["L1"]["p1"] = [-5.0] * 3
    facts, _ = branch_calculations.calculate(raw, _scope())
    assert {f["metric"]: f["value"] for f in facts}[
        "branches_above_rating"
    ] == 0  # one stated, judged
    raw["branches"]["L1"]["p0"] = [15.0] * 3
    raw["branches"]["L1"]["p1"] = [-15.0] * 3
    raw["branches"]["L1"]["rating"] = 15.0  # exactly at its rating: not above
    facts, _ = branch_calculations.calculate(raw, _scope())
    assert {f["metric"]: f["value"] for f in facts}["branches_above_rating"] == 0


def test_the_unavailable_reason_names_the_assumed_rating_only_when_that_is_the_cause(
    monkeypatch,
):
    two = {**_scope(), "metrics": ["hours_above_rating", "branches_above_rating"]}
    # network profile: hours is not a network metric, so it stays unsupported; some assumed: the count answers
    some = _raw(assumed_second=True)
    assert _slots(some, two, monkeypatch) == {
        "hours_above_rating": "unsupported_metric",
        "branches_above_rating": None,
    }
    # every branch assumed: the count is the one left out
    every = _raw(assumed_second=True)
    every["branches"]["L1"]["rating_assumed"] = True
    assert _slots(every, two, monkeypatch) == {
        "hours_above_rating": "unsupported_metric",
        "branches_above_rating": "rating_assumed",
    }
    # missing data wins over the marker
    broken = _raw(assumed_second=True)
    broken["branches"]["L1"]["p0"][0] = None
    assert (
        _slots(broken, two, monkeypatch)["branches_above_rating"]
        == "invalid_or_missing_branch_data"
    )
    # single branch: its count is not a branch metric; a stated branch keeps its hours
    single = {
        **_scope(branch_calculations.PROFILE, "L2"),
        "metrics": ["hours_above_rating", "branches_above_rating"],
    }
    raw = _raw(assumed_second=True)
    raw["profile"], raw["entity_id"], raw["branches"] = (
        branch_calculations.PROFILE,
        "L2",
        {"L2": raw["branches"]["L2"]},
    )
    assert _slots(raw, single, monkeypatch) == {
        "hours_above_rating": "rating_assumed",
        "branches_above_rating": "unsupported_metric",
    }


def _case14_raw(monkeypatch, *, assumed, transformers_assumed=False):
    """case14 through the real source(): every line at 150 %, the named lines assumed."""
    from services import workflow_tools

    n = _imported()
    _overloaded(n, n.lines.index[0])
    for line in n.lines.index:  # every line at 150 % of its own rating
        rating = float(n.lines.at[line, "s_nom"])
        n.lines_t.p0[line], n.lines_t.p1[line] = 1.5 * rating, -1.5 * rating
    for t in ("p0", "p1"):
        frame = getattr(n.transformers_t, t)
        for tr in n.transformers.index:
            frame[tr] = 0.0
    n.lines["s_nom_source"] = [
        "assumed" if i in assumed else "file" for i in n.lines.index
    ]
    n.transformers["s_nom_source"] = "assumed" if transformers_assumed else "file"
    monkeypatch.setattr(
        workflow_tools, "_network_input_fingerprint", lambda ctx, exact=False: "fp"
    )
    n.meta["assistant_solve_input_fingerprint"] = "fp"
    n.meta["assistant_solve_exact_input_fingerprint"] = "fp"
    ctx = SimpleNamespace(
        network=n, solver_state={"status": "completed", "condition": "optimal"}
    )
    return n, branch_calculations.source(ctx, "network", True)


def _network_facts(raw, monkeypatch):
    from harness import calculations

    monkeypatch.setattr(
        calculations, "requested_metrics", lambda scope: scope["metrics"]
    )
    facts, slots = branch_calculations.calculate(raw, _scope())
    return {f["metric"]: f["value"] for f in facts}, slots


def test_case14_one_assumed_line_is_left_out_of_the_sealed_count(monkeypatch):
    n, raw = _case14_raw(monkeypatch, assumed={"__none__"})
    stated, _ = _network_facts(raw, monkeypatch)
    assert stated["branches_above_rating"] == len(n.lines)  # every line at 150 %
    n, raw = _case14_raw(monkeypatch, assumed={n.lines.index[0]})
    got, _ = _network_facts(raw, monkeypatch)
    assert got["branches_above_rating"] == len(n.lines) - 1
    assert got["max_branch_utilisation"] == pytest.approx(150)
    assert any(
        "1 branch(es) with an assumed rating" in x
        for x in branch_calculations.notes(raw)
    )


def test_case14_all_lines_assumed_leaves_the_count_unavailable_not_zero(monkeypatch):
    n, raw = _case14_raw(
        monkeypatch, assumed=set(_imported().lines.index), transformers_assumed=True
    )
    got, slots = _network_facts(raw, monkeypatch)
    assert "branches_above_rating" not in got
    assert got["max_branch_utilisation"] == pytest.approx(150)
    reasons = {s["metric"]: s.get("reason") for s in slots}
    assert reasons["branches_above_rating"] == "rating_assumed"
    assert any(
        f"{len(n.lines) + len(n.transformers)} branch(es) with an assumed rating" in x
        for x in branch_calculations.notes(raw)
    )
