# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02, owner decision (a): the connection limit is the import Links'
capacity. A weak-grid study takes ``import_p_nom_mw`` from the network, not the
template's constant, in the run body (`harness/run_eh.freeze` through
`_request_for(..., n)`) and the readiness body (`_readiness`, through
`_with_import_mw` and the facts' import capacity)."""

from __future__ import annotations

import json
import pathlib

import pytest

from project_templates.eh_templates import (
    build_eh_datacenter,
    build_eh_datacenter_staged,
)
from services import flow_service as F

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "project_templates"


def _meta(tid: str) -> dict:
    return json.loads((TEMPLATES / tid / "eh_template.json").read_text())


@pytest.mark.parametrize(
    "tid, build",
    [
        ("eh_datacenter", build_eh_datacenter),
        ("eh_datacenter_staged", build_eh_datacenter_staged),
    ],
)
def test_an_edited_limit_reaches_the_run_body(tid, build):
    meta = _meta(tid)
    n = build()
    template_mw = meta["pack_overrides"]["import_p_nom_mw"]
    # As built, the network carries the template's capacity: nothing changes.
    assert F._request_for(meta, None, n)["pack_overrides"][
        "import_p_nom_mw"
    ] == pytest.approx(
        float(n.links.loc[n.links.eh_role == "grid_import", "p_nom"].sum())
    )
    n.links.loc[n.links.eh_role == "grid_import", "p_nom"] = 45.0
    body = F._request_for(meta, None, n)
    assert body["archetype"] == "weak_flexible"
    assert (
        body["pack_overrides"]["import_p_nom_mw"] == pytest.approx(45.0) != template_mw
    )
    # Without the network (the parity copy of the frontend's body), the template's value.
    assert (
        F._request_for(meta, None)["pack_overrides"]["import_p_nom_mw"] == template_mw
    )


def test_the_readiness_body_takes_the_facts_import_capacity():
    base = F._request_for(_meta("eh_datacenter"), None)
    body = F._with_import_mw(base, 45.0)
    assert body["pack_overrides"]["import_p_nom_mw"] == 45.0
    # Only the import cap moves; the rest of the request is the template's.
    assert {
        k: v for k, v in body["pack_overrides"].items() if k != "import_p_nom_mw"
    } == {k: v for k, v in base["pack_overrides"].items() if k != "import_p_nom_mw"}
    assert {k: v for k, v in body.items() if k != "pack_overrides"} == {
        k: v for k, v in base.items() if k != "pack_overrides"
    }


def test_other_site_types_and_no_capacity_are_unchanged():
    strong = {"archetype": "strong_grid"}
    assert F._with_import_mw(strong, 45.0) is strong
    weak = {"archetype": "weak_flexible", "pack_overrides": {"import_p_nom_mw": 40.0}}
    assert F._with_import_mw(weak, None) is weak
    assert F._with_import_mw(weak, 0.0) is weak


def test_a_strong_grid_request_ignores_the_network():
    meta = {**_meta("eh_datacenter"), "recommended_archetype": "strong_grid"}
    n = build_eh_datacenter()
    n.links.loc[n.links.eh_role == "grid_import", "p_nom"] = 45.0
    assert "import_p_nom_mw" not in (
        F._request_for(meta, None, n).get("pack_overrides") or {}
    )


def test_the_frozen_run_takes_the_edited_limit(tmp_path):
    """The Run's frozen request (`run_eh.freeze`): what the study runs."""
    import threading
    import types

    from harness import run_eh

    (tmp_path / F.TEMPLATE_FILE).write_text(json.dumps(_meta("eh_datacenter")))
    n = build_eh_datacenter()
    n.links.loc[n.links.eh_role == "grid_import", "p_nom"] = 45.0
    ctx = types.SimpleNamespace(
        storage_dir=str(tmp_path),
        project_uuid="p",
        network=n,
        network_revision=0,
        solver_state_lock=threading.RLock(),
        solver_state={"solver_config": {}},
    )
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    assert frozen["args"]["archetype"] == "weak_flexible"
    assert frozen["args"]["pack_overrides"]["import_p_nom_mw"] == pytest.approx(45.0)
