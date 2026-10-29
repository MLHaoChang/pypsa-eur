"""S3: one fact builder for the static, page, series, run and aggregate kinds.

The behaviour of each kind stays pinned by its own A1.x suite; this file pins
the builder's shape, the one spelling it changed (static's "/" + metric), a
static fact for every input class and a series step of a name that needs
escaping.
"""

import pytest

from services import assurance_records as records
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import approval
from tests.assurance_acceptance.test_p1_timeseries import (
    STEPS,
    network,
    read_series,
    series_draft,
    set_series,
)


def test_static_path_is_unchanged_for_every_input_metric():
    # The live table: a metric added later is checked too.
    for metrics in records.INPUT_METRICS.values():
        for metric in metrics:
            assert "~" not in metric and "/" not in metric
            assert records._fact("L1", metric, 1.0, "MW", metric)["path"] == "/" + metric


def test_fact_names_entity_metric_value_unit_and_escaped_path():
    fact = records._fact("a/b~c", "p_set", 2.5, "MW", "a/b~c", "2030", "t0")
    assert list(fact) == ["path", "entity_id", "metric", "value", "unit"]
    assert fact == {
        "path": "/a~1b~0c/2030/t0",
        "entity_id": "a/b~c",
        "metric": "p_set",
        "value": 2.5,
        "unit": "MW",
    }


COMPONENTS = {
    "Load": {"bus": "B1"},
    "Generator": {"bus": "B1"},
    "Line": {"bus0": "B1", "bus1": "B2", "x": 0.1},
    "Transformer": {"bus0": "B1", "bus1": "B2", "x": 0.1},
    "StorageUnit": {"bus": "B1"},
    "Store": {"bus": "B1"},
}


@pytest.mark.parametrize("component_class", sorted(records.INPUT_METRICS))
def test_static_facts_of_every_input_class(baseline, component_class):
    ctx = PyPSAService.get_active_context()
    if "B2" not in ctx.network.buses.index:
        ctx.network.add("Bus", "B2")
    metrics = records.INPUT_METRICS[component_class]
    values = {metric: 2.0 + i for i, metric in enumerate(metrics)}
    ctx.network.add(component_class, "X1", **COMPONENTS[component_class], **values)
    assert records._raw_facts(ctx, component_class, "X1") == [
        {
            "path": "/" + metric,
            "entity_id": "X1",
            "metric": metric,
            "value": values[metric],
            "unit": unit,
        }
        for metric, unit in metrics.items()
    ]


def test_series_step_of_a_name_with_pointer_characters(baseline, harness):
    name = "a/b~c"
    network().add("Load", name, bus="B1", p_set=1)
    set_series([4.0, 5.5, 6.0, 6.5])
    network().loads_t.p_set[name] = [4.0, 5.5, 6.0, 6.5]
    args = {"component": "loads", "name": name, "attribute": "p_set"}
    escaped = series_draft(entity=name, path=f"/a~1b~0c/{STEPS[1]}")
    assert approval(read_series(harness, escaped, args))["verdict"] == "passed"
    unescaped = series_draft(entity=name, path=f"/a/b~c/{STEPS[1]}")
    assert approval(read_series(harness, unescaped, args))["verdict"] == "limited"
