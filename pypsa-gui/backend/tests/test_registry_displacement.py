"""Displacement write-back: registering over a resident context must not strand it.

Mirrors the setup style of test_eviction.py — build a bound, non-empty ctx
directly and drive the registry, rather than standing up a full request.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _bus_network(bus_name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", bus_name)
    return n


def _bound_ctx(name: str) -> ProjectContext:
    ctx = PyPSAService.build_context()
    n = _bus_network(f"{name}_BUS")
    n.name = name
    ctx.network = n
    ctx.loaded_project = name
    return ctx


def test_register_saves_the_context_it_displaces(monkeypatch):
    saved: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        PyPSAService,
        "_save_evicted_ctx",
        staticmethod(lambda vid, vctx: saved.append((vid, vctx.loaded_project))),
    )

    first = _bound_ctx("alpha")
    second = _bound_ctx("alpha")
    PyPSAService.register("org:alpha", first)
    PyPSAService.register("org:alpha", second)

    assert saved == [("org:alpha", "alpha")], (
        "displacing a resident context must write it back before it becomes unreachable"
    )
    assert PyPSAService.get_context("org:alpha") is second


def test_reregistering_the_same_object_saves_nothing(monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(
        PyPSAService,
        "_save_evicted_ctx",
        staticmethod(lambda vid, vctx: saved.append(vid)),
    )

    ctx = _bound_ctx("beta")
    PyPSAService.register("org:beta", ctx)
    PyPSAService.register("org:beta", ctx)

    assert saved == [], "re-registering the same object is not a displacement"


def test_first_registration_saves_nothing(monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(
        PyPSAService,
        "_save_evicted_ctx",
        staticmethod(lambda vid, vctx: saved.append(vid)),
    )

    PyPSAService.register("org:gamma", _bound_ctx("gamma"))

    assert saved == [], "nothing was displaced"
