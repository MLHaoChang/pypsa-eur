"""`POST /simulation/preflight` is unchanged by the project flow's refactor
(B1, gatekeeper point 1): its three gates moved into
`routers.simulation.preflight_validate`, which the flow shares.

The golden (`fixtures/preflight_pin.json`) was written by this test's
`--write` mode on master BEFORE the refactor (0dc6d87f), and the same test
passes after it: status, ok, the counts, the sorted issue codes and the
deferred payload, for every committed template, one broken network and an
in-flight solve.

Regenerate (only for an intended change): `PREFLIGHT_PIN_WRITE=1 pytest
tests/test_preflight_pin.py`.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from routers import simulation as sim_router

GOLDEN = pathlib.Path(__file__).resolve().parent / "fixtures" / "preflight_pin.json"
TEMPLATES = ["eh_datacenter", "eh_datacenter_staged", "eh_h2_hub", "eh_h2_hub_staged",
             "eh_microgrid", "eh_microgrid_staged", "eh_site"]


def _shape(r) -> dict:
    body = r.json()
    return {"status": r.status_code,
            **{k: body.get(k) for k in ("ok", "errors", "warnings", "deferred",
                                        "deferred_reason", "deferred_stuck")},
            "codes": sorted(i["code"] for i in body.get("issues", []))}


def _case(client, case, monkeypatch) -> dict:
    if case in TEMPLATES:
        kw = {"json": {"site_type": "industrial"}} if case == "eh_site" else {}
        r = client.post(f"/api/projects/from_template/{case}", params={"name": "pin"}, **kw)
        assert r.status_code == 200, r.text
    elif case == "broken":
        r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "pin"})
        assert r.status_code == 200, r.text
        r = client.put("/api/simulation/solver_config",
                       json={**client.get("/api/simulation/solver_config").json(),
                             "solve_strategy": "rolling", "rolling_horizon": 0})
        assert r.status_code == 200, r.text
    elif case == "in_flight":
        r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "pin"})
        assert r.status_code == 200, r.text
        monkeypatch.setattr(sim_router, "_solver_in_flight", lambda: True)
    return _shape(client.post("/api/simulation/preflight"))


@pytest.mark.parametrize("case", [*TEMPLATES, "broken", "in_flight"])
def test_preflight_response_is_pinned(client, tmp_projects_dir, monkeypatch, case):
    got = _case(client, case, monkeypatch)
    if os.environ.get("PREFLIGHT_PIN_WRITE"):
        data = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        data[case] = got
        GOLDEN.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        return
    assert got == json.loads(GOLDEN.read_text())[case]
