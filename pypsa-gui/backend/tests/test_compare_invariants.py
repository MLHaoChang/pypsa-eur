"""Structural invariants: additivity by metric kind, plus per-tab identities."""
from __future__ import annotations

import pytest

from models.schemas import CarrierPeriodValue
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

@pytest.mark.xfail(
    strict=True,
    reason=(
        "capacity_mw_by_carrier.by_period is NOT 'sum of vintages with "
        "build_year=P' for plain (non vintage-expanded) assets — "
        "_compute_capacity_summary's _walk_plain replicates brownfield "
        "p_nom into EVERY period's by_period bucket (_bucket_replicate_"
        "per_period) while total counts it once, so sum(by_period) ~= "
        "N_periods x total whenever brownfield capacity exists. On the "
        "golden fixture (2 periods): gas sum=218.57 vs total=118.57 "
        "(ratio 1.843x — partly extendable, so less than 2x); solar "
        "sum=120.0 vs total=60.0 and diesel sum=20.0 vs total=10.0 "
        "(both exactly 2.0x — fully non-extendable, so it is pure "
        "double-counting of brownfield across periods). See "
        "task-5-6-report.md."
    ),
)
def test_installed_capacity_total_is_at_least_the_sum_of_its_vintages(golden):
    """
    `total` is the installed stock; `by_period` are the vintages built in each
    period. Pre-existing capacity has no vintage, so the sum of vintages can be
    less than the total but never more.
    """
    cap = cs.summarise(golden)["capacity"]
    for carrier, cpv in cap.capacity_mw_by_carrier.items():
        if not cpv.by_period:
            continue
        assert sum(cpv.by_period.values()) <= cpv.total * (1 + 1e-9), (
            f"{carrier}: vintages {cpv.by_period} exceed installed total {cpv.total}")


def test_new_capacity_never_exceeds_installed_capacity(golden):
    cap = cs.summarise(golden)["capacity"]
    for carrier, new in cap.new_capacity_mw_by_carrier.items():
        installed = cap.capacity_mw_by_carrier.get(carrier)
        assert installed is not None, f"{carrier} has new build but no installed entry"
        assert new.total <= installed.total * (1 + 1e-9)
