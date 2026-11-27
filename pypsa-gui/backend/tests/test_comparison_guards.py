"""AA-09 S2: what a retained baseline must share with the candidate (GK B (75) GO).

A comparison needs the same window, cost basis, period (the sealed window
identity, and exactly the in-window snapshot index and objective weights of
both raws) and unit; a delta keeps the compared metric's unit and a percent of
a percent is refused; the scope admits a comparison_evidence_id for an exact
set of profiles; a missing baseline total reads unavailable, never 0.
"""

import typing

import pytest

from harness import assurance_conversation, assurance_scope, calculations, fact_identity

INDEX = ["2026-01-01T00:00:00", "2026-01-01T01:00:00", "2026-01-01T02:00:00"]
WEIGHTS = [1.0, 1.0, 1.0]
BASIS = {"currency": "EUR", "price_year": 2026, "annual_fixed_cost_fraction": 1}
EID = "0" * 36


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


def _raw(index=INDEX, weights=WEIGHTS):
    return {
        "generators": {
            "G1": {"index": list(index), "inputs": {"objective_weight": list(weights)}}
        }
    }


def _baseline(scope=None, index=INDEX, weights=WEIGHTS, unit="EUR"):
    scope = scope or _scope()
    return {
        "evidence_id": EID,
        "source_sha256": "sha",
        "request_scope": {**scope, "retain": True},
        "window": fact_identity.window(index, scope),
        "facts": [{"metric": "total_cost", "unit": unit, "value": 260.0}],
        "raw": _raw(index, weights),
    }


def _refused(monkeypatch, baseline, raw=None):
    """calculations._comparison's kind for this baseline, or None when it compares."""
    from services import assurance_records as records

    monkeypatch.setattr(records, "evidence", lambda owner, evidence_id: baseline)
    try:
        calculations._comparison(
            raw or _raw(), _scope(comparison_evidence_id=EID), "owner"
        )
    except ValueError as exc:
        return str(exc)
    return None


def test_the_same_window_basis_period_and_unit_are_comparable(monkeypatch):
    assert calculations.comparison_mismatch(_raw(), _scope(), _baseline()) is None
    assert _refused(monkeypatch, _baseline()) is None


def test_every_site_reads_the_one_comparison_keys_tuple(monkeypatch):
    monkeypatch.setattr(calculations, "COMPARISON_KEYS", ("profile",))
    assert set(assurance_conversation._compatible(_scope())) == {"profile"}
    other = _baseline(_scope(entity_id="G2"))
    assert calculations.comparison_mismatch(_raw(), _scope(), other) is None


@pytest.mark.parametrize(
    ("key", "baseline"),
    [
        ("start", lambda: _baseline(_scope(start=INDEX[1]))),
        (
            "cost_basis",
            lambda: _baseline(_scope(cost_basis={**BASIS, "price_year": 2027})),
        ),
        ("period", lambda: _baseline(weights=[1.0, 2.0, 1.0])),
        ("unit", lambda: _baseline(unit="USD")),
    ],
    ids=["window", "basis", "period", "unit"],
)
def test_each_key_is_refused_by_the_calculation(monkeypatch, key, baseline):
    assert calculations.comparison_mismatch(_raw(), _scope(), baseline()) == key
    assert _refused(monkeypatch, baseline()) == "incompatible_comparison_basis"


def test_the_conversation_keys_cover_window_and_basis():
    current = assurance_conversation._compatible(_scope())
    assert assurance_conversation._compatible(_scope(start=INDEX[1])) != current
    assert assurance_conversation._compatible(_scope(end=INDEX[1])) != current
    other_basis = _scope(cost_basis={**BASIS, "price_year": 2027})
    assert assurance_conversation._compatible(other_basis) != current


def test_another_resolution_is_refused(monkeypatch):
    # Same start and end at another resolution: two snapshots, not three.
    coarse = [INDEX[0], INDEX[-1]]
    base = _baseline(index=coarse, weights=[1.0, 1.0])
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"
    assert _refused(monkeypatch, base) == "incompatible_comparison_basis"


def test_the_same_index_with_other_weights_is_refused(monkeypatch):
    base = _baseline(weights=[1.0, 1.0, 3.0])
    assert base["window"] == _baseline()["window"]
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"
    assert _refused(monkeypatch, base) == "incompatible_comparison_basis"


def test_the_same_count_with_other_timestamps_is_refused(monkeypatch):
    uneven = [INDEX[0], "2026-01-01T00:30:00", INDEX[-1]]
    base = _baseline(index=uneven)
    assert base["window"] == _baseline()["window"]
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"
    assert _refused(monkeypatch, base) == "incompatible_comparison_basis"


@pytest.mark.parametrize(
    "shape",
    [
        {},
        {"generators": {}},
        {"storage_unit": {"index": INDEX}},
        {"generators": {"G1": {"index": INDEX}}},
    ],
    ids=["empty", "no-generators", "other-profile-shape", "no-weights"],
)
def test_an_unknown_baseline_shape_is_refused_never_equal(monkeypatch, shape):
    base = {**_baseline(), "raw": shape}
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"
    assert _refused(monkeypatch, base) == "incompatible_comparison_basis"


def test_period_of_an_uncomparable_profile_raises():
    with pytest.raises(ValueError, match="^unknown_period_shape$"):
        calculations._period(_raw(), _scope(profile="generator-dispatch/1"))


def test_a_population_whose_members_disagree_raises():
    raw = _raw()
    raw["generators"]["G2"] = {
        "index": INDEX,
        "inputs": {"objective_weight": [2.0] * 3},
    }
    with pytest.raises(ValueError, match="^unknown_period_shape$"):
        calculations._period(raw, _scope(profile="network-linear-cost/1"))


def test_the_storage_cost_period_reads_its_trajectory():
    raw = {
        "storage_unit": {"index": INDEX, "costs": {"objective_weight": [1.0, 2.0, 3.0]}}
    }
    scope = _scope(profile="storage-unit-cost/1", start=INDEX[1])
    assert calculations._period(raw, scope) == (INDEX[1:], [2.0, 3.0])


@pytest.mark.parametrize(
    "window", [None, {}, {**fact_identity.window(INDEX, _scope()), "snapshots": None}]
)
def test_an_unreadable_window_record_is_refused_never_matched(window):
    base = _baseline()
    if window is None:
        del base["window"]
    else:
        base["window"] = window
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"


def test_another_weight_basis_is_refused():
    base = _baseline()
    base["window"]["weight_basis"]["total_cost"] = "objective"
    assert calculations.comparison_mismatch(_raw(), _scope(), base) == "period"


def test_a_definition_unit_change_since_the_seal_is_refused(monkeypatch):
    # The sealed baseline fact says EUR; the candidate's definition now says USD.
    monkeypatch.setitem(calculations.DEFINITIONS["total_cost"], "unit", "USD")
    assert calculations.comparison_mismatch(_raw(), _scope(), _baseline()) == "unit"
    assert _refused(monkeypatch, _baseline()) == "incompatible_comparison_basis"


def test_a_delta_keeps_the_compared_unit():
    for profile in sorted(calculations.COST_PROFILES):
        assert (
            calculations.definition("cost_delta", profile)["unit"]
            == (calculations.definition("total_cost", profile)["unit"])
        )
    assert calculations.definition("cost_change_percent")["unit"] == "%"


def _compare(monkeypatch, baseline_facts, available=None):
    monkeypatch.setattr(
        calculations, "scoped_calculate", lambda raw, scope: (baseline_facts, [])
    )
    lp = {"kind": "lp", "gap": 0.0, "optimal": True, "disclosure": None}
    raw = {
        "solve": {**lp, "objective": 230.0},
        "comparison": {"raw": {"solve": {**lp, "objective": 260.0}}, "scope": _scope()},
    }
    available = {"total_cost": 230.0} if available is None else available
    reasons = {}
    calculations._comparison_values(raw, available, reasons)
    return available, reasons


def test_a_percent_of_a_percent_is_refused_and_the_delta_is_in_pp(monkeypatch):
    monkeypatch.setitem(calculations.DEFINITIONS["total_cost"], "unit", "%")
    available, reasons = _compare(
        monkeypatch,
        [{"metric": "total_cost", "unit": "%", "value": 40.0}],
        {"total_cost": 50.0},
    )
    assert available["cost_delta"] == 10.0
    assert calculations.definition("cost_delta", "generator-cost/1")["unit"] == "pp"
    assert "cost_change_percent" not in available
    assert reasons["cost_change_percent"] == "derived_unit_mismatch"


def test_the_delta_is_candidate_minus_baseline(monkeypatch):
    available, _ = _compare(
        monkeypatch, [{"metric": "total_cost", "unit": "EUR", "value": 260.0}]
    )
    assert available["cost_delta"] == -30.0
    assert available["cost_change_percent"] == -30.0 / 260.0 * 100


@pytest.mark.parametrize(
    "facts",
    [
        [],
        [{"metric": "dispatch_energy", "unit": "MWh", "value": 1.0}],
        [{"metric": "total_cost", "unit": "EUR", "value": None}],
    ],
    ids=["no-facts", "no-total", "none-total"],
)
def test_a_missing_baseline_total_reads_unavailable_never_zero(monkeypatch, facts):
    available, reasons = _compare(monkeypatch, facts)
    assert "cost_delta" not in available and "cost_change_percent" not in available
    assert reasons["cost_delta"] == "baseline_total_unavailable"
    assert reasons["cost_change_percent"] == "baseline_denominator_zero_or_unavailable"


def test_a_zero_baseline_gives_a_delta_but_no_percent(monkeypatch):
    available, reasons = _compare(
        monkeypatch, [{"metric": "total_cost", "unit": "EUR", "value": 0.0}]
    )
    assert available["cost_delta"] == 230.0
    assert "cost_change_percent" not in available
    assert reasons["cost_change_percent"] == "baseline_denominator_zero_or_unavailable"


def test_a_candidate_without_a_total_gives_no_delta(monkeypatch):
    available, reasons = _compare(
        monkeypatch, [{"metric": "total_cost", "unit": "EUR", "value": 260.0}], {}
    )
    assert available == {}
    assert reasons["cost_delta"] == "baseline_total_unavailable"


def _valid_base(profile):
    """A scope for `profile` that validates without a comparison, or None."""
    for entity in ("G1", "network"):
        for component_class in (None, "Generator", "Line", "StorageUnit"):
            scope = {
                "version": 1,
                "profile": profile,
                "project_id": "p1",
                "entity_id": entity,
            }
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
            assurance_scope.validate({**base, "comparison_evidence_id": EID})
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


@pytest.mark.parametrize(
    ("profile", "extra", "kind"),
    [
        ("storage-unit-dispatch/1", {}, "unsupported_storage_cost_scope"),
        ("carrier-energy/1", {}, "unsupported_carrier_cost_scope"),
        ("branch-loading/1", {}, "unsupported_loading_cost_scope"),
        ("bus-price/1", {}, "unsupported_result_cost_scope"),
        ("lost-load/1", {}, "unsupported_lost_load_cost_scope"),
        ("energy-balance/1", {"entity_id": "network"}, "network_scope_required"),
        (
            "capacity/1",
            {"component_class": "Generator"},
            "unsupported_result_cost_scope",
        ),
        (
            "generator-dispatch/1",
            {"entity_id": "*", "component_class": "Generator"},
            "unsupported_population_scope",
        ),
    ],
)
def test_profiles_refused_earlier_keep_their_old_kind(profile, extra, kind):
    scope = {
        "version": 1,
        "profile": profile,
        "project_id": "p1",
        "entity_id": "G1",
        "comparison_evidence_id": EID,
        **extra,
    }
    with pytest.raises(ValueError, match=f"^{kind}$"):
        assurance_scope.validate(scope)


def test_a_profile_without_an_earlier_kind_gets_the_comparison_kind():
    scope = {
        "version": 1,
        "profile": "renewable-share/1",
        "project_id": "p1",
        "entity_id": "network",
        "comparison_evidence_id": EID,
    }
    with pytest.raises(ValueError, match="^unsupported_result_cost_scope$"):
        assurance_scope.validate(scope)
    scope = {
        "version": 1,
        "profile": "storage-cycles/1",
        "project_id": "p1",
        "entity_id": "S1",
        "component_class": "Store",
        "comparison_evidence_id": EID,
    }
    with pytest.raises(ValueError, match="^unsupported_result_cost_scope$"):
        assurance_scope.validate(scope)


def test_a_profile_outside_the_allow_set_gets_the_comparison_kind(monkeypatch):
    # Every released profile outside the set is refused earlier today; this
    # pins the last guard itself, for the next profile added without a refusal.
    monkeypatch.setattr(
        assurance_scope,
        "COMPARISON_PROFILES",
        assurance_scope.COMPARISON_PROFILES - {"generator-cost/1"},
    )
    scope = {
        "version": 1,
        "profile": "generator-cost/1",
        "project_id": "p1",
        "entity_id": "G1",
        "comparison_evidence_id": EID,
    }
    with pytest.raises(ValueError, match="^unsupported_comparison_scope$"):
        assurance_scope.validate(scope)
