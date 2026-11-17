# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02, owner decision (a): the connection limit is the import Links'
capacity. A weak-grid study takes ``import_p_nom_mw`` from the network through
one reader, `flow_service.import_capacity`: the run body (`run_eh.freeze`), its
admission (`run_eh.admit`), the readiness body and the Site card's facts
(`_connection`). Where the network fixes no limit it refuses in plain words,
never the template's constant (GK B C1-C6)."""

from __future__ import annotations

import json
import pathlib
import threading
import types

import pytest

from project_templates.eh_templates import (
    build_eh_datacenter,
    build_eh_datacenter_staged,
)
from services import flow_service as F

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "project_templates"


def _meta(tid: str) -> dict:
    return json.loads((TEMPLATES / tid / "eh_template.json").read_text())


WEAK = _meta("eh_datacenter")
TEMPLATE_MW = WEAK["pack_overrides"]["import_p_nom_mw"]


def _imports(n):
    return n.links.eh_role == "grid_import"


def _ctx(tmp_path, n, meta=WEAK):
    (tmp_path / F.TEMPLATE_FILE).write_text(json.dumps(meta))
    return types.SimpleNamespace(
        storage_dir=str(tmp_path),
        project_uuid="p",
        network=n,
        network_revision=0,
        solver_state_lock=threading.RLock(),
        solver_state={"solver_config": {}},
    )


def _freeze(tmp_path, n, meta=WEAK):
    from harness import run_eh

    return run_eh.freeze(_ctx(tmp_path, n, meta), {"eh_study": {"budget_solves": 2}}, 6)


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
    # As built, the network carries the template's capacity: nothing changes.
    assert F._request_for(meta, None, n)["pack_overrides"][
        "import_p_nom_mw"
    ] == pytest.approx(float(n.links.loc[_imports(n), "p_nom"].sum()))
    n.links.loc[_imports(n), "p_nom"] = 45.0
    body = F._request_for(meta, None, n)
    assert body["archetype"] == "weak_flexible"
    assert (
        body["pack_overrides"]["import_p_nom_mw"]
        == pytest.approx(45.0)
        != meta["pack_overrides"]["import_p_nom_mw"]
    )


def test_only_the_import_cap_moves_in_the_request():
    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = 45.0
    base = F._template_request(WEAK, None)
    body = F._request_for(WEAK, None, n)
    assert {
        k: v for k, v in body["pack_overrides"].items() if k != "import_p_nom_mw"
    } == {k: v for k, v in base["pack_overrides"].items() if k != "import_p_nom_mw"}
    assert {k: v for k, v in body.items() if k != "pack_overrides"} == {
        k: v for k, v in base.items() if k != "pack_overrides"
    }


def test_a_strong_grid_request_ignores_the_network():
    meta = {**WEAK, "recommended_archetype": "strong_grid"}
    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = (
        0.0  # no limit to read: a strong grid needs none
    )
    assert "import_p_nom_mw" not in (
        F._request_for(meta, None, n).get("pack_overrides") or {}
    )


# ── C1: one selector for the facts, readiness, the freeze and the run ─────────


def test_the_facts_the_freeze_and_the_run_take_the_same_import_links(tmp_path):
    """Untagged, the import Links are found by the PoC rule (eh_poc on the grid
    bus): two Links into the site, plus a Link with an import carrier that only
    the carrier rule would match. Every reader takes the same two Links and
    the same sum, and the run's pack applies to exactly them."""
    from services.adequacy import archetypes as arch

    n = build_eh_datacenter()
    n.links["eh_role"] = ""
    n.add("Link", "grid_import_b", bus0="grid", bus1="dc_mv", carrier="AC", p_nom=15.0)
    n.add("Link", "site_tie", bus0="dc_mv", bus1="it_bus", carrier="AC", p_nom=7.0)
    facts_cap, connection = F._connection(n, None, WEAK)
    assert (
        sorted(facts_cap.links) == ["grid_import", "grid_import_b"]
        and facts_cap.mw == 55.0
    )
    frozen = _freeze(tmp_path, n)["args"]
    assert frozen["pack_overrides"]["import_p_nom_mw"] == 55.0
    pack = F._pack_for(
        {"archetype": frozen["archetype"], "pack_overrides": frozen["pack_overrides"]}
    )
    assert sorted(arch.select_import_links(n, pack.import_overlay)) == sorted(
        facts_cap.links
    )
    assert "site_tie" not in connection


# ── C2, C3, C4: no limit the network doesn't fix ─────────────────────────────


def _refusal(n):
    with pytest.raises(F.ImportCapacityError) as exc:
        F._request_for(WEAK, None, n)
    return str(exc.value)


def test_an_unreadable_import_capacity_refuses(tmp_path, monkeypatch):
    n = build_eh_datacenter()
    # The fault after the network is built: its p_nom column can't be read.
    n.links["p_nom"] = n.links["p_nom"].astype(object)
    n.links.loc["grid_import", "p_nom"] = "forty"
    assert _refusal(n) == F.CAP_UNREADABLE
    assert F._connection(n, None, WEAK)[0].mw is None
    from harness import run_eh

    with pytest.raises(run_eh.FreezeError, match="can't be read from the network"):
        _freeze(tmp_path, n)
    with pytest.raises(run_eh.FreezeError, match="can't be read"):
        _freeze(tmp_path, None)


def test_a_selector_fault_refuses():
    class Broken:
        @property
        def links(self):
            raise RuntimeError("store unreadable")

    assert (
        F.import_capacity(Broken(), F._template_request(WEAK, None)).refusal
        == F.CAP_UNREADABLE
    )


def test_a_zero_limit_refuses_never_the_template_value(tmp_path):
    from harness import run_eh

    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = 0.0
    assert _refusal(n) == F.CAP_ZERO and "0 MW" in F.CAP_ZERO
    with pytest.raises(run_eh.FreezeError, match="0 MW"):
        _freeze(tmp_path, n)


def test_no_import_link_refuses_never_the_template_value(tmp_path):
    from harness import run_eh

    n = build_eh_datacenter()
    n.remove("Link", "grid_import")
    n.buses["eh_poc"] = False
    n.links.loc["site_transformer", "carrier"] = "site AC"  # no import carrier either
    assert _refusal(n) == F.CAP_NO_LINK
    with pytest.raises(run_eh.FreezeError, match="no grid import link"):
        _freeze(tmp_path, n)


def test_a_kw_grid_bus_refuses_rather_than_sum_raw():
    n = build_eh_datacenter()
    n.buses.loc["grid", "unit"] = "kW"
    n.links.loc["grid_import", "p_nom"] = 40_000.0
    message = _refusal(n)
    assert "grid_import" in message and "kW" in message and "MW" in message


def test_an_extendable_import_link_refuses():
    n = build_eh_datacenter()
    n.links.loc["grid_import", "p_nom_extendable"] = True
    message = _refusal(n)
    assert "grid_import" in message and "sized by the optimisation" in message


def test_readiness_says_the_limit_cannot_be_read():
    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = 0.0
    facts = types.SimpleNamespace(
        revision=1,
        voll=1.0,
        cfg_hash="x",
        cfg_dict={},
        copy=None,
        import_cap=F._connection(n, None, WEAK)[0],
    )
    result = F._readiness(facts, WEAK, "c2-test")
    assert isinstance(result, F.ImportCapacityError) and str(result) == F.CAP_ZERO
    [item] = F._readiness_items(result, voll_noted=False)
    assert item["severity"] == "error" and item["message"] == F.CAP_ZERO


# ── C5: a frozen limit the network no longer has stops the run ───────────────


def test_an_edit_after_the_freeze_stops_the_run(tmp_path):
    from harness import run_eh

    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = 45.0
    ctx = _ctx(tmp_path, n)
    args = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)["args"]
    assert run_eh._connection_limit_change(ctx, args) is None
    n.links.loc[_imports(n), "p_nom"] = 30.0
    stale = run_eh._connection_limit_change(ctx, args)
    assert "45 MW" in stale and "30 MW" in stale
    n.links.loc[_imports(n), "p_nom"] = 0.0
    assert F.CAP_ZERO in run_eh._connection_limit_change(ctx, args)


def test_admit_refuses_a_run_whose_limit_moved(tmp_path):
    from harness import run_eh

    n = build_eh_datacenter()
    ctx = _ctx(tmp_path, n)
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    step = {
        "id": "solve.2",
        "tool": "run_eh_study",
        "args": dict(frozen["args"]),
        "frozen": frozen,
        "status": "pending",
        "attempt_id": "a1",
    }
    task = {
        "task_id": "t1",
        "project_id": "p",
        "status": "running",
        "steps": [step],
        "run": {
            "profile": "eh_site@1",
            "status": "running",
            "stage": "solve",
            "stages": {},
        },
    }
    n.links.loc[_imports(n), "p_nom"] = 30.0
    kind, message = run_eh.admit(tmp_path / "task.json", task, step, step["args"], ctx)
    assert kind == run_eh.ARGS_KIND and "connection limit changed" in message


# ── C6: the network decides; the flow row carries it to the cards ────────────


def test_request_for_needs_the_network():
    import inspect

    assert (
        inspect.signature(F._request_for).parameters["n"].default
        is inspect.Parameter.empty
    )


def test_the_frozen_run_takes_the_edited_limit(tmp_path):
    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = 45.0
    args = _freeze(tmp_path, n)["args"]
    assert args["archetype"] == "weak_flexible"
    assert (
        args["pack_overrides"]["import_p_nom_mw"] == pytest.approx(45.0) != TEMPLATE_MW
    )
