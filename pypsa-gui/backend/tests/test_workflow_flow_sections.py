"""Harness workflow steps name their project-flow section (B1, design §3.6)."""
from __future__ import annotations

import importlib

import pytest

from services.flow_sections import SECTION_IDS

# The module, not the `registry()` function the package re-exports under its name.
registry = importlib.import_module("harness.workflows.registry")


@pytest.mark.parametrize("wf_id", ["hub-design", "build-network"])
def test_every_step_names_a_flow_section(wf_id):
    wf = registry.load_all()[wf_id]
    assert all(s.flow_section in SECTION_IDS for s in wf.steps), \
        [(s.id, s.flow_section) for s in wf.steps]


def test_hub_design_follows_the_flow_order():
    steps = registry.load_all()["hub-design"].steps
    order = [SECTION_IDS.index(s.flow_section) for s in steps]
    assert order == sorted(order)


def test_an_unknown_flow_section_fails_the_load(tmp_path):
    (tmp_path / "bad.md").write_text(
        "---\nid: bad\ntitle: Bad\nintent: x\nwhen: [expert]\norder: 1\n"
        "opening_request: go\nsteps:\n  - id: one\n    title: One\n"
        "    done_when: done\n    flow_section: nowhere\n---\n\n## Step: one\n\nDo it.\n")
    with pytest.raises(registry.WorkflowError, match="unknown flow_section 'nowhere'"):
        registry.load_all(tmp_path)


def test_a_step_without_a_flow_section_still_loads(tmp_path):
    (tmp_path / "plain.md").write_text(
        "---\nid: plain\ntitle: Plain\nintent: x\nwhen: [expert]\norder: 1\n"
        "opening_request: go\nsteps:\n  - id: one\n    title: One\n"
        "    done_when: done\n---\n\n## Step: one\n\nDo it.\n")
    assert registry.load_all(tmp_path)["plain"].steps[0].flow_section is None
