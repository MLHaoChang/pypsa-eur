"""A1 for get_timeseries: one cited snapshot of one input series."""

import json

import pandas as pd

from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import approval, public_text, text_turn, tool_turn


def set_profile(values, name="L1"):
    network = PyPSAService.get_active_context().network
    network.loads_t.p_set[name] = pd.Series(values, index=network.snapshots)


def series_draft(row, value, entity="L1"):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "series-row",
                    "kind": "observed",
                    "entity_id": entity,
                    "metric": "p_set",
                    "value": value,
                    "unit": "MW",
                    "source": {"tool_use_id": "read-series", "path": f"/data/{row}/0"},
                }
            ],
        }
    )


def read_series(harness, draft, name="L1", attribute="p_set", component="loads"):
    return harness.run(
        [
            tool_turn(
                "get_timeseries",
                {"component": component, "name": name, "attribute": attribute},
                call_id="read-series",
            ),
            text_turn(draft),
            text_turn(draft),
        ]
    )


def test_cited_series_row_has_snapshot_scoped_evidence(baseline, harness):
    set_profile([7.0, 9.0])
    approved = approval(read_series(harness, series_draft(1, 9)))
    assert approved["verdict"] == "passed"
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
    assert record["project_id"] == baseline[1]
    assert record["source_kind"] == "timeseries_input"
    assert record["scope"]["entity_id"] == "L1"
    assert record["scope"]["metric"] == "p_set"
    assert record["scope"]["rows"] == 2
