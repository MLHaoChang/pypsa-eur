"""
The zero-profit-by-construction caveat reaches the user, not only the model.

An extendable asset at an interior optimum earns approximately zero net
profit BY CONSTRUCTION: the LP builds until the marginal MW breaks even.
Until now that sentence lived only in `explain_investment`'s reading notes,
which the chat model sees and the user does not, so the Economics tab and
Asset Detail showed a near-zero net profit with no explanation and a reader
took it for "no return". These tests pin:

  * `/results/asset_economics` carries `reading_notes` with the caveat when
    any extendable asset in the payload sits at an interior optimum, and an
    empty list when none does. A fixed asset, an asset the LP stopped at its
    ceiling and an asset it did not build are NOT the zero-profit case, and
    saying so would be wrong the other way;
  * the Asset Detail summary carries the same note for an interior asset and
    stays silent for the three negatives;
  * "solved" means fresh dispatch, the predicate `explain_investment` uses —
    not the presence of a `p_nom_opt` column, which PyPSA carries on every
    network (default 0) and which survives `clear_dispatch` after an edit;
  * there is ONE implementation of the sizing classification and ONE copy of
    the sentence, shared by the chat tool and the two surfaces.

Each test fails if the caveat is removed from the surface it names, or if
the note is emitted for any extendable asset regardless of where it sits.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.results as R
from services import chat_tools as T
from services.asset_results.service import build_response
from services.dispatch_status import clear_dispatch
from services.pypsa_service import PyPSAService
from services.results import sizing
from services.results.economics_caveats import ZERO_PROFIT_BY_CONSTRUCTION


def _network(
    *, extendable: bool = True, p_nom_opt: float = 100.0,
    p_nom_min: float = 0.0, p_nom_max: float | None = None,
    dispatched: bool = True,
) -> pypsa.Network:
    """
    One generator, hand-dispatched. Defaults give an interior optimum:
    extendable, floor 0, no ceiling, optimised 100 MW. The keyword arguments
    move it onto a bound, make it a fixed input, or leave it unsolved.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "elec")
    kw: dict = {}
    if p_nom_max is not None:
        kw["p_nom_max"] = p_nom_max
    n.add(
        "Generator", "G", bus="elec", p_nom=100.0, p_nom_extendable=extendable,
        p_nom_min=p_nom_min, marginal_cost=10.0, capital_cost=1000.0,
        fom_cost=20.0, **kw,
    )
    sns = n.snapshots
    n.generators["p_nom_opt"] = p_nom_opt
    if dispatched:
        n.generators_t["p"] = pd.DataFrame({"G": [50.0, 50.0]}, index=sns)
        n.buses_t["marginal_price"] = pd.DataFrame({"elec": [40.0, 40.0]}, index=sns)
        n._objective = 0.0
    return n


def _with_network(n, fn):
    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        return fn()
    finally:
        ctx.network = previous


def _economics(n) -> dict:
    return _with_network(n, R.get_asset_economics)


def _summary(n) -> dict:
    return _with_network(n, lambda: build_response(
        n, "Generator", "G", category="summary", metric_ids=[],
        source="lopf", from_iso=None, to_iso=None, period=None,
        mode="chronological",
    ))


# ── The positive case, on both surfaces ────────────────────────────────────

def test_interior_optimum_carries_the_zero_profit_note():
    """Fails if `reading_notes` is dropped from the payload or never filled."""
    assert ZERO_PROFIT_BY_CONSTRUCTION in _economics(_network())["reading_notes"]


def test_asset_detail_summary_carries_the_note_for_an_interior_asset():
    assert ZERO_PROFIT_BY_CONSTRUCTION in _summary(_network())["reading_notes"]


# ── The negatives: fixed, at the ceiling, not built ───────────────────────
# A mutation that emits the note for ANY extendable asset passes the
# fixed-asset case and fails the other two; that is why all three exist.

NEGATIVES = {
    "fixed_input": dict(extendable=False),
    "at_upper_bound": dict(p_nom_max=100.0, p_nom_opt=100.0),
    "not_built": dict(p_nom_opt=0.0),
}


@pytest.mark.parametrize("case", sorted(NEGATIVES))
def test_economics_is_silent_when_the_asset_is_not_an_interior_optimum(case):
    assert _economics(_network(**NEGATIVES[case]))["reading_notes"] == []


@pytest.mark.parametrize("case", sorted(NEGATIVES))
def test_asset_detail_is_silent_when_the_asset_is_not_an_interior_optimum(case):
    assert _summary(_network(**NEGATIVES[case]))["reading_notes"] == []


# ── "Solved" is fresh dispatch, not a `p_nom_opt` column ──────────────────
# The Asset Detail summary has no dispatch-readiness gate of its own, so
# these run on that surface, where the wrong rule was reachable.

def test_an_unsolved_network_carries_no_note_even_with_a_positive_floor():
    """
    PyPSA gives every network a `p_nom_opt` column (default 0). With a
    positive floor and no dispatch, a column-based "solved" rule read this
    as an interior optimum. `explain_investment` calls it `not_solved`.
    """
    n = _network(p_nom_min=5.0, p_nom_opt=0.0, dispatched=False)
    assert _summary(n)["reading_notes"] == []


def test_a_dispatch_cleared_network_carries_no_note():
    """
    Every `/api/network/*` edit clears the dispatch tables but leaves the
    stale `p_nom_opt` behind. The note must go with the dispatch.
    """
    n = _network()
    assert _summary(n)["reading_notes"] != []
    clear_dispatch(n)
    assert _summary(n)["reading_notes"] == []


# ── One implementation ─────────────────────────────────────────────────────

def test_the_chat_tool_and_the_surfaces_share_one_classifier_and_one_sentence():
    """
    Fails if `chat_tools` keeps its own `_sizing` or its own copy of the
    sentence. Two copies drift; the drift is exactly the defect this file
    exists for.
    """
    assert T._sizing is sizing.classify_sizing
    notes = T._reading_notes(
        {"binding_constraint": "interior"}, co2=[], buses=["elec"],
    )
    assert ZERO_PROFIT_BY_CONSTRUCTION in notes
