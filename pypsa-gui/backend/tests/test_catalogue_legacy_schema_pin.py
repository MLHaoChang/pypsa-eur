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
def test_route_bytes_equal_to_the_recording(client, component):
    """The exact bytes FastAPI's JSONResponse renders for the recorded master payload (key order,
    number formatting and all)."""
    r = client.get(f"/api/network/asset_schema/{component}")
    assert r.status_code == 200
    want = json.dumps(
        RECORDED[component],
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")
    assert r.content == want
