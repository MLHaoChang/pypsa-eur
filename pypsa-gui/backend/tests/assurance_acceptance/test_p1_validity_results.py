"""A1.6 / A1.7: an unknown result value is null, never a real-looking 0.

Two get_results kinds used to fill an unknown with 0:

* emissions: a CO2 cap whose dual ``mu`` is missing or non-finite showed a
  shadow price of 0 EUR/tCO2. It is now null, in ``caps[]`` and in the
  legacy ``cap``; a finite mu of 0 (a slack cap) stays 0.
* lost_load: a capture without its totals showed 0 MWh / 0 EUR, and a VoLL
  that could be neither read nor derived showed 0 EUR/MWh. They are now null;
  a captured 0 stays 0 and the VoLL derivation never divides by zero.

Every case solves through POST /api/simulation/run and reads through the
public seams: the dispatched chat tool and the route handler. The checked
profiles never certify the unknown as 0.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import math

import pytest

from services import chat_tools
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_lost_load_assurance import SHED, answered, short  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

CAP = "CO2Limit"
PROFILES = ("lost-load/1", "network-emissions/1")


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(PROFILES))


def network():
    return PyPSAService.get_active_context().network


def solve(client, session_state, voll=0.0):
    state = session_state(client)
    state["solver_config"] = SolverConfig(voll=voll)
    response = client.post("/api/simulation/run")
    assert response.status_code == 200, response.text
    state["thread"].join(timeout=90)
    assert state["status"] == "completed", state.get("condition")
    return state


def route(client, kind):
    response = client.get(f"/api/results/{kind}")
    assert response.status_code == 200, response.text
    return response.json()


# ── emissions ─────────────────────────────────────────────────────────────


@pytest.fixture
def capped(baseline, client, session_state):
    """G1 burns gas at 0.5 tCO2/MWh under a slack cap: the solve's mu is 0."""
    n = network()
    n.add("Carrier", "gas", co2_emissions=0.5)
    n.generators.at["G1", "carrier"] = "gas"
    n.add(
        "GlobalConstraint",
        CAP,
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=1e6,
    )
    solve(client, session_state)
    return network()


def shadow_prices(payload):
    return (
        [c["shadow_price_eur_per_tCO2"] for c in payload["caps"]],
        payload["cap"]["shadow_price_eur_per_tCO2"],
    )


def test_a_slack_cap_keeps_its_real_zero_shadow_price(capped, client):
    assert capped.global_constraints.at[CAP, "mu"] == 0
    for payload in (
        chat_tools.get_results(result_kind="emissions"),
        route(client, "emissions"),
    ):
        caps, legacy = shadow_prices(payload)
        assert caps == [0.0] and legacy == 0.0
        assert payload["cap"]["binding"] is False


@pytest.mark.parametrize("mu", [float("nan"), float("inf")])
def test_a_non_finite_dual_is_a_null_shadow_price(capped, client, mu):
    capped.global_constraints.at[CAP, "mu"] = mu
    for payload in (
        chat_tools.get_results(result_kind="emissions"),
        route(client, "emissions"),
    ):
        caps, legacy = shadow_prices(payload)
        assert caps == [None] and legacy is None
        assert (
            payload["caps"][0]["binding"] is None and payload["cap"]["binding"] is None
        )


def test_a_missing_dual_column_is_a_null_shadow_price(capped, client):
    capped.global_constraints.drop(columns="mu", inplace=True)
    for payload in (
        chat_tools.get_results(result_kind="emissions"),
        route(client, "emissions"),
    ):
        caps, legacy = shadow_prices(payload)
        assert caps == [None] and legacy is None


def test_explain_investment_carries_the_null_shadow_price(capped):
    capped.global_constraints.at[CAP, "mu"] = float("nan")
    out = chat_tools.explain_investment("Generator", "G1")
    [cap] = out["system_signals"]["co2_caps"]
    assert cap["name"] == CAP and cap["shadow_price_eur_per_tCO2"] is None
    assert cap["binding"] is None


def test_a_null_shadow_price_is_not_a_certified_fact(capped, baseline, checked):
    capped.global_constraints.at[CAP, "mu"] = float("nan")
    ctx = baseline[0]
    # G1 meets L1's 5 MW for two hours at 0.5 tCO2/MWh: 5 tCO2.
    frames = checked.run(
        NumericalProvider({"total_emissions": 5}),
        message="Check emissions.",
        checked_scope={
            "version": 1,
            "profile": "network-emissions/1",
            "project_id": ctx.project_uuid,
            "entity_id": "network",
            "retain": False,
        },
    )
    slots = {s["metric"]: s for s in approval(frames)["receipt"]["result_slots"]}
    assert slots["co2_cap_shadow_price"]["disposition"] == "unavailable"
    blocks = answered(checked, frames)
    assert "co2_cap_shadow_price" not in blocks
    assert blocks["total_emissions"] == pytest.approx(5)


# ── lost_load ─────────────────────────────────────────────────────────────


def capture_of(client, session_state):
    return session_state(client)["last_lost_load"]


def lost_load_reads(client):
    return chat_tools.get_results(result_kind="lost_load"), route(client, "lost_load")


def totals(payload):
    return payload["total_mwh"], payload["total_cost_eur"], payload["voll_eur_per_mwh"]


def test_a_capture_without_totals_reads_null_not_zero(short, client, session_state):
    solve(client, session_state, voll=1000.0)
    capture = capture_of(client, session_state)
    for key in ("lost_load_total_mwh", "lost_load_cost_eur", "voll_eur_per_mwh"):
        capture.pop(key, None)
    for payload in lost_load_reads(client):
        assert totals(payload) == (None, None, None)
        assert payload["voll_applied"] is True


def test_a_capture_without_voll_derives_it_from_its_totals(
    short, client, session_state
):
    solve(client, session_state, voll=1000.0)
    capture = capture_of(client, session_state)
    capture.pop("voll_eur_per_mwh")
    capture["lost_load_total_mwh"], capture["lost_load_cost_eur"] = 4.0, 6000.0
    for payload in lost_load_reads(client):
        assert totals(payload) == (4.0, 6000.0, 1500.0)


@pytest.mark.parametrize(
    "mwh, cost", [(0.0, 0.0), (0.0, 50.0), (float("nan"), 50.0), (4.0, None)]
)
def test_voll_is_never_derived_by_dividing_by_zero_or_an_unknown(
    short, client, session_state, mwh, cost
):
    solve(client, session_state, voll=1000.0)
    capture = capture_of(client, session_state)
    capture.pop("voll_eur_per_mwh")
    capture["lost_load_total_mwh"], capture["lost_load_cost_eur"] = mwh, cost
    for payload in lost_load_reads(client):
        assert payload["voll_eur_per_mwh"] is None
        got_mwh, got_cost, _ = totals(payload)
        assert got_mwh == (mwh if math.isfinite(mwh) else None)
        assert got_cost == cost


def test_a_captured_zero_stays_zero(short, client, session_state):
    solve(client, session_state, voll=1000.0)
    capture = capture_of(client, session_state)
    capture["lost_load_total_mwh"], capture["lost_load_cost_eur"] = 0.0, 0.0
    for payload in lost_load_reads(client):
        assert totals(payload) == (0.0, 0.0, 1000.0)


def test_a_captured_solve_keeps_its_real_totals(short, client, session_state):
    solve(client, session_state, voll=1000.0)
    capture = capture_of(client, session_state)
    for payload in lost_load_reads(client):
        assert totals(payload) == (
            pytest.approx(capture["lost_load_total_mwh"]),
            pytest.approx(capture["lost_load_cost_eur"]),
            1000.0,
        )
        assert payload["total_mwh"] > 0


def test_a_null_lost_load_total_is_not_a_certified_fact(
    short, client, session_state, checked
):
    """The checked profile rebuilds energy from the shed series; with no VoLL
    it refuses the cost rather than certifying the null as 0."""
    solve(client, session_state, voll=1000.0)
    capture = capture_of(client, session_state)
    for key in ("lost_load_total_mwh", "lost_load_cost_eur", "voll_eur_per_mwh"):
        capture.pop(key, None)
    ctx = short
    frames = checked.run(
        NumericalProvider({k: v for k, v in SHED.items() if k != "lost_load_cost"}),
        message="Check lost load.",
        checked_scope={
            "version": 1,
            "profile": "lost-load/1",
            "project_id": ctx.project_uuid,
            "entity_id": "L1",
            "retain": False,
        },
    )
    receipt = approval(frames)["receipt"]
    slots = {s["metric"]: s for s in receipt["result_slots"]}
    assert slots["lost_load_cost"]["disposition"] == "unavailable"
    assert slots["lost_load_cost"]["reason"] == "voll_missing_or_zero"
    blocks = answered(checked, frames)
    assert "lost_load_cost" not in blocks
    # The energy that IS certified is the shed series' own 15 MWh, never 0.
    assert blocks["lost_load_energy"] == pytest.approx(SHED["lost_load_energy"])
