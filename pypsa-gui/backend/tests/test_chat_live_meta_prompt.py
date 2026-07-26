"""A4 — live network meta injected into the chat system prompt."""
from __future__ import annotations

import pandas as pd
import pypsa

from services import chat_service
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _tiny_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, s_nom=100)
    n.add("Load", "load1", bus="B1", p_set=10.0)
    n.add("Generator", "gen1", bus="B1", p_nom=50.0, marginal_cost=10.0)
    return n


def test_format_live_network_meta_unbound_guides_activate():
    ctx = ProjectContext(network=_tiny_network(), loaded_project=None)
    line = chat_service._format_live_network_meta(ctx)
    assert line is not None
    assert "No project is loaded" in line
    assert "activate_project" in line
    assert "list_projects" in line


def test_format_live_network_meta_includes_counts():
    n = _tiny_network()
    ctx = ProjectContext(network=n, loaded_project="DemoProj")
    line = chat_service._format_live_network_meta(ctx)
    assert line is not None
    assert "Working with DemoProj:" in line
    assert "2 buses" in line
    assert "1 lines" in line
    assert "3 snapshots" in line
    assert "solved=" in line


def test_build_system_prompt_includes_live_meta_when_provided():
    sess = chat_service.ChatSession()
    meta = "Working with DemoProj: 2 buses, 1 lines, 3 snapshots, solved=False."
    prompt = chat_service._build_system_prompt(sess, live_meta=meta)
    assert meta in prompt
    assert "<untrusted_data>" in prompt


def test_build_system_prompt_omits_live_meta_when_none():
    sess = chat_service.ChatSession()
    prompt = chat_service._build_system_prompt(sess, live_meta=None)
    assert "Working with" not in prompt


def test_format_live_network_meta_from_active_bound_context(install_network):
    """Bound active context yields a non-empty live-meta line."""
    install_network(_tiny_network(), name="BoundProj")
    ctx = PyPSAService.get_active_context()
    line = chat_service._format_live_network_meta(ctx)
    assert line is not None
    assert "BoundProj" in line
    assert "2 buses" in line
