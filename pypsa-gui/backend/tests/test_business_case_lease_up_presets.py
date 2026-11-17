"""
Business case 15 (GK B2 C1): a volume factor of 100 % leaves every preset fixture's
build byte-identical. Each preset module's own builds are re-run through a wrapper that
also builds with the factor stated as 1.0 in every operating year (and once only outside
the horizon) and compares the hash, the templates and the engine's P&L and KPIs with ==
against the build without it, which is master's code path.
"""

from __future__ import annotations

import numpy as np
import pytest

import tests.test_business_case_presets as PRE
import tests.test_business_case_presets_concentrator as CON
import tests.test_business_case_presets_h2_nh3 as H2
import tests.test_business_case_presets_ipp_microgrid as IPP
from services.finance.business_case import build_business_case, kpis, pnl
from services.finance.engine import run_case
from services.finance.timeline import build_timeline

PACK = PRE.PACK


def _same(a, b):
    assert a.hash == b.hash
    assert a.case.templates == b.case.templates
    assert a.case.assets == b.case.assets
    assert a.locked == b.locked
    ra, rb = run_case(a.case, PACK), run_case(b.case, PACK)
    pa, pb = pnl(ra), pnl(rb)
    assert list(pa.rows) == list(pb.rows)
    for k, v in pa.rows.items():
        if v is None:
            assert pb.rows[k] is None, k
        else:
            np.testing.assert_array_equal(pb.rows[k], v)
    assert kpis(ra, pa) == kpis(rb, pb)


@pytest.fixture
def checked(monkeypatch):
    """Patch each module's `build_business_case` to check the 100 % identity."""
    calls = []

    def wrapper(ic, inputs, *a, **kw):
        ref = build_business_case(ic, inputs, *a, **kw)
        ops = build_timeline(ic).operating_years
        for factor in (
            {int(y): 1.0 for y in ops},
            {int(ops[0]) - 1: 0.3, int(ops[-1]) + 1: 0.0},
        ):
            alt = inputs.model_copy(update={"volume_factor_by_year": factor})
            _same(ref, build_business_case(ic, alt, *a, **kw))
        calls.append(inputs)
        return ref

    for mod in (PRE, CON, H2, IPP):
        monkeypatch.setattr(mod, "build_business_case", wrapper)
    return calls


@pytest.mark.parametrize("variant", PRE.VARIANTS)
def test_c1_the_datacenter_presets_are_unchanged_at_full_volume(checked, variant):
    PRE.test_parity_a_preset_runs_as_the_same_lines_typed_by_hand(variant)
    assert len(checked) == 2


def test_c1_the_concentrator_preset_is_unchanged_at_full_volume(checked):
    CON.test_g3_a_shortfall_lowers_revenue_once()
    assert checked


@pytest.mark.parametrize("template", H2.CASES)
def test_c1_the_h2_and_ammonia_presets_are_unchanged_at_full_volume(checked, template):
    H2._run(template, 1000.0, 1.0)
    H2._run(template, 400.0, 0.5)
    assert len(checked) == 2


def test_c1_the_ipp_and_microgrid_presets_are_unchanged_at_full_volume(checked):
    IPP.test_the_ipp_business_case_revenue_includes_the_ppa_exactly_once()
    IPP.test_b4_unserved_microgrid_load_earns_no_tariff_and_no_voll_appears()
    assert len(checked) == 4
