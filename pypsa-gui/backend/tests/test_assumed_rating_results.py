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
