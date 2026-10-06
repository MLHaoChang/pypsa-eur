"""
U2 WP2/WP4 — GS's export-cycling preflight expectations, re-expressed on a
COMPILED study fork (the commercial config bound on the network), where IC's
ported check (`commercial/preflight.py`, U1 f; owner decision 10) answers with
its commercial codes.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §5.3,
WP2 (the 7 export-cycling tests of `test_tariff_bill.py`, F1-B6's 4 among
them, and the seed-tariff acceptance).

After U2 the guided network carries no prices (`links_t.marginal_cost` stays
0): the import price is the PoC adders of the compiled tariff and the export
credit is `ic_export_price` (C3). So the codes a study discloses are
`commercial.arbitrage_loop` (same hour) and `commercial.arbitrage_loop_via_
storage` (across hours through site storage); GS's two raw codes stay IC's
answer for networks WITHOUT a commercial config (IC's own tests).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.study import Tariff
from tests.u2_targets import FAKE_REF, SEEDS, series_resolver

SAME = "commercial.arbitrage_loop"
CROSS = "commercial.arbitrage_loop_via_storage"



def _C():
    from services.study import compile as C

    return C


def _form(bands, *, export_price=None, series_ref=None) -> Tariff:
    return Tariff.model_validate(dict(
        tariff_id="t", name="toy", source="user", currency="EUR", currency_year=2026,
        energy_bands=bands, export={"price_per_mwh": export_price, "series_ref": series_ref}))


def _site(idx, *, storage: str | None = None, eta_rt: float = 0.9) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=100.0, p_min_pu=-1.0,
          eh_role="grid_supply")
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0, eh_role="grid_import")
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=10.0, eh_role="grid_export")
    n.add("Load", "l", bus="site", p_set=1.0)
    if storage == "su":
        n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, max_hours=2.0,
              efficiency_store=eta_rt ** 0.5, efficiency_dispatch=eta_rt ** 0.5)
    elif storage == "store":
        n.add("Store", "bess_store", bus="site", e_nom_extendable=True)
    return n


def _issues(n, form, export: pd.Series):
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    c = _C().commercial_from_form(form, n.snapshots, export_series=FAKE_REF)
    c = _C().bind_on_network(n, c, resolve_ref=series_resolver(export))
    assert "grid_import" not in n.links_t.marginal_cost.columns   # no prices in the network
    return validate_for_run(n, SolverConfig(commercial=c.config.model_dump(mode="json")))


def _same_hour(export_price: float, *, storage=None):
    idx = pd.date_range("2030-01-01", periods=24, freq="h")
    n = _site(idx, storage=storage)
    form = _form([{"label": "cheap", "price_per_mwh": 30.0, "applies": {"hours": [3]}},
                  {"label": "dear", "price_per_mwh": 80.0, "applies": {}}],
                 export_price=export_price)
    return _issues(n, form, pd.Series(export_price, index=idx))


def _cross_hour(*, storage: str | None = "su", eta_rt: float = 0.9):
    """
    A custom tariff no single hour flags (GS F1-B6 fixture): import 50
    before 06:00 and 120 after; a market-indexed export credit of 0 at night
    and 100 from 17:00 to 20:00. A battery charging at 50 and exporting at
    100 × 0.9 earns 40 per MWh.
    """
    idx = pd.date_range("2030-01-01", periods=48, freq="h")
    n = _site(idx, storage=storage, eta_rt=eta_rt)
    form = _form([{"label": "night", "price_per_mwh": 50.0, "applies": {"hours": list(range(6))}},
                  {"label": "day", "price_per_mwh": 120.0, "applies": {}}], series_ref="spot")
    credit = pd.Series(np.where((idx.hour >= 17) & (idx.hour <= 20), 100.0, 0.0), index=idx)
    return _issues(n, form, credit)


def _codes(issues) -> list[str]:
    return [i.code for i in issues if i.code in (SAME, CROSS)]


def test_an_export_credit_above_the_import_price_warns_on_the_compiled_fork():
    """Port of `test_preflight_warns_when_export_price_exceeds_import_price`."""
    hit = [i for i in _same_hour(40.0) if i.code == SAME]
    assert len(hit) == 1 and hit[0].severity == "warning"
    assert "grid_import" in hit[0].message or "grid_export" in hit[0].message
    assert _codes(_same_hour(25.0)) == []


def test_cycling_across_hours_through_storage_warns_on_the_compiled_fork():
    """
    Port of `test_preflight_flags_cycling_across_hours_through_storage`
    (F1-B6): a StorageUnit or a Store on the site bus shifts energy.
    """
    assert _codes(_cross_hour()) == [CROSS]
    assert _codes(_cross_hour(storage="store")) == [CROSS]


def test_the_study_keeps_the_cycling_codes_through_the_engines_filter():
    """
    Port of `test_the_study_keeps_both_cycling_codes_and_no_other_warning`
    (BC-F1-1): `preflight.cycling_flags` picks exactly the cycling codes.
    """
    from services.commercial.preflight import cycling_flags

    issues = _cross_hour()
    assert cycling_flags(issues) == [CROSS]
    assert cycling_flags(_same_hour(40.0)) == [SAME]


def test_cross_hour_cycling_needs_storage_and_a_gain_after_losses():
    assert _codes(_cross_hour(storage=None)) == []
    assert _codes(_cross_hour(eta_rt=0.4)) == []           # 100 × 0.4 = 40 < 50


def test_a_pair_flagged_in_the_same_hour_is_not_flagged_twice():
    assert _codes(_same_hour(40.0, storage="su")) == [SAME]


@pytest.mark.parametrize("tid", SEEDS)
def test_the_seed_tariffs_do_not_flag_cycling_with_a_battery_at_the_site(tid):
    """S4 acceptance on the compiled seeds (export at the seed's flat price)."""
    from services.study import library as L

    defaults = L.load_defaults()
    form = defaults.tariffs[tid]
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    n = _site(idx, storage="su")
    n.storage_units.at["bess", "max_hours"] = 4.0
    assert _codes(_issues(n, form, pd.Series(form.export.price_per_mwh, index=idx))) == []
