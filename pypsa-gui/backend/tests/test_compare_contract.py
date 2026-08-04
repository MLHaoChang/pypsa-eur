"""Contract-level checks: the payload is populated, deterministic, classified."""
from __future__ import annotations

import pytest

from models import schemas
from tests import compare_support as cs
from tests.golden import fixture as gf

MODELS_WITH_PERIOD_VALUES = (
    "CapacityComparison", "DispatchComparison", "LineLoadingEntry",
    "PricesComparison", "CarrierPriceStats", "EmissionsComparison",
    "CarrierEconomics", "AssetLCOHEntry", "CurtailmentComparison",
    "LostLoadComparison", "LostLoadBus", "LostLoadByCarrier",
    "StorageCyclingComparison", "StorageUnitCycles",
)


def _period_value_fields(model_name: str) -> list[str]:
    """Fields whose type is CarrierPeriodValue or dict[str, CarrierPeriodValue]."""
    model = getattr(schemas, model_name)
    out = []
    for field_name, info in model.model_fields.items():
        ann = str(info.annotation)
        if "CarrierPeriodValue" in ann:
            out.append(field_name)
    return out


def test_every_period_bearing_field_is_classified():
    missing = []
    for model_name in MODELS_WITH_PERIOD_VALUES:
        for field in _period_value_fields(model_name):
            if f"{model_name}.{field}" not in cs.KIND:
                missing.append(f"{model_name}.{field}")
    assert not missing, (
        "These fields carry a per-period breakdown but no extensive/intensive/"
        "stock classification, so the additivity suite would skip them "
        "silently:\n  " + "\n  ".join(missing)
    )


def test_the_registry_names_no_field_that_has_been_removed():
    """The mirror image: a renamed field must not leave a dead entry behind."""
    stale = []
    for key in cs.KIND:
        model_name, field = key.split(".", 1)
        model = getattr(schemas, model_name, None)
        if model is None or field not in model.model_fields:
            stale.append(key)
    assert not stale, f"registry entries with no matching field: {stale}"


@pytest.fixture()
def golden(reset_backend):
    """Solved golden network, installed after conftest's autouse reset."""
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def test_the_harness_derives_the_same_periods_the_fixture_declares(golden):
    s = cs.summarise(golden)
    assert s["is_multi"] is True
    assert s["periods"] == list(gf.GOLDEN_PERIODS)


def test_every_tab_is_populated_for_a_solved_multi_period_network(golden):
    s = cs.summarise(golden)
    empty = [f for f in cs.TAB_FIELDS if s[f] is None]
    assert not empty, f"tabs returning None on a solved network: {empty}"


def test_summarising_twice_gives_an_identical_payload(golden):
    """
    The backend computes no delta — CompareView.tsx diffs two independent
    fetches client-side. So A-vs-A showing zero everywhere rests on the
    summary being a pure function of the network. If it is not, every
    comparison inherits the noise.
    """
    first, second = cs.summarise(golden), cs.summarise(golden)
    for field in cs.TAB_FIELDS:
        assert first[field].model_dump() == second[field].model_dump(), (
            f"{field} differs between two summarisations of one network")
