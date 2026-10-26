"""PR #727 screenshot data: copy to pypsa-gui/backend/tests/test_zz_pr727_dump.py, run with PR727_OUT=<file>, delete."""
import json
import math
import os

from gridspine.templates.grid_codes import load_grid_code
from services import campus_grid_code_service as gc
from services.adequacy import grid_readiness as G
from tests import test_grid_readiness as T
from tests.test_campus_grid_code_service import hub, user_and_db  # noqa: F401
from tests.test_process_flicker_fields import HEATER
from tests.test_process_harmonic_fields import _net


def test_dump(client, install_network, tmp_path, hub):  # noqa: F811
    out = {}
    install_network(_net())
    assert client.post("/api/network/links", json=HEATER).status_code in (200, 201)
    # The scorecard's no-step case lands on this unit: its step is not set.
    nostep = {k: v for k, v in HEATER.items() if k != "flicker_step_mva"}
    assert client.post("/api/network/links", json={**nostep, "name": "compressor", "flicker_changes_per_min": 2.0}
                       ).status_code in (200, 201)
    conveyor = {"name": "conveyor", "bus0": "hub", "bus1": "nh3", "carrier": "NH3", "p_nom": 2.0, "efficiency": 1.0}
    assert client.post("/api/network/links", json=conveyor).status_code in (200, 201)
    for k, path in (("links", "links"), ("buses", "buses"), ("carriers", "carriers"),
                    ("schema_link", "asset_schema/Link")):
        out[k] = client.get(f"/api/network/{path}").json()
    cases = {"gr_pass": T._flicker_site(step=10.0), "gr_warn": T._flicker_site(),
             "gr_nostep": T._flicker_site(step=math.nan), "gr_nosk": T._flicker_site(sk=math.nan)}
    for k, site in cases.items():
        out[k] = json.loads(json.dumps(G.grid_readiness(tmp_path / k, site, T.PROFILE), default=str))
    gc.new_draft(hub, "eu_flicker", "EU RfG/DCC CE (copy)", False)
    raw = load_grid_code(T.PROFILE, raw=True)
    raw["title"] = "EU RfG/DCC CE (copy)"
    gc.save_draft(hub, "eu_flicker", gc._dump(raw))
    out["draft"] = json.loads(json.dumps(gc.get_draft(hub, "eu_flicker"), default=str))
    out["grid_codes"] = json.loads(json.dumps(gc.list_grid_codes(hub), default=str))
    with open(os.environ["PR727_OUT"], "w") as f:
        json.dump(out, f, indent=1, allow_nan=False)
