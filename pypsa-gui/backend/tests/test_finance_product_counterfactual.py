"""
The B investment case (grid-to-process ticket 11): a product Load served by the owner's process takes the site-today
baseline re-solve as its counterfactual.

On `eh_concentrator_staged` the concentrate is made by the owner's mills, so there is nothing to buy in. The case
compares the plan with the site as it stands today (the same process, the existing connection, no candidates). The
shortfall slack's ledgered lost-production value (never VoLL) is on both sides once, so the lost production nets to
one delta. The report splits that delta out of the incremental operating cash, and states the lost product behind it.

The live checks plan 2027 alone (about a minute each, as in `test_eh_concentrator_staged`). The case's value flows and
CODs are trimmed to that horizon's rows: mill 3 and flotation 3 (built 2035) are dropped from the network.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys
import types

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_concentrator as C  # noqa: E402
import eh_templates as T  # noqa: E402

from tests import test_eh_concentrator_staged as S  # noqa: E402

TID = "eh_concentrator_staged"
YEAR = C.YEARS[0]


def _first_year_only(n) -> None:
    """Drop what is built after 2027: a one-year build has no period for it."""
    for comp in ("Link", "Generator", "Store", "StorageUnit"):
        df = n.static(comp)
        if "build_year" in df.columns:
            n.remove(comp, [a for a in df.index if (df.at[a, "build_year"] or 0) > YEAR])


def _bare(n) -> None:
    S._no_candidates({"pv", "bess", "pile"})(n)
    _first_year_only(n)


def _cfg(n):
    """The template's solver config, its value flows and CODs trimmed to the rows of `n`."""
    from services.solver_service import SolverConfig

    raw = T.solver_config(TID)
    raw["finance"]["cod_by_asset"] = {a: d for a, d in raw["finance"]["cod_by_asset"].items()
                                      if int(d[:4]) <= YEAR}
    vf = raw["commercial"]["value_flows"]
    vf["asset_owners"] = [a for a in vf["asset_owners"] if a["asset_id"] in n.static(a["component"]).index]
    return SolverConfig(**raw)


_CASES: dict[str, tuple] = {}


def _case(key: str, *, baseline: bool = True):
    """(network, case, result) for the 2027 plan `key` ('bare': every candidate off, 'all': at pack price)."""
    ck = (key, baseline)
    if ck not in _CASES:
        import routers.results as R
        from models.finance import FinanceInputs
        from services.finance.engine import run_case
        from services.results import baseline as Bl
        from services.results.finance_case import baseline_plan, build_finance_case

        n, state, _ = S._plan(f"g2p11-{key}", years=(YEAR,), edit=_bare if key == "bare" else _first_year_only)
        cfg = _cfg(n)
        base = None
        if baseline:
            plan = baseline_plan(n, cfg)
            assert plan is not None and plan.needed
            Bl.clear_cache()
            base = Bl.solve_baseline(n, cfg, plan)
            Bl.clear_cache()
        case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance), result_df=R._result_df,
                                  lost_load=state.get("last_lost_load"), baseline=base)
        _CASES[ck] = (n, case, run_case(case))
    return _CASES[ck]


def _block(case, result) -> dict:
    from services.finance.report import _counterfactual_block

    return _counterfactual_block(result, case)


# ── the template: the PoC meter is the connection, not an asset ─────────────────────────────────────────────────────

def test_the_poc_meter_is_untyped_and_skipped_as_a_meter():
    """`grid_import` is not typed as existing (overnight 0), so the case skips it as an uncosted meter instead of
    refusing `cod_missing` on it (it has no COD: it is the connection)."""
    from services.commercial import lp_bindings as L
    from services.commercial import participants as P
    from services.results.finance_case import _resolve_owner, _uncosted_meter_links

    n = T.BUILDERS[TID]()
    assert np.isnan(n.links.at["grid_import", "overnight_cost"])
    cfg = T.solver_config(TID)
    parsed = L._parse(cfg["commercial"])
    _owner, owned = _resolve_owner(P.parse_value_flows(parsed.value_flows), None)
    assert ("Link", "grid_import") in owned
    assert _uncosted_meter_links(n, parsed, owned) == ["grid_import"]
    assert "grid_import" not in cfg["finance"]["cod_by_asset"]


# ── the connection envelope the network carries but the agreement does not ──────────────────────────────────────────

def test_the_envelope_flag_fires_for_2027_only():
    """2027's PoC `p_max_pu` dips to the 15 MW envelope (it varies within the period); 2030 and 2035 sit at 1."""
    from services.results.finance_case import _envelope_flags

    n = T.BUILDERS[TID]()
    parsed = types.SimpleNamespace(poc_link="grid_import", connection=None)
    assert _envelope_flags(n, parsed) == [f"connection_envelope_network_only:{YEAR}"]
    firm = types.SimpleNamespace(poc_link="grid_import", connection=types.SimpleNamespace(kind="firm"))
    assert _envelope_flags(n, firm) == [f"connection_envelope_network_only:{YEAR}"]


def test_the_envelope_flag_is_quiet_for_a_constant_cap_or_a_non_firm_agreement():
    from services.results.finance_case import _envelope_flags

    n = T.BUILDERS[TID]()
    non_firm = types.SimpleNamespace(poc_link="grid_import",
                                     connection=types.SimpleNamespace(kind="non_firm_dynamic"))
    assert _envelope_flags(n, non_firm) == []
    # A constant staged cap (2027's 25 MW of 35 all year) is energisation, not an envelope.
    pu = n.links_t.p_max_pu["grid_import"]
    at = n.snapshots.get_level_values(0) == YEAR
    n.links_t.p_max_pu.loc[at, "grid_import"] = float(pu[at].max())
    assert _envelope_flags(n, types.SimpleNamespace(poc_link="grid_import", connection=None)) == []


# ── the case against the site today ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.live_solve
def test_with_no_candidates_the_case_computes_at_npv_0_and_no_lost_tonne_delta(reset_backend):
    n, case, result = _case("bare")
    assert "counterfactual_basis:site_today" in case.flags
    assert not [f for f in case.flags if f.startswith("counterfactual_not_established")]
    assert result.metrics["project_pre_tax_npv"] == pytest.approx(0.0, abs=0.5)
    block = _block(case, result)
    (row,) = block["lost_product"]
    assert row["counterfactual_per_year"] > 1.0                  # the site today loses concentrate in 2027 ...
    assert row["delta_per_year"] == pytest.approx(0.0, abs=1e-6)  # ... and so does the plan with nothing to build
    assert np.allclose(block["incremental_split"]["lost_production"], 0.0, atol=1e-6)


@pytest.mark.live_solve
def test_the_split_reconciles_to_the_incremental_cash_per_year(reset_backend):
    n, case, result = _case("all")
    assert not [f for f in case.flags if f.startswith("counterfactual_not_established")]
    assert result.metrics["project_pre_tax_npv"] is not None
    block = _block(case, result)
    sp = {k: np.asarray(v) for k, v in block["incremental_split"].items() if k != "energy_bill_streams"}
    np.testing.assert_allclose(sp["energy_bill"] + sp["lost_production"] + sp["other"],
                               result.op_incremental["net"], rtol=0, atol=1e-6)
    assert np.abs(sp["energy_bill"]).max() > 1.0                 # PV cuts the bill ...
    assert np.abs(sp["lost_production"]).max() > 1.0             # ... and the 2027 lost concentrate
    assert set(block["incremental_split"]["energy_bill_streams"]) >= {"energy_import", "network_energy"}


@pytest.mark.live_solve
def test_the_lost_product_is_per_year_of_its_period_in_the_bus_unit(reset_backend):
    """Hand value: Σ p · snapshot_weightings.generators over 2027's hours (one year), on each side. The period stands
    for 18 years (2027 to the horizon end), so Σ p · snapshot_weights would be 18 × that."""
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan

    n, case, result = _case("all")
    (row,) = _block(case, result)["lost_product"]
    years = C.HORIZON_END - YEAR
    assert row["unit"] == "t" and row["years"] == years and years != 1
    w = n.snapshot_weightings.generators.to_numpy()
    project = float((n.generators_t.p["__lost_concentrate"].to_numpy() * w).sum())
    assert row["project_per_year"] == pytest.approx(project, abs=1e-6)
    Bl.clear_cache()
    base = Bl.solve_baseline(n, _cfg(n), baseline_plan(n, _cfg(n))).network
    Bl.clear_cache()
    hand = float((base.generators_t.p["__lost_concentrate"].to_numpy() * w).sum())
    assert row["counterfactual_per_year"] == pytest.approx(hand, rel=1e-9)
    assert row["delta_per_year"] < -1.0                          # the plan loses less than the site today
    # The money is that quantity at the ledgered value, in the first operating year (no escalation yet).
    lp = np.asarray(_block(case, result)["incremental_split"]["lost_production"])
    first = int(np.flatnonzero(np.abs(lp) > 0)[0])
    assert lp[first] == pytest.approx(-row["delta_per_year"] * C.LOST_EUR_PER_T, rel=1e-6)


@pytest.mark.live_solve
def test_a_shortfall_only_change_moves_the_lost_production_line_alone(reset_backend):
    """The same case with the site today's lost-production lines doubled: the energy bill does not move."""
    from services.finance.engine import run_case

    n, case, result = _case("all")

    def doubled(t):
        return dataclasses.replace(t, lines=tuple(
            dataclasses.replace(ln, amount=2.0 * ln.amount) if ln.stream == "lost_production" else ln
            for ln in t.lines))

    other = dataclasses.replace(case, counterfactual=tuple(doubled(t) for t in case.counterfactual))
    a, b = _block(case, result)["incremental_split"], _block(other, run_case(other))["incremental_split"]
    np.testing.assert_allclose(b["energy_bill"], a["energy_bill"], rtol=0, atol=1e-9)
    np.testing.assert_allclose(b["other"], a["other"], rtol=0, atol=1e-6)
    moved = np.asarray(b["lost_production"]) - np.asarray(a["lost_production"])
    assert np.abs(moved).max() > 1.0
    np.testing.assert_allclose(moved, np.asarray(b["net"]) - np.asarray(a["net"]), rtol=0, atol=1e-6)


@pytest.mark.live_solve
def test_the_envelope_flag_reaches_the_notes_and_leaves_the_npv(reset_backend):
    from services.finance.engine import run_case

    n, case, result = _case("all")
    flag = f"connection_envelope_network_only:{YEAR}"
    assert flag in case.flags
    assert any("non-firm envelope in 2027" in t for t in _block(case, result)["notes"])
    quiet = dataclasses.replace(case, flags=tuple(f for f in case.flags if f != flag))
    assert run_case(quiet).metrics["project_pre_tax_npv"] == result.metrics["project_pre_tax_npv"]


@pytest.mark.live_solve
def test_without_a_baseline_the_product_load_blocks_with_its_own_reason(reset_backend):
    """The analytic basis has no re-solve: the product Load is named, and the heat-load rule is not what fires."""
    n, case, result = _case("all", baseline=False)
    assert "counterfactual_not_established:product_load_needs_baseline" in case.flags
    assert "counterfactual_product_loads:shipments" in case.flags
    assert "counterfactual_not_established:owner_conversion_load" not in case.flags
    assert result.metrics["project_pre_tax_npv"] is None
