"""
U2 WP8 part B — gate WP7 [N6]: the `BY_CONSTRUCTION` codes hold at the
centre only (the §4.6 identity at the LP optimum). A price bound is an
`option_case` on the re-dispatched variant network, at another tariff than
the one the battery was sized at, so its view must not carry them
(`option_case(..., centre=False)`; the driver's `demand_charge_price` low
bound is −61,423 EUR while its view said `npv_nonnegative_at_optimum`).

Mutation: `_price_bound` without `centre=False` → the findings test red;
`option_case` ignoring `centre` → the live test red.
"""
from __future__ import annotations

import types

import pytest

from tests.golden import site_fixture as sf
from tests.u2_targets import ic_case_option


def test_a_price_bound_values_its_case_off_the_centre(monkeypatch):
    from services.study import findings as F

    seen: dict = {}

    def case(ctx, n, cfg, ledger, tariff, bills, option_id, asset_economics=None, **kw):
        seen.update(kw, network=n)
        return "the bound's view"

    solved = object()
    monkeypatch.setattr(F, "_with_value", lambda ledger, key, value: ledger)
    monkeypatch.setattr(F, "_solver_config", lambda ctx, ledger, snapshots=None: "cfg")
    monkeypatch.setattr(F, "fixed_size_network", lambda n, drop_battery=False: n)
    monkeypatch.setattr(F, "_solved", lambda solve, net, cfg, variant_id: solved)
    monkeypatch.setattr(F, "_on_engine", lambda ctx, n: True)
    monkeypatch.setattr(F, "_case", case)
    # an engine-solved variant on a study context (`_on_engine` stubbed above)
    ctx = types.SimpleNamespace(ledger=None, export_series={"ref": "x"})
    monkeypatch.setattr(F.study_engine, "engine_ready", lambda n: True)
    n_centre = types.SimpleNamespace(snapshots=None)
    out = F._price_bound(ctx, n_centre, "bess_2h", "demand_charge_price", 1.0, None, "v",
                         reference=False)
    assert out == "the bound's view"
    assert seen["network"] is solved
    assert seen["centre"] is False and seen["keep"] is False


def test_the_findings_case_passes_centre_to_the_engine(monkeypatch):
    from services.study import findings as F

    seen: dict = {}

    def option_case(*a, **kw):
        seen.update(kw)
        return types.SimpleNamespace(view="v")

    monkeypatch.setattr(F, "_on_engine", lambda ctx, n: True)
    monkeypatch.setattr(F, "_compiled", lambda ctx, ledger, snapshots: "compiled")
    monkeypatch.setattr(F.study_engine, "option_case", option_case)
    ctx = types.SimpleNamespace(study_id="s", fidelity=None, question=None,
                                study_currency_year=None, bundles={})
    n = types.SimpleNamespace(snapshots=None)
    F._case(ctx, n, "cfg", None, None, {}, "bess_2h", keep=False, centre=False)
    assert seen["centre"] is False
    F._case(ctx, n, "cfg", None, None, {}, "bess_2h")
    assert seen["centre"] is True


@pytest.mark.live_solve
def test_an_off_centre_view_carries_no_by_construction_code():
    from services.study import engine_adapter as A

    n, cfg, compiled, ledger = ic_case_option("bess_2h")
    kw = dict(compiled=compiled, option_id="bess_2h", study_id=sf.SITE_STUDY_ID,
              fidelity="full_study")
    centre = A.option_case(n, cfg, ledger, **kw).view
    off = A.option_case(n, cfg, ledger, centre=False, **kw).view
    assert set(A.BY_CONSTRUCTION) <= set(centre.honesty_notes)
    assert not set(A.BY_CONSTRUCTION) & set(off.honesty_notes)
    # Only the claim moves: the same notes otherwise, the same figures.
    assert [c for c in centre.honesty_notes if c not in A.BY_CONSTRUCTION] == \
        list(off.honesty_notes)
    assert off.kpis == centre.kpis and off.years == centre.years
