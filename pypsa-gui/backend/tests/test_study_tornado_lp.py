"""
S6 on real LP solves of the site golden fixture (plan S6 "Acceptance";
review v1 B5; gate S5 carries). Few solves, each 10-15 s:

* the constructed MARGINAL case — the test that could never pass under v1:
  a user's storage quote of 400 EUR/kWh (outside the library's band, so the
  range is re-centred on it) sizes a 2-hour battery with a small positive
  NPV; at the high storage-cost bound, with the size HELD FIXED, the NPV is
  negative. Re-optimising the size instead keeps it >= 0 by construction
  (the LP builds nothing), so `marginal` is never emitted;
* the `bess_pv` battery attribution against a PV-only reference, whose PV
  rows cancel exactly;
* the discount-rate bar (no solve), which must reach the ledger AND the
  solver config.

The solves go through the production LP pieces in process (the periodized
cost fill and `_wrap_with_demand_charge`), injected as the tornado's `solve`;
the queue and the throw-away forks are `test_study_tornado_routes.py`'s.
"""
from __future__ import annotations

import pytest

from services.study import findings as F
from services.study import packs, proforma
from services.study import ledger as L
from services.study import questions as Q
from tests.golden import site_fixture as sf


def lp_solve(net, cfg, _variant_id):
    from services.solver.objective import _wrap_with_demand_charge
    from services.solver_service import with_periodized_cost_defaults

    with with_periodized_cost_defaults(net, cfg):
        status = net.optimize(solver_name="highs",
                              extra_functionality=_wrap_with_demand_charge(net, None, cfg))
    assert status == ("ok", "optimal"), status
    return net


def _question(*drivers):
    return Q.BESS_AT_SITE.model_copy(update={"key_drivers": list(drivers)})


def _ctx(ledger, options: dict, question) -> F.TornadoContext:
    tariff = sf.site_tariff(ledger)
    nb, _ = sf.solve_site_option("none")
    return F.TornadoContext(
        study_id=sf.SITE_STUDY_ID, question=question, intake=sf.site_intake(),
        ledger=ledger, library=sf.site_library(), tariff=tariff, baseline_network=nb,
        baseline_bill=F._bill_of(nb, tariff), fidelity="full_study",
        options={oid: F.OptionInput(oid, n, F._bill_of(n, tariff))
                 for oid, n in options.items()})


@pytest.fixture(scope="module")
def dear_storage():
    """The 2-hour battery solved at a user's storage quote of 400 EUR/kWh."""
    ledger = L.apply_user_row(sf.site_ledger(), "battery_storage_eur_per_kwh", 400.0,
                              unit="EUR/kWh", changed_by="test")
    tariff = sf.site_tariff(ledger)
    n = packs.build_site_network(sf.site_intake(), ledger, "bess_2h", library=sf.site_library())
    lp_solve(n, packs.option_solver_config(ledger, tariff), "centre")
    return ledger, n


def test_the_sign_flips_at_the_high_storage_cost_bound_so_the_verdict_is_marginal(dear_storage):
    ledger, n = dear_storage
    ctx = _ctx(ledger, {"bess_2h": n}, _question("battery_storage_eur_per_kwh"))
    calls = []
    out = F.run_tornado(ctx, lambda net, cfg, vid: calls.append(vid) or lp_solve(net, cfg, vid))
    [att] = out.attributions
    assert att.status == "ok" and att.battery_p_nom_mw > 0.5
    assert 0.0 < att.battery_npv < 1e5, att.battery_npv
    assert out.robustness.status == "ok", out.robustness
    [row] = out.robustness.tornado
    assert row.key == "battery_storage_eur_per_kwh"
    # 400 lies outside the library's band: the band is re-centred on it.
    assert "range_recentred_on_user_value" in row.notes
    assert row.low_value < 400.0 < row.high_value
    assert row.npv_low > att.battery_npv > 0.0 > row.npv_high
    # Sizes held fixed: a cost row solves nothing.
    assert calls == [] and out.robustness.solves_charged == 0
    v = F.verdict(out.attributions, out.robustness, option_networks={"bess_2h": n},
                  fidelity="full_study")
    assert v.class_ == "marginal", v
    assert v.drivers == ["battery_storage_eur_per_kwh"]


def test_a_centre_bound_reproduces_the_centre_npv(dear_storage):
    """The variant path, at the centre value, is the centre case (no drift)."""
    ledger, n = dear_storage
    ctx = _ctx(ledger, {"bess_2h": n}, _question())
    [att] = F.run_tornado(ctx, lp_solve).attributions
    tgt = F._Target("bess_2h", n, ctx.options["bess_2h"].bill)
    for key in ("battery_storage_eur_per_kwh", "discount_rate"):
        centre = next(r.value for r in ledger.rows if r.key == key)
        got = F._battery_npv_at(ctx, tgt, key, centre, lp_solve, "c")
        assert got == pytest.approx(att.battery_npv, rel=1e-9), key


def test_the_bess_pv_battery_is_valued_against_a_pv_only_reference():
    n, _cfg = sf.solve_site_option("bess_pv_2h")
    ledger = sf.site_ledger()
    ctx = _ctx(ledger, {"bess_pv_2h": n}, _question())
    calls = []
    out = F.run_tornado(ctx, lambda net, cfg, vid: calls.append((vid, net)) or lp_solve(net, cfg, vid))
    [(vid, ref_net)] = calls
    assert vid == "ref-bess_pv_2h"
    # The reference OMITS the battery (BC-1) and FIXES PV at the option's size.
    assert "battery" not in ref_net.storage_units.index
    assert not bool(ref_net.generators.at["pv", "p_nom_extendable"])
    assert ref_net.generators.at["pv", "p_nom"] == pytest.approx(F.pv_size(n))
    [att] = out.attributions
    case, ref = out.cases["bess_pv_2h"], out.reference_cases["bess_pv_2h"]
    assert att.method == "battery_removed_same_pv" and att.status == "ok"
    assert att.battery_npv == pytest.approx(case.kpis.npv - ref.kpis.npv, rel=1e-12)
    # The PV rows cancel exactly: what is left is the battery's own rows.
    p_bat = F.battery_size(n)
    up = packs.battery_upfront_eur_per_mw(ledger, 2.0)
    assert case.years[0].capex - ref.years[0].capex == pytest.approx(up["total"] * p_bat, rel=1e-12)
    assert case.upfront_gaps[0].asset == "battery"
    pv_capex = case.kpis.capex_total - up["total"] * p_bat
    assert ref.kpis.capex_total == pytest.approx(pv_capex, rel=1e-12)
    assert case.years[10].replacements - ref.years[10].replacements == pytest.approx(
        up["inverter"] * p_bat, rel=1e-12)
    fom_bat = packs.battery_fom_eur_per_mw(ledger) * p_bat
    assert case.years[1].opex_fixed - ref.years[1].opex_fixed == pytest.approx(fom_bat, rel=1e-6)
    # By construction (the reference is feasible for the option's LP) and
    # smaller than the option's value, most of which is PV's.
    assert 0.0 <= att.battery_npv < case.kpis.npv
    v = F.verdict(out.attributions, out.robustness, option_networks={"bess_pv_2h": n},
                  fidelity="full_study")
    assert v.class_ == "recommended"
    assert "battery_value_against_pv_only_reference" in v.disclosures
    assert "{{pv_p_nom_mw}}" in v.sentence and "pv_p_nom_mw" in v.facts


def test_the_discount_rate_bar_reaches_the_ledger_and_the_solver_config():
    n, cfg = sf.solve_site_option("bess_2h")
    ledger = sf.site_ledger()
    ctx = _ctx(ledger, {"bess_2h": n}, _question("discount_rate"))
    out = F.run_tornado(ctx, lambda *_a: pytest.fail("a rate bar solves nothing"))
    assert out.robustness.status == "ok", out.robustness
    [row] = out.robustness.tornado
    assert (row.low_value, row.high_value) == (0.049, 0.091) and row.notes == ()
    assert row.evaluation == "rate_only"
    assert row.npv_low > out.robustness.npv_centre > row.npv_high > 0.0
    # The rate in the ledger alone is refused by the pro forma.
    variant = F._with_value(ledger, "discount_rate", 0.091)
    with pytest.raises(proforma.ProformaError) as exc:
        proforma.build_investment_case(
            n, cfg, None, variant, {"baseline": ctx.baseline_bill,
                                    "option": ctx.options["bess_2h"].bill},
            "bess_2h", study_id=sf.SITE_STUDY_ID, tariff=ctx.tariff)
    assert exc.value.code == "discount_rate_differs_from_lp"


def test_a_price_bound_recomputes_the_baseline_bill_at_the_perturbed_tariff():
    """
    BC-7: at a perturbed demand-charge price BOTH bills are priced at that
    price. With the dispatch held (an identity solve), the only change in the
    savings is the demand stream scaled by the price ratio, so the battery NPV
    at each bound is known in closed form.
    """
    n, _cfg = sf.solve_site_option("bess_2h")
    ledger = sf.site_ledger()
    ctx = _ctx(ledger, {"bess_2h": n}, _question("demand_charge_price"))
    calls = []
    out = F.run_tornado(ctx, lambda net, cfg, vid: calls.append(cfg) or net)
    assert out.robustness.status == "ok", out.robustness
    [row] = out.robustness.tornado
    assert row.evaluation == "redispatch"
    assert len(calls) == 2 and out.robustness.solves_charged == 2
    # Each re-dispatch carries the perturbed price in its own SolverConfig.
    assert sorted(c.demand_charge["price_per_mw_per_period"] for c in calls) == pytest.approx(
        [row.low_value, row.high_value])
    centre = next(r.value for r in ledger.rows if r.key == "demand_charge_price")
    streams = {s.key: s.annual_value for s in F.value_streams(
        ctx.baseline_bill, ctx.options["bess_2h"].bill)}
    rate = next(r.value for r in ledger.rows if r.key == "discount_rate")
    af = (1 - (1 + rate) ** -25) / rate
    for bound, npv in ((row.low_value, row.npv_low), (row.high_value, row.npv_high)):
        expected = out.robustness.npv_centre + (bound / centre - 1.0) * streams[
            "demand_charge_reduction"] * af
        assert npv == pytest.approx(expected, rel=1e-9), bound


def test_a_zero_size_battery_is_judged_by_its_size_not_its_npv_sign():
    """
    Gate S5 carry: a battery at or below epsilon is "no investment". Here the
    size is 0.5 kW on a dispatch that still shaves the peak, so the NPV read
    naively is large and POSITIVE; judged by size, the option is never
    re-dispatched, its NPV is not read, and nothing is recommended.
    """
    n, _cfg = sf.solve_site_option("bess_2h")
    tiny = F.copy_network(n)
    tiny.storage_units.loc["battery", "p_nom_opt"] = 0.5 * F.EPSILON_MW
    ctx = _ctx(sf.site_ledger(), {"bess_2h": tiny}, _question("demand_charge_price"))
    out = F.run_tornado(ctx, lambda *_a: pytest.fail("a zero-size battery is never re-dispatched"))
    [att] = out.attributions
    assert out.cases["bess_2h"].kpis.npv > 0            # what a sign test would read
    assert "size_zero_no_investment" in out.cases["bess_2h"].honesty_notes
    assert att.status == "skipped" and att.battery_npv is None
    assert att.unavailable["battery_npv"] == "size_zero_no_investment"
    assert "size_zero_no_investment" in att.notes
    assert out.robustness.status == "skipped" and out.robustness.note == "no_battery_candidate"
    v = F.verdict(out.attributions, out.robustness, fidelity="full_study")
    assert (v.status, v.class_, v.sentence_template) == ("ok", "not_recommended",
                                                         "not_recommended_none")
