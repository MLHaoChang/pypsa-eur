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


def test_every_extensive_metric_sums_across_periods(golden):
    s = cs.summarise(golden)
    bad = []
    for tab in cs.TAB_FIELDS:
        for model_name, field, label, cpv in _walk_period_values(s[tab]):
            if cs.classify(model_name, field) != cs.EXTENSIVE:
                continue
            if not cpv.by_period:
                continue
            total_of_parts = sum(cpv.by_period.values())
            if abs(cpv.total) < 1e-12 and abs(total_of_parts) < 1e-12:
                continue
            if cpv.total == 0 or abs(total_of_parts / cpv.total - 1.0) > 1e-6:
                bad.append(f"{tab}: {label} total={cpv.total!r} "
                           f"sum(by_period)={total_of_parts!r}")
    assert not bad, "extensive metrics whose periods do not sum to the total:\n  " \
        + "\n  ".join(bad)


def test_the_walk_visits_a_meaningful_number_of_period_values(golden):
    """
    Guard against both additivity tests above passing vacuously. Nothing in
    ``_walk_period_values`` stops it from silently failing to descend into
    one of the payload's four nesting shapes (bare CarrierPeriodValue field,
    dict[str, CarrierPeriodValue], a CarrierPeriodValue field on a model
    nested in a dict, or on a model nested in a list) — if that happened,
    both tests above would report zero bad entries and look green while
    checking nothing. Every tab yields at least one CarrierPeriodValue on
    this solved, multi-period, multi-carrier network, even the tabs whose
    values are trivially zero for THIS fixture (``lost_load`` has no
    ``results_state.pkl`` capture — see ``summarise()``'s no-project-dir
    note; ``curtailment`` has no generator with a time-varying
    ``p_max_pu`` profile) — those still surface their bare ``total_mwh`` /
    ``total_cost_meur`` / ``total_gwh`` / ``system_rate_pct`` fields with
    ``total=0.0, by_period={}``, which the two additivity tests' own skip
    conditions correctly exclude from judgement without the walk itself
    going empty.
    """
    s = cs.summarise(golden)
    counts = {tab: sum(1 for _ in _walk_period_values(s[tab])) for tab in cs.TAB_FIELDS}
    starved = {tab: n for tab, n in counts.items() if n == 0}
    assert not starved, f"tabs the walk visited zero period-values in: {starved} (full census: {counts})"


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
            parts = [v for v in cpv.by_period.values()]
            if len(parts) < 2 or abs(cpv.total) < 1e-12:
                continue
            if all(abs(p) < 1e-12 for p in parts):
                continue
            if abs(sum(parts) / cpv.total - 1.0) < 1e-6:
                bad.append(f"{tab}: {label} total={cpv.total!r} == sum(by_period)")
    assert not bad, "intensive metrics reported as a sum:\n  " + "\n  ".join(bad)
