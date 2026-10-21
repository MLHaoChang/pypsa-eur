"""site-import/1 (checked-results S7b): the Load shape tab's import and export.

Each fixture is checked against hand values and against the tab's own
`load_shape` payload on the same solve.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from services import workflow_tools
from services.results.load_shape import load_shape
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "site-import/1"


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE)


def solve(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


def ask(checked, ctx, values, **window):
    scope = {"version": 1, "profile": PROFILE, "project_id": ctx.project_uuid,
             "entity_id": "network", "retain": False, **window}
    return checked.run(NumericalProvider(values), message="Check the grid import.", checked_scope=scope)


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def reasons(frames):
    return {s["metric"]: s["reason"] for s in approval(frames)["receipt"]["result_slots"]
            if s["disposition"] != "answered"}


def tab(n):
    """The tab's annual import and export, and its peak import over every week."""
    p = load_shape(n)
    assert p["status"] == "ok", p.get("reason")
    row = p["annual"][0]
    peak = max(max(load_shape(n, week=k)["week"]["series"]["grid"]["mw"])
               for k in range(len(p["week"]["weeks"])))
    return {"grid_import_energy": row["grid"], "export_energy": row["export"], "peak_grid_import": peak}


def site(baseline, load, local, limit_pu):
    """A site bus behind a 6 MW grid_import meter; the grid buys at 20 and sells at 20."""
    ctx = baseline[0]
    n = ctx.network
    n.generators.at["G1", "marginal_cost"] = 0
    n.generators_t.p_max_pu = pd.DataFrame({"G1": local}, index=n.snapshots)
    n.loads_t.p_set = pd.DataFrame({"L1": load}, index=n.snapshots)
    n.add("Bus", "grid")
    n.add("Carrier", ["grid"])
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=100, p_min_pu=-1, marginal_cost=20)
    n.add("Link", "meter", bus0="grid", bus1="B1", p_nom=6, p_min_pu=-1, eh_role="grid_import")
    n.links_t.p_max_pu = pd.DataFrame({"meter": limit_pu}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 1]
    return ctx


@pytest.fixture
def importing(baseline):
    """No local output: 6 MW then 3 MW come in, each at the meter's limit (6 x 1, 6 x 0.5)."""
    return site(baseline, load=[6, 3], local=[0.0, 0.0], limit_pu=[1.0, 0.5])


@pytest.fixture
def exporting(baseline):
    """6 MW in for the first hour; then free local output serves 8 and sells 6 back."""
    return site(baseline, load=[6, 8], local=[0.0, 1.0], limit_pu=[1.0, 1.0])


def test_import_and_its_limit_per_snapshot(importing, checked):
    ctx = solve(importing)
    # Import 6 MW x 2 h + 3 MW x 1 h; both snapshots at p_nom_opt x p_max_pu: 3 weighted hours.
    values = {"grid_import_energy": 15, "peak_grid_import": 6, "export_energy": 0, "hours_at_import_limit": 3}
    frames = ask(checked, ctx, values)
    assert answered(checked, frames) == pytest.approx(values)
    expected = tab(ctx.network)
    for metric in ("grid_import_energy", "peak_grid_import", "export_energy"):
        assert values[metric] == pytest.approx(expected[metric], abs=1e-4)
    text = public_text(frames)
    assert "p_nom_opt x p_max_pu per snapshot" in text and "not checked" in text


def test_export_is_what_the_meter_delivers_into_the_grid_bus(exporting, checked):
    ctx = solve(exporting)
    assert list(ctx.network.links_t.p0["meter"]) == pytest.approx([6, -6])
    values = {"grid_import_energy": 12, "peak_grid_import": 6, "export_energy": 6, "hours_at_import_limit": 2}
    assert answered(checked, ask(checked, ctx, values)) == pytest.approx(values)
    expected = tab(ctx.network)
    for metric in ("grid_import_energy", "peak_grid_import", "export_energy"):
        assert values[metric] == pytest.approx(expected[metric], abs=1e-4)


def test_a_window_bounds_every_figure(exporting, checked):
    ctx = solve(exporting)
    stamp = ctx.network.snapshots[1].isoformat()
    values = {"grid_import_energy": 0, "peak_grid_import": 0, "export_energy": 6, "hours_at_import_limit": 0}
    assert answered(checked, ask(checked, ctx, values, start=stamp, end=stamp)) == pytest.approx(values)


def test_without_a_grid_import_link_the_limit_is_unavailable(exporting, checked):
    exporting.network.links.at["meter", "eh_role"] = ""
    ctx = solve(exporting)
    frames = ask(checked, ctx, {"grid_import_energy": 12, "peak_grid_import": 6, "export_energy": 6})
    assert reasons(frames) == {"hours_at_import_limit": "no_import_connection"}
    assert "p_max_pu per snapshot" not in public_text(frames)


def test_a_network_without_a_grid_says_so(baseline, checked):
    ctx = solve(baseline[0])
    assert set(reasons(ask(checked, ctx, {})).values()) == {"no_grid_connection"}


def test_a_network_the_tab_refuses_gives_no_figure(importing, checked):
    importing.network.buses.at["B1", "unit"] = "t/h"
    ctx = solve(importing)
    assert load_shape(ctx.network)["status"] == "not_established"
    assert set(reasons(ask(checked, ctx, {})).values()) == {"load_shape_not_established"}


def test_a_stale_solve_gives_no_figure(importing, checked):
    ctx = solve(importing)
    ctx.network.loads_t.p_set.iloc[0, 0] = 1
    assert set(reasons(ask(checked, ctx, {})).values()) == {"unverified_solve"}
