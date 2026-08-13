"""
`/api/results/asset_economics` must never present a failed capital-cost
resolve as €0.00.

The defect: `get_asset_economics` wrapped `periodized_capital_costs(n, cfg)`
in a bare `try/except` that swallowed the exception and left `asset_costs =
{}`. Every downstream read is
`asset_costs.get(<class>, {}).get(<name>, {}).get("capital_cost", 0.0)`, so a
single exception anywhere in the resolver turned EVERY asset's capital cost
into 0.00 — and that propagated:

    fixed_cost_eur   -> 0
    fom_cost_eur     -> 0
    net_profit_eur   -> inflated by the whole missing capital cost
    lcoe/lcos        -> understated

Nothing was logged, and the Economics tab rendered those zeros with exactly
the same confidence as real figures. A user reading "Fixed cost €0.00" against
a 118 MW gas plant has no way to tell the difference between "this asset is
free" and "the number could not be computed".

**Decision (user's, not this test's):** show "unavailable", never a number.
The wire signal is `null` on every capital-cost-derived field; the summary is
the top-level `capital_costs_available` flag. Both, not one — the flag lets
the frontend render one banner instead of forty identical em-dashes, and the
nulls mean a consumer that ignores the flag still cannot print a zero.

Each test below names the production change that makes it fail; every one of
them was verified by making that change, not by reading the code.
"""
from __future__ import annotations

import logging

import pandas as pd
import pypsa
import pytest

import routers.results as R
from services.pypsa_service import PyPSAService

# The five fields that cannot be computed without the resolver. `fom_cost_eur`
# is derived from the component's own `fom_cost` column rather than from
# `asset_costs`, but it is published as the FOM breakdown OF `fixed_cost_eur` —
# showing it beside an unavailable fixed cost invites the reader to treat it as
# the whole annualised cost, so it goes dark with the rest.
CAPITAL_DERIVED_TOTAL = (
    "fixed_cost_eur", "fom_cost_eur", "net_profit_eur",
)
# Fields that owe nothing to capital cost and must keep their real values —
# nulling the whole row would be just as dishonest in the other direction.
INDEPENDENT = ("revenue_eur", "vom_cost_eur", "energy_mwh")


def _boom(*_args, **_kwargs):
    """Stand-in for a resolver that blows up mid-way, as the real one can."""
    raise RuntimeError("annuity lookup exploded")


def _flat_network() -> pypsa.Network:
    """
    One generator + one storage unit, two snapshots, hand-dispatched.

    Every quantity is chosen so the numbers can be written down rather than
    read back off a solver:

        price at `elec`  = 40 EUR/MWh both snapshots
        G: p = [50, 50]  marginal_cost 10  capital_cost 1000  fom_cost 20
           p_nom_opt 100
             energy  = 100 MWh      revenue = 4_000     vom = 1_000
             fixed   = 1000 x 100 = 100_000             fom = 20 x 100 = 2_000
             lcoe    = (100_000 + 1_000) / 100 = 1_010 EUR/MWh
        B: p = [-20, +20] (charge then discharge), capital_cost 500,
           p_nom_opt 50
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "elec")
    n.add(
        "Generator", "G", bus="elec", p_nom=100.0,
        marginal_cost=10.0, capital_cost=1000.0, fom_cost=20.0,
    )
    n.add(
        "StorageUnit", "B", bus="elec", p_nom=50.0, max_hours=4.0,
        marginal_cost=5.0, capital_cost=500.0,
    )
    sns = n.snapshots
    n.generators["p_nom_opt"] = 100.0
    n.generators_t["p"] = pd.DataFrame({"G": [50.0, 50.0]}, index=sns)
    n.storage_units["p_nom_opt"] = 50.0
    n.storage_units_t["p"] = pd.DataFrame({"B": [-20.0, 20.0]}, index=sns)
    n.buses_t["marginal_price"] = pd.DataFrame({"elec": [40.0, 40.0]}, index=sns)
    # `is_solved` derives from the objective, so mark it the way PyPSA does.
    n._objective = 0.0
    return n


def _multi_period_network() -> pypsa.Network:
    """
    The same shape across two investment periods, so `by_period` is populated.

    `by_period` is a separate emission path from the top-level totals — it
    builds its own dict literal per period — so nulling one and not the other
    is an easy and completely silent mistake. It gets its own coverage.
    """
    n = pypsa.Network()
    hours = pd.date_range("2030-01-01", periods=2, freq="h")
    mi = pd.MultiIndex.from_product(
        [[2030, 2040], hours], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2030, 2040]
    n.add("Bus", "elec")
    n.add(
        "Generator", "G", bus="elec", p_nom=100.0,
        marginal_cost=10.0, capital_cost=1000.0, fom_cost=20.0,
    )
    n.generators["p_nom_opt"] = 100.0
    n.generators_t["p"] = pd.DataFrame({"G": [50.0] * len(mi)}, index=mi)
    n.buses_t["marginal_price"] = pd.DataFrame({"elec": [40.0] * len(mi)}, index=mi)
    n._objective = 0.0
    return n


def _run(n) -> dict:
    """Call the endpoint function against `n`, restoring the prior network."""
    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        return R.get_asset_economics()
    finally:
        ctx.network = previous


@pytest.fixture()
def broken(monkeypatch) -> dict:
    """Response with the capital-cost resolver raising."""
    monkeypatch.setattr(R, "periodized_capital_costs", _boom)
    return _run(_flat_network())


@pytest.fixture()
def healthy() -> dict:
    """Response with the resolver working normally."""
    return _run(_flat_network())


# ── The flag ──────────────────────────────────────────────────────────────

def test_a_failed_resolve_is_flagged_in_the_response(broken):
    """
    Fails if: the `capital_costs_available` key is dropped from the response
    dict, or hardcoded to True.

    The flag is what lets the frontend say "capital costs are unavailable"
    once, at the top of the tab, instead of leaving the reader to infer it
    from forty blank cells.
    """
    assert broken["capital_costs_available"] is False


def test_the_happy_path_still_reports_the_flag_true_with_real_numbers(healthy):
    """
    Fails if: the flag is hardcoded False, or if the null-ing helper fires
    unconditionally rather than only on resolver failure.

    Guards the direction the failure path cannot: a fix that reports every
    network as unavailable would pass every other test in this file.
    """
    assert healthy["capital_costs_available"] is True

    gen = healthy["generators"][0]
    assert gen["fixed_cost_eur"] == pytest.approx(100_000.0)
    assert gen["fom_cost_eur"] == pytest.approx(2_000.0)
    assert gen["net_profit_eur"] == pytest.approx(4_000.0 - 100_000.0 - 1_000.0)
    assert gen["lcoe_eur_per_mwh"] == pytest.approx(1_010.0)

    su = healthy["storage_units"][0]
    assert su["fixed_cost_eur"] == pytest.approx(25_000.0)
    assert su["lcos_eur_per_mwh"] is not None


# ── The wire signal: null, never 0.0 ──────────────────────────────────────

def test_capital_derived_generator_fields_are_null_not_zero(broken):
    """
    Fails if: `_capital_derived` is reverted to `_safe_finite` for any of these
    fields.

    This is the whole defect in one assertion. Under the old code every value
    here is a float — 0.0 for fixed/fom, and a net profit of +3_000 that is
    wrong by the entire missing 100_000 of CAPEX. `0.0 is None` is False, so
    the revert fails here rather than passing quietly.
    """
    gen = broken["generators"][0]
    for field in CAPITAL_DERIVED_TOTAL:
        assert gen[field] is None, (
            f"generator.{field} came back as {gen[field]!r} — a failed capital-cost "
            f"resolve must be null on the wire, never a number the UI can format"
        )
    assert gen["lcoe_eur_per_mwh"] is None


def test_capital_derived_storage_fields_are_null_not_zero(broken):
    """
    Fails if: the storage_units block keeps `_safe_finite` on its cost fields.

    Storage is a separate emission path from generators (LCOS instead of LCOE,
    net profit netting charge cost) and was nulled in a separate edit, so a
    partial fix that only covered generators is caught here.
    """
    su = broken["storage_units"][0]
    for field in CAPITAL_DERIVED_TOTAL:
        assert su[field] is None, f"storage_unit.{field} = {su[field]!r}"
    assert su["lcos_eur_per_mwh"] is None


def test_fields_that_do_not_depend_on_capital_cost_keep_real_values(broken):
    """
    Fails if: the fix nulls the whole row instead of the capital-derived
    subset — e.g. by returning early, or by routing revenue/VOM through the
    same helper.

    Revenue and VOM are computed entirely from dispatch and bus prices. The
    resolver's failure says nothing about them, and blanking them would throw
    away good data and hide a working half of the tab.
    """
    gen = broken["generators"][0]
    assert gen["revenue_eur"] == pytest.approx(4_000.0)
    assert gen["vom_cost_eur"] == pytest.approx(1_000.0)
    assert gen["energy_mwh"] == pytest.approx(100.0)
    for field in INDEPENDENT:
        assert gen[field] is not None

    su = broken["storage_units"][0]
    assert su["discharge_mwh"] == pytest.approx(20.0)
    assert su["charge_mwh"] == pytest.approx(20.0)
    assert su["charge_cost_eur"] == pytest.approx(800.0)
    assert su["vom_cost_eur"] == pytest.approx(100.0)


def test_by_period_entries_are_nulled_too(monkeypatch):
    """
    Fails if: only the top-level row dicts are nulled and the `by_period`
    literals keep `_safe_finite`.

    The per-period drill-down is a second, independently-written emission of
    the same five fields. On a multi-period run it is what the user actually
    reads when they expand an asset, so a fix that stops at the top level
    leaves the zeros exactly where they do the most damage.
    """
    monkeypatch.setattr(R, "periodized_capital_costs", _boom)
    payload = _run(_multi_period_network())

    assert payload["is_multi_period"] is True
    assert payload["capital_costs_available"] is False

    gen = payload["generators"][0]
    assert len(gen["by_period"]) == 2, "fixture should produce two periods"
    for entry in gen["by_period"]:
        for field in CAPITAL_DERIVED_TOTAL:
            assert entry[field] is None, (
                f"by_period[{entry['period']}].{field} = {entry[field]!r}"
            )
        assert entry["lcoe_eur_per_mwh"] is None
        # ...while the period's own dispatch figures survive.
        assert entry["revenue_eur"] == pytest.approx(2 * 50.0 * 40.0)
        assert entry["vom_cost_eur"] == pytest.approx(2 * 50.0 * 10.0)


def test_by_period_keeps_real_numbers_on_the_happy_path():
    """
    Fails if: the by_period null-ing is unconditional rather than gated on the
    resolver having failed.

    The multi-period counterpart of the healthy-path test above — without it,
    a helper that always returns None would satisfy every failure-path
    assertion in this file.
    """
    payload = _run(_multi_period_network())

    assert payload["capital_costs_available"] is True
    gen = payload["generators"][0]
    assert len(gen["by_period"]) == 2
    for entry in gen["by_period"]:
        assert entry["fixed_cost_eur"] is not None
        assert entry["fixed_cost_eur"] > 0
        assert entry["lcoe_eur_per_mwh"] is not None


# ── The log ───────────────────────────────────────────────────────────────

def test_the_swallowed_exception_is_logged_with_a_traceback(monkeypatch, caplog):
    """
    Fails if: the `except` branch drops `logger.exception(...)` (including a
    downgrade to `logger.debug`, which would not reach `pypsa-gui.log`).

    The original `except Exception: asset_costs = {}` produced no record
    anywhere. Whatever the UI does about it, someone reading the log after a
    support report needs to see WHICH resolver failed and why — so this
    asserts the level, the logger, and the presence of exception info rather
    than just "something was logged".
    """
    monkeypatch.setattr(R, "periodized_capital_costs", _boom)

    with caplog.at_level(logging.ERROR, logger="pypsa_gui.results"):
        payload = _run(_flat_network())

    records = [
        r for r in caplog.records
        if r.name == "pypsa_gui.results" and r.levelno >= logging.ERROR
    ]
    assert records, "the capital-cost resolver failed and nothing was logged"
    record = records[0]
    assert record.exc_info is not None, (
        "logged without exception info — use logger.exception(), not "
        "logger.error(), so the traceback reaches pypsa-gui.log"
    )
    assert "annuity lookup exploded" in caplog.text
    # And the response still degrades gracefully rather than 500-ing.
    assert payload["capital_costs_available"] is False


def test_nothing_is_logged_on_the_happy_path(caplog):
    """
    Fails if: the log line is emitted unconditionally (outside the `except`).

    A warning that fires on every solved network is a warning users learn to
    ignore, which erases its value for the run that actually failed.
    """
    with caplog.at_level(logging.ERROR, logger="pypsa_gui.results"):
        _run(_flat_network())

    assert not [
        r for r in caplog.records
        if r.name == "pypsa_gui.results" and r.levelno >= logging.ERROR
    ]
