# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02c: a weak-grid EH study started from chat (`run_eh_study`) or
`POST /api/results/eh_study` with no `pack_overrides.import_p_nom_mw` runs on the
network's connection limit (`flow_service.import_capacity`), never the pack's
constant. Where the network fixes no limit it refuses with #1626's plain
sentence; an explicit cap that is not exactly the network's is refused naming
both values. Only archetypes whose pack has an import cap (weak_flexible) fill.

`run_eh_study` is stubbed: the test reads the pack the worker receives."""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest
from fastapi import HTTPException

from models.energy_hub import (
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from project_templates.eh_templates import build_eh_datacenter
from services import flow_service as F

STUDY_URL = "/api/results/eh_study"
PACK_MW = default_weak_flexible_pack().import_overlay.import_p_nom_mw


def _imports(n):
    return n.links.eh_role == "grid_import"


def _network(mw=45.0):
    n = build_eh_datacenter()
    n.links.loc[_imports(n), "p_nom"] = mw
    return n


@pytest.fixture
def seen(monkeypatch):
    """The packs the stubbed study worker received."""
    import services.adequacy.eh_study as eh_mod

    packs: list = []

    def fake(network, pack, cfg, **kw):
        packs.append(pack)
        return {"stub": True}

    monkeypatch.setattr(eh_mod, "run_eh_study", fake)
    return packs


class _Via:
    """One way in: the HTTP route (over the authenticated TestClient) or the
    chat tool (called in-process, as the chat loop calls it)."""

    def __init__(self, via, request, install_network, n):
        self.via = via
        if via == "route":
            self.client = request.getfixturevalue("client")
        install_network(n)
        if via == "route":
            r = self.client.put(
                "/api/simulation/solver_config",
                json={"solver_name": "highs", "voll": 150.0},
            )
            assert r.status_code == 200, r.text
        else:
            from services.pypsa_service import PyPSAService

            state = PyPSAService.get_solver_state()
            state["solver_config"] = dataclasses.replace(
                state["solver_config"], solver_name="highs", voll=150.0
            )

    def record(self):
        if self.via == "route":
            r = self.client.get(STUDY_URL)
            return r.json() if r.status_code == 200 else None
        from services import chat_tools as T

        return T.get_adequacy_results("eh_study")

    def _wait(self, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rec = self.record()
            if rec and rec.get("status") not in ("running", "no_data"):
                return rec
            time.sleep(0.02)
        raise AssertionError("eh_study never finished")

    def start(self, archetype="weak_flexible", **kw):
        """Start the study; the 422 detail, or None once it has finished."""
        if self.via == "route":
            r = self.client.post(STUDY_URL, json={"archetype": archetype, **kw})
            if r.status_code == 422:
                return r.json()["detail"]
            assert r.status_code == 200, r.text
        else:
            from services import chat_tools as T

            try:
                T.run_eh_study(archetype=archetype, **kw)
            except HTTPException as exc:
                assert exc.status_code == 422
                return exc.detail
        self._wait()
        return None


@pytest.fixture
def make(request, install_network):
    return lambda via, n: _Via(via, request, install_network, n)


def test_the_pack_constant_is_not_the_network_value():
    # The test networks' 45 MW is not the pack's constant, so a fill that
    # fell back to it would show.
    assert PACK_MW is not None and PACK_MW != 45.0


VIAS = pytest.mark.parametrize("via", ["route", "chat"])


@VIAS
def test_no_cap_takes_the_networks_limit(make, seen, via):
    s = make(via, _network(45.0))
    assert s.start() is None
    assert len(seen) == 1
    assert seen[0].import_overlay.import_p_nom_mw == 45.0
    # The study record (a re-run's source) holds the filled value.
    assert s.record()["pack_overrides"]["import_p_nom_mw"] == 45.0


@VIAS
def test_an_equal_explicit_cap_passes(make, seen, via):
    s = make(via, _network(45.0))
    assert s.start(pack_overrides={"import_p_nom_mw": 45.0}) is None
    assert seen[0].import_overlay.import_p_nom_mw == 45.0


@VIAS
@pytest.mark.parametrize("asked", [50.0, 44.0, 45.000001])
def test_a_different_explicit_cap_is_refused(make, seen, via, asked):
    s = make(via, _network(45.0))
    detail = s.start(pack_overrides={"import_p_nom_mw": asked})
    assert detail is not None
    assert f"{asked:.12g} MW" in detail and "45 MW" in detail
    assert "change it on the network" in detail
    assert seen == []


def _no_link(n):
    # Every Link: with the tagged import gone, the selector's fallback would
    # take the site transformer Link as the import.
    n.remove("Link", n.links.index)
    return n


@VIAS
@pytest.mark.parametrize(
    "build, sentence",
    [
        (lambda: _network(0.0), F.CAP_ZERO),
        (lambda: _network(float("nan")), F.CAP_UNREADABLE),
        (lambda: _network(float("inf")), F.CAP_UNREADABLE),
        (lambda: _no_link(_network()), F.CAP_NO_LINK),
    ],
    ids=["zero", "nan", "inf", "no_link"],
)
def test_no_network_limit_is_refused(make, seen, via, build, sentence):
    s = make(via, build())
    assert s.start() == sentence
    # An explicit cap does not stand in for a limit the network does not fix.
    assert s.start(pack_overrides={"import_p_nom_mw": 45.0}) == sentence
    assert seen == []


def test_only_weak_flexible_has_an_import_cap():
    assert default_strong_grid_pack().import_overlay.import_p_nom_mw is None
    assert default_off_grid_pack().import_overlay.import_p_nom_mw is None


@VIAS
@pytest.mark.parametrize("archetype", ["strong_grid", "off_grid"])
def test_other_archetypes_do_not_fill(make, seen, via, archetype):
    # No import cap in their pack: they start on a network with no import
    # Link, and the worker's pack keeps None.
    s = make(via, _no_link(_network()))
    assert s.start(archetype=archetype) is None
    assert seen[0].import_overlay.import_p_nom_mw is None
    assert "import_p_nom_mw" not in (s.record()["pack_overrides"] or {})


def test_import_capacity_takes_no_network_lock(install_network):
    """`check_eh_study_start` reads the limit while it holds the network lock;
    the reader must not take a PyPSAService lock of its own (P5)."""
    from services.pypsa_service import PyPSAService

    n = install_network(_network(45.0))
    held, done = threading.Event(), threading.Event()

    def hold():
        with PyPSAService.get_lock():
            held.set()
            done.wait(timeout=10)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    try:
        assert held.wait(timeout=5)
        out: list = []
        reader = threading.Thread(
            target=lambda: out.append(
                F.import_capacity(n, {"archetype": "weak_flexible"})
            ),
            daemon=True,
        )
        reader.start()
        reader.join(timeout=5)
        assert out and out[0].mw == 45.0, "import_capacity waited on the network lock"
    finally:
        done.set()
        t.join(timeout=5)
