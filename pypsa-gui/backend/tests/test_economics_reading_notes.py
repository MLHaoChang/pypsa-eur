"""
The zero-profit-by-construction caveat reaches the user, not only the model.

An extendable asset at an interior optimum earns approximately zero net
profit BY CONSTRUCTION: the LP builds until the marginal MW breaks even.
Until now that sentence lived only in `explain_investment`'s reading notes,
which the chat model sees and the user does not, so the Economics tab and
Asset Detail showed a near-zero net profit with no explanation and a reader
took it for "no return". These tests pin three things:

  * `/results/asset_economics` carries `reading_notes` with the caveat when
    any extendable asset in the payload sits at an interior optimum, and an
    empty list when none does (a bound-hitting or fixed asset is not the
    zero-profit case, and saying so would be wrong the other way);
  * the Asset Detail summary carries the same note for an interior asset;
  * there is ONE implementation of the sizing classification and ONE copy of
    the sentence, shared by the chat tool and the two surfaces, so the three
    cannot drift.

Each test fails if the caveat is removed from the surface it names, not only
if the string changes.
"""
from __future__ import annotations

import pandas as pd
import pypsa

import routers.results as R
from services import chat_tools as T
from services.asset_results.service import build_response
from services.pypsa_service import PyPSAService
from services.results import sizing
from services.results.economics_caveats import ZERO_PROFIT_BY_CONSTRUCTION


def _network(*, extendable: bool) -> pypsa.Network:
    """
    One generator, hand-dispatched, either an interior optimum or a fixed
    input. Interior: extendable, floor 0, no ceiling, optimised 100 MW, so it
    sits on neither bound. Fixed: not extendable, the same numbers.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "elec")
    n.add(
        "Generator", "G", bus="elec", p_nom=100.0, p_nom_extendable=extendable,
        marginal_cost=10.0, capital_cost=1000.0, fom_cost=20.0,
    )
    sns = n.snapshots
    n.generators["p_nom_opt"] = 100.0
    n.generators_t["p"] = pd.DataFrame({"G": [50.0, 50.0]}, index=sns)
    n.buses_t["marginal_price"] = pd.DataFrame({"elec": [40.0, 40.0]}, index=sns)
    n._objective = 0.0
    return n


def _run(n) -> dict:
    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        return R.get_asset_economics()
    finally:
        ctx.network = previous


def _summary(n) -> dict:
    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        return build_response(
            n, "Generator", "G", category="summary", metric_ids=[],
            source="lopf", from_iso=None, to_iso=None, period=None,
            mode="chronological",
        )
    finally:
        ctx.network = previous


# ── /results/asset_economics ───────────────────────────────────────────────

def test_interior_optimum_carries_the_zero_profit_note():
    """Fails if `reading_notes` is dropped from the payload or never filled."""
    out = _run(_network(extendable=True))
    assert ZERO_PROFIT_BY_CONSTRUCTION in out["reading_notes"]


def test_a_fixed_asset_carries_no_note():
    """
    Fails if the note is emitted unconditionally. A typed capacity is not an
    equilibrium; telling the reader its profit is zero "by construction"
    would invent a cause the LP never had.
    """
    out = _run(_network(extendable=False))
    assert out["reading_notes"] == []


# ── Asset Detail summary ───────────────────────────────────────────────────

def test_asset_detail_summary_carries_the_note_for_an_interior_asset():
    out = _summary(_network(extendable=True))
    assert ZERO_PROFIT_BY_CONSTRUCTION in out["reading_notes"]


def test_asset_detail_summary_is_silent_for_a_fixed_asset():
    out = _summary(_network(extendable=False))
    assert out["reading_notes"] == []


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
