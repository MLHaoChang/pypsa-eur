"""Study harness issue 17: the profile-backed workflow files are generated.

A checked-in workflow that differs from the generator output means a profile
or a source changed without `python tools/generate_workflows.py`.
"""

import pytest
import yaml

from harness.workflows.registry import get as get_workflow
from tools import generate_workflows as gen

SOURCES = gen.sources()


def test_the_three_workflows_are_generated():
    assert [s.stem for s in SOURCES] == [
        "hub-design",
        "investment-decision",
        "run-study",
    ]


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.stem)
def test_checked_in_file_is_byte_equal_to_generator_output(source):
    checked_in = (gen.WORKFLOWS / source.name).read_bytes()
    assert checked_in == gen.generate(source).encode("utf-8"), (
        f"{source.name} is stale: run `python tools/generate_workflows.py` from pypsa-gui/backend"
    )


def test_editing_a_profile_without_regenerating_fails(monkeypatch):
    from services import run_profiles

    source = next(s for s in SOURCES if s.stem == "run-study")
    profile = run_profiles.resolve("eh_site@2")
    profile["stages"]["input_gate"]["rules"].append({"id": "in.extra.rule"})
    monkeypatch.setattr(run_profiles, "resolve", lambda ref: profile)
    assert (gen.WORKFLOWS / source.name).read_bytes() != gen.generate(source).encode(
        "utf-8"
    )


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.stem)
def test_stage_steps_name_their_gate_rule_ids(source):
    from services import run_profiles

    wf = get_workflow(source.stem)
    stage_of = {
        s["id"]: s.get("stage")
        for s in yaml.safe_load(source.read_text().split("---\n")[1])["steps"]
    }
    profile = run_profiles.resolve("eh_site@2")
    for step in wf.steps:
        stage = stage_of[step.id]
        if stage is None:
            continue
        for rule in profile["stages"][stage]["rules"]:
            assert rule["id"] in step.done_when
