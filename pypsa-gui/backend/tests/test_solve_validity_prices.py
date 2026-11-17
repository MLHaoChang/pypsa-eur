"""bus-price/1 on a MIP (assistant-assurance 10, S2, GK B G2d).

On master's solve path HiGHS returns no duals for a MIP and PyPSA writes 0.0 to
every bus price, so a committable network's prices must never be certified.
"""

import pandas as pd
import pytest

from harness import solve_validity
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_result_profiles_assurance import PRICE, ask, slots, solve
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "bus-price/1")


@pytest.fixture
def priced(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.add("Generator", "G2", bus="B1", p_nom=20, marginal_cost=30)
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 25]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [1, 3]
    return ctx


def test_a_committable_network_never_certifies_its_zero_prices(priced, checked):
    priced.network.generators.loc["G2", "committable"] = True
    ctx = solve(priced, voll=1000)
    assert (ctx.network.buses_t.marginal_price["B1"] == 0.0).all()
    frames = ask(
        checked,
        ctx,
        "bus-price/1",
        "B1",
        {**PRICE, "mean_price": 0, "min_price": 0, "max_price": 0},
    )
    got = slots(frames)
    assert {m: (s["disposition"], s.get("reason")) for m, s in got.items()} == {
        m: ("unavailable", solve_validity.DUALS_NOT_MARGINAL) for m in PRICE
    }
    assert approval(frames)["receipt"]["request_complete"] is False


def test_the_same_network_without_commitment_still_answers(priced, checked):
    ctx = solve(priced, voll=1000)
    got = slots(ask(checked, ctx, "bus-price/1", "B1", PRICE))
    assert {s["disposition"] for s in got.values()} == {"answered"}


def test_a_stale_mip_solve_still_reads_unverified(priced, checked):
    priced.network.generators.loc["G2", "committable"] = True
    ctx = solve(priced, voll=1000)
    ctx.network.loads.at["L1", "p_set"] = (
        9  # edit after the solve: the fingerprint no longer matches
    )
    got = slots(ask(checked, ctx, "bus-price/1", "B1", PRICE))
    assert {s.get("reason") for s in got.values()} == {"unverified_solve"}
