"""LC-15: one bounded native summary of a project's assets (OJ-13, OJ-14).

The oracle is the fixture below, counted by hand. The summary reads the live
network for the caller's active project and the saved files otherwise, so a
peek at another project never switches the active one.
"""
import pandas as pd
import pypsa
import pytest

from harness.providers.fake import FakeProvider
from services import chat_tools, project_registry, run_evidence
from services.pypsa_service import PyPSAService
from tests.test_openai_comprehensive_live import setup_profile
from tests.test_workflow_tools import helpers_state  # noqa: F401

GRID, COPY = "oj-grid", "oj-grid-copy"


def oj_grid() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=24, freq="h"))
    for bus, v in (("B1", 380), ("B2", 380), ("B3", 110)):
        n.add("Bus", bus, v_nom=v)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=300, length=120)
    n.add("Line", "L2", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=300, length=80)
    n.add("Transformer", "T1", bus0="B2", bus1="B3", x=0.1, s_nom=200)
    n.add("Carrier", "gas")
    n.add("Carrier", "solar")
    n.add("Generator", "Gas", bus="B1", carrier="gas", p_nom=100, marginal_cost=50)
    n.add("Generator", "Solar", bus="B2", carrier="solar", p_nom=80, p_nom_extendable=True,
          p_max_pu=[0.0] * 6 + [0.5] * 12 + [0.0] * 6)
    n.add("StorageUnit", "Battery", bus="B2", p_nom=20, max_hours=4)
    n.add("Store", "H2 tank", bus="B3", e_nom=500)
    n.add("Load", "Demand", bus="B3", p_set=60)
    return n


@pytest.fixture
def grids(client, session_ctx, install_network):
    install_network(oj_grid())
    assert client.post(f"/api/projects/{GRID}").status_code == 200
    grid_id = next(p["id"] for p in client.get("/api/projects/").json() if p["name"] == GRID)
    response = client.post(f"/api/projects/{grid_id}/scenarios", json={"name": COPY})
    assert response.status_code == 201, response.text
    copy_id = next(p["id"] for p in client.get("/api/projects/").json() if p["name"] == COPY)
    assert client.post(f"/api/projects/{grid_id}/activate").status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        yield ctx, grid_id, copy_id
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def summary_of(project: str, **params) -> dict:
    return chat_tools.get_project_statistics(project, **params)["summary"]


def test_the_summary_matches_the_hand_counted_oracle(grids):
    _ctx, grid_id, _copy = grids
    s = summary_of(grid_id)
    assert s["version"] == 1 and s["project_name"] == GRID and s["source"] == "live"
    assert s["counts"] == {"Bus": 3, "Line": 2, "Transformer": 1, "Link": 0, "Generator": 2,
                           "Load": 1, "StorageUnit": 1, "Store": 1, "ShuntImpedance": 0}
    gens = {row["name"]: row for row in s["assets"]["Generator"]["items"]}
    assert gens["Gas"]["p_nom"] == 100 and gens["Gas"]["carrier"] == "gas"
    assert gens["Solar"]["p_nom"] == 80 and gens["Solar"]["p_nom_extendable"] is True
    assert [row["name"] for row in s["assets"]["Generator"]["items"]] == ["Gas", "Solar"]
    assert s["capacity"]["generation_mw_by_carrier"] == {"gas": 100.0, "solar": 80.0}
    assert s["capacity"]["extendable"] == {"Generator": ["Solar"]}
    battery = s["assets"]["StorageUnit"]["items"][0]
    assert battery["name"] == "Battery" and battery["p_nom"] == 20 and battery["energy_mwh"] == 80
    assert s["capacity"]["storage_power_mw"] == 20.0
    assert s["capacity"]["storage_energy_mwh"] == 580.0
    assert s["lines"] == {"count": 2, "total_length_km": 200.0, "total_s_nom_mva": 600.0}
    assert s["time"]["snapshot_count"] == 24 and s["time"]["resolution"] == "1h"
    assert s["time"]["first"].startswith("2026-01-01T00:00") and s["time"]["last"].startswith("2026-01-01T23:00")
    assert s["gaps"]["variable_generators_without_profile"] == []
    assert s["latest_run"] is None
    assert s["validation"]["errors"] >= 0 and "warnings" in s["validation"]


def test_a_variable_generator_without_a_profile_is_named_as_missing(grids):
    PyPSAService.get_network().add("Generator", "Wind", bus="B1", carrier="wind", p_nom=30)
    s = summary_of(grids[1])
    assert s["gaps"]["variable_generators_without_profile"] == ["Wind"]


def test_each_class_is_bounded_with_has_more(grids):
    n = PyPSAService.get_network()
    for i in range(7):
        n.add("Load", f"Extra {i}", bus="B1", p_set=i)
    s = summary_of(grids[1], limit=3)
    loads = s["assets"]["Load"]
    assert len(loads["items"]) == 3 and loads["has_more"] is True and loads["total"] == 8


def test_the_latest_run_is_named_with_whether_it_is_current(grids):
    ctx, grid_id, _copy = grids
    from services.run_evidence import _input_fingerprint
    with chat_tools._acting() as (db, user):
        root = project_registry.project_dir(project_registry.resolve_project(db, user, grid_id))
    run_evidence.publish_run(root, grid_id, run_id="r-old", kind="simulation", status="completed",
                             started_at="2026-10-01T10:00:00+00:00", finished_at="2026-10-01T10:02:00+00:00",
                             mode="lopf", fingerprint="not-the-current-inputs",
                             metrics={"objective": {"value": 1.0, "unit": "EUR"}})
    assert summary_of(grid_id)["latest_run"]["current"] is False
    run_evidence.publish_run(root, grid_id, run_id="r-new", kind="simulation", status="completed",
                             started_at="2026-10-02T14:00:00+00:00", finished_at="2026-10-02T14:02:00+00:00",
                             mode="lopf", fingerprint=_input_fingerprint(ctx),
                             metrics={"objective": {"value": 1.0, "unit": "EUR"}})
    run = summary_of(grid_id)["latest_run"]
    assert run["run_id"] == "r-new" and run["status"] == "completed" and run["current"] is True
    assert run["finished_at"].startswith("2026-10-02T14:02")


def test_another_project_is_summarised_from_its_saved_files_without_switching(grids, client):
    _ctx, grid_id, copy_id = grids
    PyPSAService.get_network().add("Generator", "Unsaved", bus="B1", carrier="gas", p_nom=5)
    s = summary_of(copy_id)
    assert s["project_name"] == COPY and s["source"] == "saved"
    assert s["counts"]["Generator"] == 2 and s["capacity"]["generation_mw_by_carrier"] == {"gas": 100.0, "solar": 80.0}
    assert PyPSAService.get_loaded_project() == GRID
    assert client.get("/api/network/meta").json()["loaded_project"] == GRID


def test_a_saved_projects_run_currency_is_reported_as_not_known(grids):
    _ctx, _grid, copy_id = grids
    with chat_tools._acting() as (db, user):
        root = project_registry.project_dir(project_registry.resolve_project(db, user, copy_id))
    run_evidence.publish_run(root, copy_id, run_id="r-copy", kind="simulation", status="completed",
                             started_at="2026-10-02T14:00:00+00:00", finished_at="2026-10-02T14:02:00+00:00",
                             mode="lopf", fingerprint="fp", metrics={"objective": {"value": 1.0, "unit": "EUR"}})
    run = summary_of(copy_id)["latest_run"]
    assert run["run_id"] == "r-copy" and run["current"] is None and run["note"]


def test_the_existing_statistics_keys_are_unchanged(grids):
    out = chat_tools.get_project_statistics(grids[2])
    assert {"name", "meta", "buses", "generators", "lines", "snapshots", "carriers"} <= set(out)
    assert (out["buses"], out["generators"], out["lines"], out["snapshots"]) == (3, 2, 2, 24)


class Stepwise(FakeProvider):
    """Builds each model turn from the native results so far, as a model would."""

    def __init__(self, steps, results):
        super().__init__([])
        self._steps, self._results = steps, results

    def stream(self, request):
        index = len(self.requests)
        if index < len(self._steps):
            name, args = self._steps[index](self._results)
            self._turns = [{"blocks": [{"type": "tool_use", "id": f"oj-{index}", "name": name, "input": args}]}]
        else:
            self._turns = [{"blocks": [{"type": "text", "text": "Summarised."}]}]
        return super().stream(request)


def journey(monkeypatch, steps, prompt):
    session = setup_profile(monkeypatch, {"resolve_project_target", "activate_project",
                                          "get_project_statistics", "ui_open_panel"})
    chat_tools.set_chat_session(session)
    results = []
    frames = []
    from services import chat_service

    for event, data in chat_service.run_turn(session, prompt, provider=Stepwise(steps, results),
                                             ui_context={"ui_mode": "guided"}):
        frames.append((event, data))
        if event == "tool_result":
            results.append(data["result"])
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
    return frames, results


def test_oj13_open_and_summarise_the_named_project(grids, client, monkeypatch):
    _ctx, grid_id, copy_id = grids
    switched, _ = journey(monkeypatch, [lambda r: ("activate_project", {"project_id": copy_id})], "Open the copy.")
    assert PyPSAService.get_loaded_project() == COPY
    steps = [lambda r: ("resolve_project_target", {"project_name": GRID}),
             lambda r: ("activate_project", {"project_id": r[0]["project_id"]}),
             lambda r: ("get_project_statistics", {"name": r[0]["project_id"]}),
             lambda r: ("ui_open_panel", {"panel_id": "overview"})]
    frames, results = journey(monkeypatch, steps, f"Open {GRID} and tell me what's in it.")
    assert not [d for e, d in frames if e in {"error", "tool_error"}], frames
    assert any(e == "project_rebound" for e, _ in frames)
    summary = results[2]["summary"]
    assert summary["project_name"] == GRID and summary["counts"]["Generator"] == 2
    assert any(e == "ui_event" and d.get("panel_id") == "overview" for e, d in frames)
    assert PyPSAService.get_loaded_project() == GRID


def test_oj14_peek_at_another_project_without_switching(grids, monkeypatch):
    steps = [lambda r: ("resolve_project_target", {"project_name": COPY}),
             lambda r: ("get_project_statistics", {"name": r[0]["project_id"]})]
    frames, results = journey(monkeypatch, steps, f"What's in {COPY}? Don't open it.")
    assert not [d for e, d in frames if e in {"error", "tool_error", "project_rebound"}], frames
    assert results[1]["summary"]["project_name"] == COPY and results[1]["summary"]["source"] == "saved"
    assert PyPSAService.get_loaded_project() == GRID


def test_the_summary_rule_rides_the_tools_on_prompt_only():
    from harness import compose
    from harness.session import ChatSession

    session = ChatSession()
    assert compose._PROJECT_OVERVIEW in compose._build_system_prompt(session)
    assert "get_project_statistics" not in compose._build_system_prompt(session, include_tools=False)
