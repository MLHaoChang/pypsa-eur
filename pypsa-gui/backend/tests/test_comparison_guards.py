"""AA-09 S2: what a retained baseline must share with the candidate (GK B (74)/(75)).

A comparison needs the same window, cost basis, period (the sealed window
identity: snapshot count and weight basis) and unit; a delta keeps the
compared metric's unit and a percent of a percent is refused; the scope admits
a comparison_evidence_id for an exact set of profiles; a missing baseline total
reads unavailable, never 0.
"""

import typing

import pytest

from harness import assurance_conversation, assurance_scope, calculations, fact_identity

INDEX = ["2026-01-01T00:00:00", "2026-01-01T01:00:00", "2026-01-01T02:00:00"]
BASIS = {"currency": "EUR", "price_year": 2026, "annual_fixed_cost_fraction": 1}


def _scope(**extra):
    return {
        "version": 1,
        "profile": "generator-cost/1",
        "project_id": "p1",
        "entity_id": "G1",
        "metrics": ["total_cost", "cost_delta", "cost_change_percent"],
        "start": INDEX[0],
        "end": INDEX[-1],
        "cost_basis": dict(BASIS),
        **extra,
    }


def _raw(index=INDEX):
    return {"generators": {"G1": {"index": list(index)}}}


def _baseline(scope=None, index=INDEX, unit="EUR", **extra):
    scope = scope or _scope()
    return {
        "request_scope": {**scope, "retain": True},
        "window": fact_identity.window(index, scope),
        "facts": [{"metric": "total_cost", "unit": unit, "value": 260.0}],
        **extra,
    }


def test_the_same_window_basis_period_and_unit_are_comparable():
    assert calculations.comparison_mismatch(_raw(), _scope(), _baseline()) is None


def test_another_window_is_refused():
    base = _baseline(_scope(start=INDEX[1]))
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "start"
    assert assurance_conversation._compatible(_scope(start=INDEX[1])) != (
        assurance_conversation._compatible(_scope())
    )


def test_another_basis_is_refused():
    other = _scope(cost_basis={**BASIS, "price_year": 2027})
    base = _baseline(other)
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "cost_basis"
    assert assurance_conversation._compatible(other) != (
        assurance_conversation._compatible(_scope())
    )


def test_another_period_is_refused():
    # Same start and end at another resolution: two snapshots, not three.
    coarse = [INDEX[0], INDEX[-1]]
    assert calculations.comparison_mismatch(
        _raw(), _scope(), _baseline(index=coarse)
    ) == "period"


def test_another_weight_basis_is_refused():
    base = _baseline()
    base["window"]["weight_basis"]["total_cost"] = "objective"
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"


@pytest.mark.parametrize("window", [None, {}, {"snapshots": None}])
def test_an_unreadable_period_is_refused_never_matched(window):
    base = _baseline()
    if window is None:
        del base["window"]
    else:
        base["window"] = window
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"


def test_another_unit_is_refused():
    base = _baseline(unit="USD")
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "unit"


def test_the_calculation_refuses_an_incompatible_baseline(monkeypatch):
    from services import assurance_records as records

    eid = "0" * 36
    base = {**_baseline(index=[INDEX[0], INDEX[-1]]), "evidence_id": eid,
            "source_sha256": "x", "raw": {}}
    monkeypatch.setattr(records, "evidence", lambda owner, evidence_id: base)
    with pytest.raises(ValueError, match="^incompatible_comparison_basis$"):
        calculations._comparison(_raw(), _scope(comparison_evidence_id=eid), "owner")


def test_a_delta_keeps_the_compared_unit():
    assert calculations.DEFINITIONS["cost_delta"]["unit"] == (
        calculations.DEFINITIONS[calculations.COMPARED_METRIC]["unit"]
    )
    assert calculations.DEFINITIONS["cost_change_percent"]["unit"] == "%"


def _compare(monkeypatch, baseline_facts, total=230.0):
    monkeypatch.setattr(
        calculations, "scoped_calculate", lambda raw, scope: (baseline_facts, [])
    )
    lp = {"kind": "lp", "gap": 0.0, "optimal": True, "disclosure": None}
    raw = {
        "solve": {**lp, "objective": total},
        "comparison": {"raw": {"solve": {**lp, "objective": 260.0}}, "scope": _scope()},
    }
    available, reasons = {"total_cost": total}, {}
    calculations._comparison_values(raw, available, reasons)
    return available, reasons


def test_a_percent_of_a_percent_is_refused(monkeypatch):
    monkeypatch.setitem(calculations.DEFINITIONS["total_cost"], "unit", "%")
    available, reasons = _compare(
        monkeypatch, [{"metric": "total_cost", "unit": "%", "value": 40.0}], 50.0
    )
    assert "cost_delta" not in available and "cost_change_percent" not in available
    assert reasons == {
        "cost_delta": "derived_unit_mismatch",
        "cost_change_percent": "derived_unit_mismatch",
    }


def test_the_delta_is_candidate_minus_baseline(monkeypatch):
    available, _ = _compare(
        monkeypatch, [{"metric": "total_cost", "unit": "EUR", "value": 260.0}]
    )
    assert available["cost_delta"] == -30.0
    assert available["cost_change_percent"] == -30.0 / 260.0 * 100


@pytest.mark.parametrize(
    "facts",
    [[], [{"metric": "dispatch_energy", "unit": "MWh", "value": 1.0}],
     [{"metric": "total_cost", "unit": "EUR", "value": None}]],
    ids=["no-facts", "no-total", "none-total"],
)
def test_a_missing_baseline_total_reads_unavailable_never_zero(monkeypatch, facts):
    available, reasons = _compare(monkeypatch, facts)
    assert "cost_delta" not in available and "cost_change_percent" not in available
    assert reasons["cost_delta"] == "baseline_total_unavailable"
    assert reasons["cost_change_percent"] == "baseline_denominator_zero_or_unavailable"


def _valid_base(profile):
    """A scope for `profile` that validates without a comparison, or None."""
    classes = (None, "Generator", "Line", "StorageUnit")
    for entity in ("G1", "network"):
        for component_class in classes:
            scope = {"version": 1, "profile": profile, "project_id": "p1",
                     "entity_id": entity}
            if component_class:
                scope["component_class"] = component_class
            try:
                assurance_scope.validate(scope)
            except ValueError:
                continue
            return scope
    return None


def test_exactly_these_profiles_admit_a_comparison_evidence_id():
    profiles = typing.get_args(
        assurance_scope.RequestScope.model_fields["profile"].annotation
    )
    admitted = set()
    for profile in profiles:
        base = _valid_base(profile)
        assert base is not None, profile
        try:
            assurance_scope.validate({**base, "comparison_evidence_id": "0" * 36})
        except ValueError:
            continue
        admitted.add(profile)
    assert admitted == {
        "generator-dispatch/1",
        "generator-cost/1",
        "network-linear-cost/1",
        "storage-unit-cost/1",
    }
    assert admitted == assurance_scope.COMPARISON_PROFILES
