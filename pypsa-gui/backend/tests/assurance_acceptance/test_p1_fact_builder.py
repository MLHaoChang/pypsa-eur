"""S3: one fact builder for the static, page, series, run and aggregate kinds.

The behaviour of each kind stays pinned by its own A1.x suite; this file pins
the builder's shape and the one spelling it changed (static's "/" + metric).
"""

from services import assurance_records as records


def test_static_path_is_unchanged_for_every_input_metric():
    for metrics in records.INPUT_METRICS.values():
        for metric in metrics:
            assert records._fact("L1", metric, 1.0, "MW", metric)["path"] == "/" + metric


def test_fact_names_entity_metric_value_unit_and_escaped_path():
    assert records._fact("a/b~c", "p_set", 2.5, "MW", "a/b~c", "2030", "t0") == {
        "path": "/a~1b~0c/2030/t0",
        "entity_id": "a/b~c",
        "metric": "p_set",
        "value": 2.5,
        "unit": "MW",
    }
