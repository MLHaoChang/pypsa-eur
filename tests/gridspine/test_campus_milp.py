"""The electrical asset choice as a MILP, solved in a loop of successive
linearisations with an adaptive convergence rate (plan C11).

The oracles do not run the code under test:
* sensitivities are checked against a central finite difference of an AC
  load flow on a network written directly in pandapower here;
* the joint optimum is a brute force over every combination of the tiny
  library, each built and solved directly in pandapower here;
* the polygon is checked against the circle numerically;
* the returned compliance is checked against a direct pandapower solve of
  the returned campus file with the returned dispatch.
"""
import math

import pandapower as pp
import pytest

from gridspine.static.campus_flow import SizingCriteria
from gridspine.static.campus_invest import open_candidates, select_assets
from gridspine.static.campus_reactive import ReactiveRequirement
from tests.gridspine.test_campus_invest import (
    PROFILE, WIDE, H7, comp, dark_hours, hourly, library, lowpf_spec, single_spec, tr,
)


# --------------------------------------------------------------------------
# the open candidate sets: C8's generators without C8's adequacy filters
# --------------------------------------------------------------------------

def test_open_candidates_are_c8s_generators_without_the_adequacy_filter(tmp_path):
    lib = library(tmp_path, transformers=[tr("T40", 40.0, 0.8e6), tr("T63", 63.0, 1.2e6)],
                  capacitor_banks=[comp("capacitor_banks", "CAP3", 3.0, 120e3)])
    table, sel = dark_hours((2030, 25.0))
    out = select_assets(lowpf_spec(existing=False), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    st = out["state"]
    by = {n.key: n for n in st.needs}
    lib_by_id = st.lib_by_id
    trafo = by["transformer TR1+TR2"]
    opened = open_candidates(trafo, lib, lib_by_id, SizingCriteria(), 50)
    # a redundant pair stays redundant: n = 2 or 3, every size, cheapest first
    assert sorted(c.items for c in opened) == sorted(
        (("transformer", i, n),) for i in ("T40", "T63") for n in (2, 3))
    assert {c.items for c in trafo.candidates} <= {c.items for c in opened}
    reactive = open_candidates(by["reactive"], lib, lib_by_id, SizingCriteria(), 50)
    assert reactive[0].label == "none" and reactive[0].items == ()
    assert sorted(c.label for c in reactive[1:]) == ["1 x CAP3", "2 x CAP3", "3 x CAP3"]
    # C8's own reactive need had a gap, so "none" was not among its candidates
    assert "none" not in {c.label for c in by["reactive"].candidates}
