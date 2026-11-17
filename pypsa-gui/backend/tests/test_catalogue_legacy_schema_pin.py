"""
`GET /api/network/asset_schema/{component}` stays byte-identical while the
Parameter catalogue grows inside `services/asset_schema/` (spec expert-catalogue
§3.1, GK B C2). The fixture was recorded from master before the move; slice 03
moves the frontend off this route, and only then may the shape change.
"""

import json
import pathlib

import pytest

from services.asset_schema import fields as F

FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "asset_schema_payload_pre_catalogue.json"
)
RECORDED = json.loads(FIXTURE.read_text("utf-8"))


def test_every_component_is_recorded():
    assert sorted(RECORDED) == F.known_components()


@pytest.mark.parametrize("component", sorted(RECORDED))
def test_payload_byte_equal_to_the_recording(component):
    now = json.dumps(F.schema_payload(component), sort_keys=True, allow_nan=True)
    assert now == json.dumps(RECORDED[component], sort_keys=True, allow_nan=True)


@pytest.mark.parametrize("component", sorted(RECORDED))
def test_route_body_equal_to_the_recording(client, component):
    r = client.get(f"/api/network/asset_schema/{component}")
    assert r.status_code == 200
    assert r.json() == RECORDED[component]
    # Key order on the wire is part of "byte-identical".
    assert [list(f) for g in r.json()["groups"] for f in g["fields"]] == [
        list(f) for g in RECORDED[component]["groups"] for f in g["fields"]
    ]
