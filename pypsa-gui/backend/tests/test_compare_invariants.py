"""Structural invariants: additivity by metric kind, plus per-tab identities."""
from __future__ import annotations

import pytest

from models.schemas import CarrierPeriodValue
from tests import compare_local_networks as cln
from tests import compare_support as cs
from tests.golden import fixture as gf

REL = 1e-9


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _walk_period_values(obj, model_name=None, path=""):
    """Yield (model_name, field, label, CarrierPeriodValue) through the payload."""
    if isinstance(obj, CarrierPeriodValue):
        return
    model_name = model_name or type(obj).__name__
    # Class-level access (`type(obj).model_fields`), not `obj.model_fields` —
    # Pydantic 2.11 deprecates the instance-level form. Same fields either
    # way; `getattr(..., {})` keeps the fallback for non-BaseModel `obj`.
    for field in getattr(type(obj), "model_fields", {}):
        value = getattr(obj, field)
        label = f"{path}{model_name}.{field}"
        if isinstance(value, CarrierPeriodValue):
            yield model_name, field, label, value
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, CarrierPeriodValue):
                    yield model_name, field, f"{label}[{key}]", item
                else:
                    yield from _walk_period_values(item, path=f"{label}[{key}].")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if hasattr(type(item), "model_fields"):
                    yield from _walk_period_values(item, path=f"{label}[{i}].")


def _extensive_verdict(cpv: CarrierPeriodValue) -> tuple[bool, bool]:
    """
    (judged, bad) for one EXTENSIVE-classified value — the exact skip/compare
    logic `test_every_extensive_metric_sums_across_periods` applies, factored
    out so the census test below can count what that test actually JUDGES,
    not merely what `_walk_period_values` visits. `judged=False` means one of
    the test's own `continue` conditions fired (empty `by_period`, or both
    `total` and `sum(by_period)` are ~0) and this value never reached the
    comparison line.
    """
    if not cpv.by_period:
        return False, False
    total_of_parts = sum(cpv.by_period.values())
    if abs(cpv.total) < 1e-12 and abs(total_of_parts) < 1e-12:
        return False, False
    bad = cpv.total == 0 or abs(total_of_parts / cpv.total - 1.0) > 1e-6
    return True, bad


def _intensive_verdict(cpv: CarrierPeriodValue) -> tuple[bool, bool]:
    """(judged, bad) for one INTENSIVE-classified value — mirrors
    `test_no_intensive_metric_is_the_sum_of_its_periods`'s skip/compare logic."""
    parts = list(cpv.by_period.values())
    if len(parts) < 2 or abs(cpv.total) < 1e-12:
        return False, False
    if all(abs(p) < 1e-12 for p in parts):
        return False, False
    bad = abs(sum(parts) / cpv.total - 1.0) < 1e-6
    return True, bad


def test_every_extensive_metric_sums_across_periods(golden):
    s = cs.summarise(golden)
    bad = []
    for tab in cs.TAB_FIELDS:
        for model_name, field, label, cpv in _walk_period_values(s[tab]):
            if cs.classify(model_name, field) != cs.EXTENSIVE:
                continue
            judged, is_bad = _extensive_verdict(cpv)
            if judged and is_bad:
                total_of_parts = sum(cpv.by_period.values())
                bad.append(f"{tab}: {label} total={cpv.total!r} "
                           f"sum(by_period)={total_of_parts!r}")
    assert not bad, "extensive metrics whose periods do not sum to the total:\n  " \
        + "\n  ".join(bad)


# Tabs where every visited value is legitimately skipped by BOTH additivity
# tests' own guard clauses, on the current golden fixture, and why. Not a
# code defect — see each reason. If a future fixture change makes one of
# these judge something, that's fine (the exclusion just stops applying);
# the risk this dict guards against is a REGRESSION that silently drops a
# currently-judged tab to zero, which is exactly what the assertion below
# still catches for every tab NOT in this dict.
KNOWN_VACUOUS_TABS = {
    "lost_load": (
        "summarise() passes a nonexistent project_dir (see its own "
        "docstring), so _compute_lost_load_summary takes the "
        "no-results_state.pkl-capture branch: total_mwh/total_cost_meur are "
        "visited (CarrierPeriodValue defaults, not None) but always "
        "total=0.0 with by_period={}, so _extensive_verdict's "
        "`if not cpv.by_period` trips every time — 2 visited, 0 judged."
    ),
    "curtailment": (
        "the golden fixture has no generator with a time-varying p_max_pu "
        "profile, so curtailment is always exactly 0: total_gwh and "
        "system_rate_pct are visited but total=0.0 with by_period={}, "
        "tripping the same not-by_period / len(parts)<2 skip in both "
        "verdict helpers — 2 visited, 0 judged."
    ),
    "storage_cycling": (
        "verified directly against the solved network (not inferred from the "
        "summary alone): n.storage_units_t.p['bess'] is exactly 0.0 on all "
        "48 snapshots — the golden fixture has no time-varying p_max_pu or "
        "p_set anywhere (flat solar output, flat demand every snapshot in a "
        "period), so a zero-cost storage unit has no arbitrage incentive to "
        "ever charge/discharge and _compute_storage_cycling_summary "
        "(routers/compare.py) correctly reports 0 throughput from that all-"
        "zero dispatch. cycles_by_carrier / StorageUnitCycles.cycles are "
        "INTENSIVE with total=0.0 -> _intensive_verdict's `abs(cpv.total) < "
        "1e-12` skip trips; StorageUnitCycles.throughput_mwh is EXTENSIVE "
        "with total=0.0 and sum(by_period)=0.0 -> _extensive_verdict's "
        "both-near-zero skip trips. 3 visited, 0 judged. This is a genuine "
        "GOLDEN-FIXTURE COVERAGE GAP (storage-cycling additivity is never "
        "actually exercised by this suite), not a code defect in the "
        "compute function or the KIND registry — flagged for the fixture "
        "owner rather than fixed here, since fixture composition is "
        "deliberately incident-driven per its own module docstring."
    ),
}


def test_the_walk_visits_a_meaningful_number_of_period_values(golden):
    """
    Guard against both additivity tests above passing vacuously — but count
    what they actually JUDGE (reached the comparison line), not merely what
    `_walk_period_values` visits. Counting visits is not enough: `curtailment`
    visits 2 values (`total_gwh`, `system_rate_pct`) on this fixture and BOTH
    are skipped by every additivity test's own guard clause (`by_period={}`),
    so a regression confined entirely to curtailment's additivity logic would
    still show "2 visited" and pass a visited-only check while the two real
    tests silently checked nothing for that tab.

    Reusing `_extensive_verdict` / `_intensive_verdict` — the exact functions
    the two additivity tests call — means this test's judged-count can only
    go to zero for a tab for the same reason the additivity tests' own
    per-value skip would, so there is no separate "the census test's skip
    logic drifted from the real tests'" failure mode to worry about.

    `lost_load` and `curtailment` judge zero values on THIS fixture for
    documented, non-defect reasons (see `KNOWN_VACUOUS_TABS`) and are
    excluded from the "must judge something" assertion below. Every other
    tab must judge at least one real comparison on this solved, multi-period,
    multi-carrier network — a tab judged zero times cannot read as covered.
    """
    s = cs.summarise(golden)
    judged_counts = {tab: 0 for tab in cs.TAB_FIELDS}
    for tab in cs.TAB_FIELDS:
        for model_name, field, label, cpv in _walk_period_values(s[tab]):
            kind = cs.classify(model_name, field)
            if kind == cs.EXTENSIVE:
                judged, _ = _extensive_verdict(cpv)
            elif kind == cs.INTENSIVE:
                judged, _ = _intensive_verdict(cpv)
            else:
                judged = False
            judged_counts[tab] += judged

    unexpectedly_vacuous = {
        tab: n for tab, n in judged_counts.items()
        if n == 0 and tab not in KNOWN_VACUOUS_TABS
    }
    assert not unexpectedly_vacuous, (
        "tabs where neither additivity test performed a single real "
        f"comparison, and this is not a documented fixture limitation: "
        f"{unexpectedly_vacuous} (full judged census: {judged_counts})"
    )


def test_no_intensive_metric_is_the_sum_of_its_periods(golden):
    """
    The mirror image, and the one that catches a well-meant "fix". An
    intensive metric equal to the sum of its periods on a multi-period network
    means someone applied the additivity rule where it does not belong.
    Skips the degenerate cases where sum and mean coincide.
    """
    s = cs.summarise(golden)
    bad = []
    for tab in cs.TAB_FIELDS:
        for model_name, field, label, cpv in _walk_period_values(s[tab]):
            if cs.classify(model_name, field) != cs.INTENSIVE:
                continue
            judged, is_bad = _intensive_verdict(cpv)
            if judged and is_bad:
                bad.append(f"{tab}: {label} total={cpv.total!r} == sum(by_period)")
    assert not bad, "intensive metrics reported as a sum:\n  " + "\n  ".join(bad)


# ── Task 5: Capacity tab — internal identity ────────────────────────────────

def test_no_periods_installed_capacity_exceeds_the_total(golden):
    """
    Corrects `test_installed_capacity_total_is_at_least_the_sum_of_its_
    vintages` (removed): that test asserted `sum(by_period.values()) <=
    total`, which is the WRONG property. `_bucket_replicate_per_period`
    (routers/compare.py) deliberately replicates brownfield capacity into
    EVERY period's `by_period` bucket WITHOUT re-adding it to `total` — its
    own docstring explains why: without replication, switching the Compare
    View's period selector from "All" to a single period would make
    in-service brownfield capacity vanish from the bar. So `by_period[P]` is
    the capacity IN SERVICE during period P (a level), not a per-period
    increment — summing levels across periods is meaningless, which is
    exactly why `compare_support.py`'s KIND registry classifies this field as
    STOCK rather than EXTENSIVE.

    The invariant that DOES hold structurally, for every period P
    independently: `total = ini + sum(all deltas)`, `by_period[P] = ini +
    sum(deltas attributed to P)`, and every delta is >= 0 (capacity never
    shrinks in `_walk_plain` / `_walk_vintages`) — so no single period's
    level can exceed the horizon-total installed stock.
    """
    cap = cs.summarise(golden)["capacity"]
    for carrier, cpv in cap.capacity_mw_by_carrier.items():
        for period, value in cpv.by_period.items():
            assert value <= cpv.total * (1 + 1e-9), (
                f"{carrier}[{period}]: in-service capacity {value} exceeds "
                f"installed total {cpv.total}")


def test_new_capacity_never_exceeds_installed_capacity(golden):
    cap = cs.summarise(golden)["capacity"]
    for carrier, new in cap.new_capacity_mw_by_carrier.items():
        installed = cap.capacity_mw_by_carrier.get(carrier)
        assert installed is not None, f"{carrier} has new build but no installed entry"
        assert new.total <= installed.total * (1 + 1e-9)


# ── Task 6: Dispatch tab — internal identity ────────────────────────────────

def test_dispatch_energy_uses_the_generators_weighting_basis(golden):
    """
    Energy must be weighted by snapshot_weightings.generators — the basis
    n.statistics() and the Results-tab KPIs use. Recomputed here from PyPSA
    primitives so a change of basis in compare.py fails loudly.
    """
    from services import period_utils

    disp = cs.summarise(golden)["dispatch"]
    w = period_utils.snapshot_weights(golden, "generators", golden.snapshots)
    p = golden.generators_t.p
    expected_gwh = {}
    for gen in golden.generators.index:
        carrier = str(golden.generators.at[gen, "carrier"] or "unknown").lower()
        expected_gwh[carrier] = expected_gwh.get(carrier, 0.0) + \
            float((p[gen] * w).sum()) / 1e3
    for carrier, want in expected_gwh.items():
        got = disp.dispatch_gwh_by_carrier.get(carrier)
        assert got is not None, f"carrier {carrier} missing from dispatch tab"
        assert got.total == pytest.approx(want, rel=1e-6)


# ── Task 7: Line loading tab — internal identity ────────────────────────────
#
# NOTE on suspect S2 (Task 15): `_compute_loading_summary` (routers/compare.py)
# builds its weights via `_build_snapshot_weights(n)` with NO explicit column
# argument, which defaults to the "objective" (COST) basis — not "generators"
# (ENERGY/HOURS), the basis `test_dispatch_energy_uses_the_generators_
# weighting_basis` above insists dispatch GWh use. `binding_hours` and
# `mean_loading` are hours/loading quantities, not cost, so this reads like
# the same class of bug. BUT on the golden fixture `snapshot_weightings
# ["objective"]` and `["generators"]` are identical (both flat 1.0 — no
# custom weighting is ever set in build_golden_network), so the two invariants
# below hold under EITHER basis here and cannot distinguish which one
# `_compute_loading_summary` is actually using. A green run of these two tests
# is NOT evidence the weighting-basis choice is correct — it is only evidence
# that peak/mean/binding-hours arithmetic is internally consistent. The basis
# question stays OPEN pending the fixture Task 15 builds with `objective` !=
# `generators`.

def test_binding_hours_never_exceed_the_horizon(golden):
    from services import period_utils

    loading = cs.summarise(golden)["loading"]
    horizon_hours = float(period_utils.snapshot_weights(
        golden, "generators", golden.snapshots).sum())
    for entry in loading.lines:
        assert entry.binding_hours.total <= horizon_hours * (1 + 1e-9), (
            f"{entry.name}: {entry.binding_hours.total} h binding of "
            f"{horizon_hours} h horizon")


def test_mean_loading_never_exceeds_peak_loading(golden):
    for entry in cs.summarise(golden)["loading"].lines:
        assert entry.mean_loading.total <= entry.peak_loading.total + 1e-9, entry.name


# ── Task 8: Prices tab — internal identity ──────────────────────────────────

def test_the_duration_curve_is_monotonically_non_increasing(golden):
    curve = cs.summarise(golden)["prices"].duration_curve
    assert curve, "empty duration curve on a solved network"
    assert all(curve[i] >= curve[i + 1] - 1e-9 for i in range(len(curve) - 1)), \
        "duration curve is not sorted descending"


def test_price_statistics_lie_inside_the_observed_range(golden):
    pr = cs.summarise(golden)["prices"]
    assert pr.min_price - 1e-9 <= pr.median_price.total <= pr.max_price + 1e-9
    assert pr.min_price - 1e-9 <= pr.mean_price.total <= pr.max_price + 1e-9
    for carrier, stats in pr.by_carrier_stats.items():
        assert pr.min_price - 1e-9 <= stats.mean_price.total <= pr.max_price + 1e-9, carrier


# ── Task 9: Emissions tab — internal identity ───────────────────────────────

def test_per_carrier_emissions_sum_to_the_total(golden):
    em = cs.summarise(golden)["emissions"]
    parts = sum(c.total for c in em.by_carrier_kt.values())
    assert parts == pytest.approx(em.total_kt.total, rel=1e-6)


def test_intensity_is_total_emissions_over_total_generator_dispatch(golden):
    """
    The brief's hypothesis (``intensity = total emissions / total LOAD``,
    i.e. ``DispatchComparison.total_load_gwh``) does NOT hold — measured
    directly, dividing by load gives 131.746 kg/MWh against a reported
    125.758 kg/MWh, a ~4.8% gap that is not rounding noise.

    `_compute_emissions_summary`'s own docstring says "Intensity = total kt
    x 1000 / total dispatch GWh x 1000 = kg/MWh", and its code builds
    `total_dispatch_mwh` by summing EVERY column of `n.generators_t.p`
    (every carrier, including zero-emission ones, weighted by the
    "generators" snapshot-weighting column x investment-period years)
    BEFORE the co2-intensity filter — i.e. total GENERATOR dispatch, not
    total load. `total_load_gwh` is a different quantity entirely: it sums
    `n.loads` magnitude across every bus, mixing electricity-MWh and
    H2-MWh loads without unit conversion, with no relation to what the
    generators produced.

    Measured on the golden fixture: total generator dispatch = 67.8857 GWh
    (gas 42.6857 + solar 21.6 + diesel 3.6) vs `total_load_gwh.total` =
    64.8 GWh. Dividing total emissions by generator dispatch reproduces the
    reported intensity exactly (ratio 1.0000000000000002) — pinning that as
    the actual definition, per the brief's own guidance to check the
    denominator before assuming the numerator is wrong.
    """
    from services import period_utils

    s = cs.summarise(golden)
    em = s["emissions"]
    w = period_utils.snapshot_weights(golden, "generators", golden.snapshots)
    p = golden.generators_t.p
    total_gen_mwh = float((p.multiply(w, axis=0)).sum().sum())
    if total_gen_mwh <= 0:
        pytest.skip("no generator dispatch in the fixture — intensity is undefined")
    # kt -> kg is a factor of 1e6; total_gen_mwh is already in MWh.
    expected = em.total_kt.total * 1e6 / total_gen_mwh
    assert em.intensity_kg_per_mwh.total == pytest.approx(expected, rel=1e-6)


# ── Task 10: Economics tab — the LCOE quotient (suspect S3) ─────────────────

def test_lcoe_is_total_cost_over_total_energy(golden):
    """
    Suspect S3: `_compute_economics_summary`'s docstring says
    ``capex (EUR/yr)`` and ``LCOE = (Sum capex + Sum opex) / Sum dispatch_MWh``.
    If capex were a per-YEAR rate while dispatch is horizon-summed, LCOE
    would be low by the horizon's year count. The golden fixture spans 15
    modelled years (GOLDEN_YEARS = (5, 10)), so that confusion would show up
    as a ~15x ratio between the reported LCOE and this identity's
    prediction — not a subtle few percent.

    Measured: for every carrier with positive dispatch (gas, solar, diesel,
    h2) the reported/expected ratio is exactly 1.0. S3 is CLEARED —
    `_capex_commitment` (routers/compare.py) already scales CAPEX by
    ``Sum years_in_period`` before it reaches this quotient (its own
    docstring: "FULL-HORIZON basis"), unlike the sibling
    `services/asset_results/compute.py` bug (fixed 2026-08-03, commit
    922eb4d0) this suspicion was modelled on.
    """
    econ = cs.summarise(golden)["economics"]
    checked = 0
    for carrier, e in econ.by_carrier.items():
        if e.dispatch_gwh.total <= 0:
            continue
        expected = ((e.capex_meur.total + e.opex_meur.total) * 1e6) / \
                   (e.dispatch_gwh.total * 1e3)
        assert e.lcoe_eur_per_mwh.total == pytest.approx(expected, rel=1e-6), (
            f"{carrier}: LCOE {e.lcoe_eur_per_mwh.total} != "
            f"(capex {e.capex_meur.total} + opex {e.opex_meur.total}) M€ / "
            f"{e.dispatch_gwh.total} GWh")
        checked += 1
    assert checked >= 1, "no carrier with positive dispatch — vacuous test"


# ── Task 11: Curtailment tab — internal identity ────────────────────────────
#
# The golden fixture is PROVABLY vacuous here (see KNOWN_VACUOUS_TABS
# ["curtailment"] above): no generator carries a time-varying `p_max_pu`, so
# nothing is ever curtailed and both `total_gwh`/`system_rate_pct` come back
# `total=0.0, by_period={}`. `tests/golden/fixture.py` must NOT change — it
# would perturb every other test's LP optimum (task-11/12/13 brief) — so
# these two tests solve their OWN tiny curtailment-forcing network from
# `compare_local_networks.py` instead of `golden`. See that module's
# `build_curtailment_network` docstring for why it actually curtails: a
# single renewable generator with a time-varying `p_max_pu` profile, and a
# load fixed below every snapshot's available power, with no storage/export
# to absorb the surplus.

@pytest.fixture()
def curtailment_net(reset_backend):
    return cln.solve_curtailment_network()


def test_per_carrier_curtailment_sums_to_the_total(curtailment_net):
    cur = cs.summarise(curtailment_net)["curtailment"]
    assert cur.total_gwh.total > 0, (
        "curtailment-forcing network produced no curtailment — fixture is broken")
    parts = sum(c.total for c in cur.by_carrier_gwh.values())
    assert parts == pytest.approx(cur.total_gwh.total, rel=1e-6)


def test_curtailment_rate_is_a_percentage_between_zero_and_one_hundred(curtailment_net):
    cur = cs.summarise(curtailment_net)["curtailment"]
    assert cur.rate_pct_by_carrier, "no per-carrier curtailment rate produced"
    for carrier, rate in cur.rate_pct_by_carrier.items():
        assert -1e-9 <= rate.total <= 100.0 + 1e-9, f"{carrier}: {rate.total}%"
    assert -1e-9 <= cur.system_rate_pct.total <= 100.0 + 1e-9
    # Canary against a degenerate always-0%/always-100% pass: on this
    # fixture the true rate is a non-trivial 75% (240 MWh curtailed of
    # 320 MWh available — see build_curtailment_network's docstring).
    assert cur.system_rate_pct.total == pytest.approx(75.0, rel=1e-6)
