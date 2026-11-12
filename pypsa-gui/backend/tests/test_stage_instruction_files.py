"""Study harness 12c, G6: every stage that runs an agent turn has its
instruction file in the package (`harness/workflows/profiles/<id>/<stage>.md`).

The cases come from `run_profiles.PROFILES` itself, so a new profile, or a
new agent stage on an old one, is checked without editing this file. A
missing file would otherwise surface only when a run's first L1, L2 or
explore round asks `run_loops.instruction` for it.
"""

import pytest

from harness import run_loops
from services import run_profiles

AGENT_STAGES = sorted(
    (ref, stage)
    for ref in run_profiles.PROFILES
    for stage, spec in run_profiles.resolve(ref)["stages"].items()
    if spec.get("agent") is not None
)


def test_the_microgrid_profiles_are_enumerated():
    refs = {ref for ref, _ in AGENT_STAGES}
    assert {"eh_microgrid@1", "eh_microgrid_staged@1"} <= refs
    assert ("eh_microgrid@1", "result_gate") in AGENT_STAGES


@pytest.mark.parametrize(("profile_ref", "stage"), AGENT_STAGES)
def test_every_agent_stage_has_its_instruction_file(profile_ref, stage):
    text = run_loops.instruction(profile_ref, stage)
    assert text.strip(), f"{profile_ref} {stage}: the instruction file is empty"
