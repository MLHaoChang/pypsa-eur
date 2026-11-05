"""LC-T07: one branch, one hand oracle, every reader (LC-03 PR-O).

One line L12, nameplate 100 MVA, two snapshots (one per investment period,
one year each, so the weights are the snapshot weights 1 and 3):

    snapshot   p0    p1   flow   s_max_pu(t)   limit   utilisation
    2030        45   -45    45      1.0         100       45 %
    2040        64   -65    65      0.5          50      130 %

Flow is the larger terminal magnitude, so snapshot 2 is 65 MW from p1: a
p0-only or signed reader sees 64 (128 %). The static `s_max_pu` column is
0.8 and must not be read (65 / 80 = 81.25 %). Hand oracle over the whole
window: peak utilisation 130 %, peak nameplate loading 65 / 100 = 65 %,
over-limit weighted hours 3 (only snapshot 2, weight 3), at-limit hours 3,
weighted hours 4, peak at snapshot 2. Over the first snapshot alone: 45 %,
45 %, 0 h over the limit, weighted hours 1.

Readers: the chat tool (`get_loadflow_summary`), the Load flow tab's route
(`/api/results/loadflow-view`), the results workbook's Load Flow sheet, the
kept `loadflow` section of a past run (`get_loadflow_summary(run_id=)`), the
past-run view (`/api/results/run-view`) and its export (`export_run_evidence`).
Each is asserted against the literal numbers above, never reader to reader.
The active-power proxy is never called AC thermal compliance on any of them.
"""
from __future__ import annotations

import io
import json
import re

import pandas as pd
import pypsa
import pytest
from openpyxl import load_workbook

from services import chat_service, chat_tools, run_evidence
from services import results_workbook as RW
from services.loadflow_summary import get_loadflow_summary
from services.pypsa_service import PyPSAService
from services.results_workbook.views import HORIZON
from services.solver_service import SolverConfig

PROJECT = "77777777-7777-7777-7777-777777777777"
OWNER = "owner-lc-t07"

WHOLE = {"window": {"from": 0, "to": 1, "snapshots": 2, "of": 2, "weighted_hours": 4.0},
         "row": {"name": "L12", "class": "Line", "basis": "active_power_proxy",
                 "util_pct": 130.0, "rating_pct": 65.0, "peak_flow": 65.0,
                 "h_over_limit": 3.0, "h_at_limit": 3.0, "rating": {"value": 100.0},
                 "s_max_pu": {"min": 0.5, "max": 1.0}},
         "over_limit": 1}
FIRST = {"window": {"from": 0, "to": 0, "snapshots": 1, "of": 2, "weighted_hours": 1.0},
         "row": {"name": "L12", "class": "Line", "basis": "active_power_proxy",
                 "util_pct": 45.0, "rating_pct": 45.0, "peak_flow": 45.0,
                 "h_over_limit": 0.0, "h_at_limit": 0.0, "rating": {"value": 100.0},
                 "s_max_pu": {"min": 1.0, "max": 1.0}},
         "over_limit": 0}
# Literal, not imported: a reworded disclaimer that claims compliance must fail.
LIMITS_CLAIM = ("Not proof of AC thermal compliance: no N-1, no voltage limits or "
                "temperature-dependent thermal ratings.")
UNITS = {"rating": "MVA", "flow": "MW (active_power_proxy) or MVA (ac_apparent_power)",
         "utilisation": "%", "hours": "weighted hours"}


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=1, freq="h")],
        names=["period", "timestep"]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = 1.0
    n.snapshot_weightings.loc[:, :] = [[1.0] * 3, [3.0] * 3]
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L12", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100.0, s_max_pu=0.8)
    idx = n.snapshots
    n.lines_t.s_max_pu = pd.DataFrame({"L12": [1.0, 0.5]}, index=idx)
    n.lines_t.p0 = pd.DataFrame({"L12": [45.0, 64.0]}, index=idx)
    n.lines_t.p1 = pd.DataFrame({"L12": [-45.0, -65.0]}, index=idx)
    n._objective = 0.0
    return n


def _solved(n: pypsa.Network) -> None:
    """Mark the active network solved on its current inputs, as a solve records it."""
    from services import workflow_tools

    state = PyPSAService.get_solver_state()
    state.update({"status": "completed", "condition": "optimal", "objective": 0.0,
                  "kind": "lopf", "ac_pf_results": None, "ac_pf_convergence": None,
                  "ac_pf_convergence_list": None, "ac_pf_converged_count": None,
                  "ac_pf_total_snapshots": None})
    workflow_tools._record_solve_inputs(n, state["solver_config"])


@pytest.fixture
def line(install_network, tmp_path, monkeypatch):
    n = _network()
    install_network(n)
    _solved(n)
    root = tmp_path / PROJECT
    root.mkdir()
    monkeypatch.setattr(run_evidence, "_authority", lambda: (root, PROJECT, OWNER))
    return {"network": n, "root": root}


def _assert_oracle(payload: dict, oracle: dict) -> None:
    """The literal hand numbers, units, window and source of one reader's payload."""
    assert payload["status"] == "ok", payload
    assert payload["source"] == "lopf"
    assert {k: payload["window"][k] for k in oracle["window"]} == oracle["window"]
    assert payload["units"] == UNITS
    assert payload["bases_present"] == ["active_power_proxy"]
    assert payload["counts"]["over_limit"] == oracle["over_limit"]
    assert payload["claims"]["full_ac_thermal_compliance"] is False
    row = payload["top"][0]
    for key, value in oracle["row"].items():
        if isinstance(value, dict):
            for k, v in value.items():
                assert row[key][k] == pytest.approx(v), (key, k)
        elif isinstance(value, float):
            assert row[key] == pytest.approx(value), key
        else:
            assert row[key] == value, key


_CLAIM = re.compile(r"(?i)thermal|complian")


def _no_compliance_claim(text: str) -> None:
    """The proxy is never called thermal compliance: only the limits disclaimer says the word."""
    assert LIMITS_CLAIM in text
    stripped = text.replace(LIMITS_CLAIM, "").replace("full_ac_thermal_compliance", "")
    assert _CLAIM.search(stripped) is None, _CLAIM.search(stripped)


# ── the current readers ──────────────────────────────────────────────────

def test_the_chat_tool_gives_the_hand_oracle(line):
    out = get_loadflow_summary()
    _assert_oracle(out, WHOLE)
    # Peak at snapshot 2 (period 2040). A multi-period index prints as a raw
    # tuple repr today; only the snapshot it names is pinned here.
    assert "2040" in out["top"][0]["peak_at"] and "2030-01-01 00:00:00" in out["top"][0]["peak_at"]
    assert out["top"][0]["mean_util_pct"] == pytest.approx(108.75)  # (45·1 + 130·3) / 4
    _no_compliance_claim(json.dumps(out))


def test_the_chat_tool_over_the_first_snapshot_only(line):
    _assert_oracle(get_loadflow_summary(window_from=0, window_to=0), FIRST)


def test_the_load_flow_tab_route_gives_the_hand_oracle(line, client):
    whole = client.get("/api/results/loadflow-view")
    assert whole.status_code == 200, whole.text
    view = whole.json()
    _assert_oracle(view, WHOLE)
    # The tab's table and chart read the same row and the same series.
    assert view["rows"][0]["util_pct"] == pytest.approx(130.0)
    assert view["rows"][0]["rating_pct"] == pytest.approx(65.0)
    series = view["series"][0]
    assert series["flow"] == pytest.approx([45.0, 65.0])
    assert series["util_pct"] == pytest.approx([45.0, 130.0])
    _no_compliance_claim(whole.text)

    first = client.get("/api/results/loadflow-view", params={"window_from": 0, "window_to": 0})
    assert first.status_code == 200, first.text
    _assert_oracle(first.json(), FIRST)
    assert first.json()["series"][0]["util_pct"] == pytest.approx([45.0])


def _sheet_text(ws) -> str:
    return "\n".join(str(c) for row in ws.iter_rows(values_only=True) for c in row if c is not None)


def _key(ws, label: str, period: str):
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row[0] == label and str(row[2]) == period:
            return row[1], row[3]
    raise AssertionError(f"no key {label!r} / {period!r}")


def test_the_results_workbook_gives_the_hand_oracle(line):
    n = line["network"]
    blob = RW.build_results_workbook(RW.WorkbookInputs(
        network=n, cfg=SolverConfig(), lost_load=None,
        result_df=lambda net, accessor, attr, source="lopf": getattr(getattr(net, accessor), attr, None),
        views=["loadflow"], project="Base", scenarios=None, solved_settings=lambda: None,
        project_id=PROJECT, project_for=None))
    ws = load_workbook(io.BytesIO(blob))["Load Flow"]
    # The whole window, then the first period (the first snapshot alone).
    assert _key(ws, "Highest peak loading", HORIZON) == ("%", pytest.approx(130.0))
    assert _key(ws, "Branches over their limit", HORIZON) == ("count", 1)
    assert _key(ws, "Weighted hours in the window", HORIZON) == ("h", pytest.approx(4.0))
    assert _key(ws, "Highest peak loading", "2030") == ("%", pytest.approx(45.0))
    assert _key(ws, "Branches over their limit", "2030") == ("count", 0)
    assert _key(ws, "Weighted hours in the window", "2030") == ("h", pytest.approx(1.0))
    assert _key(ws, "Highest peak loading", "2040") == ("%", pytest.approx(130.0))
    assert _key(ws, "Weighted hours in the window", "2040") == ("h", pytest.approx(3.0))
    rows = list(ws.iter_rows(values_only=True))
    header = next(r for r in rows if r and "rating_pct" in r and "h_over_limit" in r)
    branch = [dict(zip(header, r)) for r in rows[rows.index(header) + 1:]
              if r and r[0] == "Base" and r[1] == HORIZON and r[3] == "L12"]
    assert len(branch) == 1
    assert branch[0]["util_pct"] == pytest.approx(130.0)
    assert branch[0]["rating_pct"] == pytest.approx(65.0)
    assert branch[0]["h_over_limit"] == pytest.approx(3.0)
    assert branch[0]["basis"] == "active_power_proxy"
    by_period = {r[1]: dict(zip(header, r)) for r in rows[rows.index(header) + 1:]
                 if r and r[0] == "Base" and r[3] == "L12"}
    assert by_period["2030"]["util_pct"] == pytest.approx(45.0)
    assert by_period["2030"]["h_over_limit"] == pytest.approx(0.0)
    _no_compliance_claim(_sheet_text(ws))


# ── the past run's readers ───────────────────────────────────────────────

def _publish(job_id: str) -> str:
    ctx = PyPSAService.get_active_context()
    token = run_evidence.start_foreground_run(ctx, job_id=job_id, tool_name="run_simulation",
                                              kind="lopf")
    assert token is not None
    record = run_evidence.publish_foreground_run(token, ctx)
    assert record is not None and record["status"] == "completed", record
    return token["run_id"]


@pytest.fixture
def past_run(line):
    """Run 1 holds the oracle; the line is then re-rated to 200 MVA and solved again."""
    first = _publish("lc-t07-first")
    n = line["network"]
    n.lines.loc["L12", "s_nom"] = 200.0
    _solved(n)
    _publish("lc-t07-second")
    # The current results moved (65 / 100 = 65 % on master), so a reader that
    # fell back to them for the past run would fail the oracle.
    assert get_loadflow_summary()["top"][0]["util_pct"] != pytest.approx(130.0)
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    yield first
    chat_tools.set_chat_session(None)


def test_the_kept_section_gives_the_hand_oracle(past_run):
    old = get_loadflow_summary(run_id=past_run)
    assert old["scope"] == "historical" and old["run"]["run_id"] == past_run
    _assert_oracle(old, WHOLE)
    _no_compliance_claim(json.dumps(old))
    # The section holds one window; another one is refused, never recomputed.
    narrowed = get_loadflow_summary(run_id=past_run, window_from=0, window_to=0)
    assert narrowed["status"] == "unavailable" and "top" not in narrowed
    assert narrowed["unavailable_kind"] == "historical_scope_mismatch"
    assert narrowed["captured"] == {"source": "lopf", "window_from": 0, "window_to": 1}


def test_the_past_run_view_gives_the_hand_oracle(past_run, client):
    view = client.get("/api/results/run-view", params={"run_id": past_run, "section": "loadflow"})
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["binding"]["scope"] == "historical" and body["binding"]["run_id"] == past_run
    _assert_oracle(body["data"]["data"], WHOLE)
    _no_compliance_claim(view.text)


def test_the_past_run_export_gives_the_hand_oracle(past_run, monkeypatch):
    saved = []

    def fake_export(data, filename, mime, **_kwargs):
        saved.append(data)
        return {"file_id": "export-1", "filename": filename, "mime": mime, "size": len(data),
                "kind": "agent_export", "sha256": "abc"}

    monkeypatch.setattr(chat_tools, "_save_agent_export", fake_export)
    out = chat_tools.DISPATCHERS["export_run_evidence"](run_id=past_run, section="loadflow")
    assert out["run_id"] == past_run and out["section"] == "loadflow"
    payload = json.loads(saved[0])
    assert payload["run_id"] == past_run and payload["section"] == "loadflow"
    _assert_oracle(payload["data"], WHOLE)
    _no_compliance_claim(saved[0].decode())
