# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Investment walk 2026-11-17, issue 01: the data-centre business case offers its
IT outputs through the routes.

The project is created from the template with the form's ``dc_load`` body; the
routes bind the solve with the spec that body stored (`linked_outputs.bind`),
so the preset's ``linked:contracted_it_kw`` and ``linked:it_energy`` lines
resolve with typed prices only."""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pypsa
import pytest

from tests.test_business_case_options import OPTIONS_URL
from tests.test_business_case_routes import BC_URL, PRESET_URL, _bc_poll, _put
from tests.test_dc_backup import _stage_dc
from tests.test_investment_case_routes import (
    STUDY_URL as IC_URL,
)
from tests.test_investment_case_routes import (
    _fake_build,
    _poll,
    _put_fin,
    _s2_inputs,
)

IT_OUTPUTS = {"contracted_it_kw", "it_energy"}


def _dc_project(client, tmp_path, monkeypatch, project_row, install_network, tid,
                dc_load=None):
    """A data-centre project created through the route (with the form's body when
    ``dc_load`` is a dict), its saved network solved and live. `fresh` is forced,
    as `solve_matches_inputs` reads it after a run through the solve route."""
    import dataclasses

    import routers.results as R
    from services import project_registry
    from services.business_case import linked_outputs as L
    from services.pypsa_service import PyPSAService
    from services.template_marker import read_marker

    _stage_dc(tmp_path, monkeypatch, tid)
    body = {"dc_load": dc_load} if dc_load is not None else None
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc_bc"},
                    json=body)
    assert r.status_code in (200, 201), r.text
    d = pathlib.Path(project_registry.project_dir(project_row(r.json().get("name", "dc_bc"))))
    n = pypsa.Network(str(d / "network.nc"))
    n.optimize(solver_name="highs")
    install_network(n)
    PyPSAService.get_active_context().storage_dir = str(d)
    monkeypatch.setattr(R, "_active_template_meta", lambda: read_marker(d))
    real = L.bind
    monkeypatch.setattr(
        L, "bind", lambda ctx, **kw: dataclasses.replace(real(ctx, **kw), fresh=True)
    )
    return d


def _typed_prices(bc: dict, kw_per_gpu: float | None = None) -> dict:
    """The preset with every empty price typed (a share takes none), and the contract
    indexation and typed quantity the preset leaves empty (walk issue 05, IB-B's: until it ships one)."""
    for ln in bc["inputs"]["lines"]:
        if ln.get("price") is None and ln.get("unit") != "share":
            ln["price"] = 100.0
        if ln.get("escalation") == "contract" and ln.get("indexation") is None:
            ln["indexation"] = 0.02
        if ln.get("quantity") is None and ln.get("quantity_source") in (None, "typed"):
            ln["quantity"] = 1000.0
    if kw_per_gpu is not None:
        bc["kw_per_gpu"] = kw_per_gpu
    return bc


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_options_offer_the_it_outputs_on_a_project_made_with_the_form(
    client, tmp_path, monkeypatch, project_row, tmp_projects_dir, install_network, tid
):
    _dc_project(client, tmp_path, monkeypatch, project_row, install_network, tid, {})
    body = client.get(OPTIONS_URL).json()
    assert body["outputs_reason"] is None, body["outputs_reason"]
    ids = {o["id"] for o in body["outputs"]}
    assert IT_OUTPUTS <= ids
    assert body["units_by_source"]["linked"]["contracted_it_kw"]["revenue"] == ["kW_month"]


@pytest.mark.parametrize(("variant", "kw_per_gpu"), [("colocation", None), ("gpu_cloud", 1.25)])
def test_the_dc_preset_runs_with_typed_prices_only(
    client, tmp_path, monkeypatch, project_row, tmp_projects_dir, install_network,
    variant, kw_per_gpu
):
    _dc_project(client, tmp_path, monkeypatch, project_row, install_network,
                "eh_datacenter", {})
    _fake_build(monkeypatch)
    assert _put_fin(client, _s2_inputs()).status_code == 200
    r = client.post(IC_URL, json={})
    assert r.status_code == 200, r.text
    assert _poll(client)["status"] == "done"

    r = client.get(PRESET_URL, params={"variant": variant})
    assert r.status_code == 200, r.text
    bc = _typed_prices(r.json()["business_case"], kw_per_gpu)
    linked = {ln["quantity_source"].split(":", 1)[1].split("@", 1)[0]
              for ln in bc["inputs"]["lines"]
              if (ln.get("quantity_source") or "").startswith("linked:")}
    assert linked & IT_OUTPUTS, linked
    r = _put(client, bc)
    assert r.status_code == 200, r.text
    kinds = [x["refusal"] for x in client.get(OPTIONS_URL).json()["refusals"]]
    assert "linked_output_missing" not in kinds, kinds
    assert client.post(BC_URL).status_code == 200
    out = _bc_poll(client)
    assert out["status"] == "done", {k: out.get(k) for k in ("error", "error_code", "message", "field")}
    outputs = out["report"]["outputs"]
    for ln in bc["inputs"]["lines"]:
        src = ln.get("quantity_source") or ""
        if src.split(":", 1)[-1].split("@", 1)[0] in IT_OUTPUTS:
            assert outputs[ln["name"]]["status"] == "ok", (ln["name"], outputs[ln["name"]])


def test_a_dc_project_without_the_form_body_offers_no_it_output(
    client, tmp_path, monkeypatch, project_row, tmp_projects_dir, install_network
):
    """No spec stored: the IT outputs are not offered (never read off a default spec)."""
    _dc_project(client, tmp_path, monkeypatch, project_row, install_network, "eh_datacenter")
    body = client.get(OPTIONS_URL).json()
    assert not IT_OUTPUTS & {o["id"] for o in body["outputs"]}


def test_bind_reads_no_spec_off_a_context_without_a_project():
    from services.business_case import linked_outputs as L

    ctx = SimpleNamespace(network=pypsa.Network(), solver_state={}, storage_dir=None)
    assert L._project_dc(ctx) is None
    ctx = SimpleNamespace(network=pypsa.Network(), solver_state={})
    assert L._project_dc(ctx) is None


def test_bind_keeps_an_explicit_dc(monkeypatch):
    from services.business_case import linked_outputs as L

    monkeypatch.setattr(L, "_project_dc", lambda ctx: ("spec", "UTC"))
    monkeypatch.setattr("harness.calculations.solve_matches_inputs", lambda ctx: True)
    ctx = SimpleNamespace(network=pypsa.Network(), solver_state={})
    assert L.bind(ctx).dc == ("spec", "UTC")
    assert L.bind(ctx, dc=None).dc is None
    assert L.bind(ctx, dc=("s", "Europe/Berlin")).dc == ("s", "Europe/Berlin")
