# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""A solve must not leave its mark on the next network built from the same template.

`_week()` once handed out the module-level SNAPSHOTS Index, which the template's
time-series frames then used as their own. A solve names an Index "snapshot" in place, so every
later eh_datacenter in the process hashed differently under the exact fingerprint (d2a in
test_energization_per_period.py failed depending on which tests shared a worker).
"""
from project_templates import eh_templates as T
from tests.test_energization_per_period import _fps, build_eh_datacenter


def test_solving_one_datacenter_leaves_the_next_ones_fingerprint_alone():
    before = _fps(build_eh_datacenter())
    solved = build_eh_datacenter()
    solved.optimize(solver_name="highs")
    assert T.SNAPSHOTS.name is None  # the shared constant is never renamed
    assert _fps(build_eh_datacenter()) == before


def test_each_build_gets_its_own_week_index():
    assert T._week() is not T.SNAPSHOTS
    assert T._week() is not T._week()
