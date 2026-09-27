"""
Common-mode import outages (plan
docs/superpowers/plans/2026-09-28-eh-zonal-mc-open-items.md, WP4).

The 2026-09-27 engine drew Link and grid outages independently. An event
that takes the PoC Link AND the grid behind it down together (a substation
fire, a storm on the shared corridor) was not modelled. It is now, from
opt-in data on the Link — ``common_mode_rate`` / ``common_mode_mttr_hours``
(``common_mode_basis`` optional, default ``FOR``) — and never defaulted.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from models.energy_hub import default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import mc as MC
from services.adequacy import mc_zonal as Z
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network
from tests.zonal_oracle import legacy_zonal_blocks

OVERLAY = default_strong_grid_pack().import_overlay
DRAWS = 400
SEED = 9


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def _weak(*, rate=None, mttr=None, basis=None, grid_sampled=True,
          firm_link=False, islanded=False):
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = 50.0
    if rate is not None:
        n.links["common_mode_rate"] = np.nan
        n.links.at["import_poc", "common_mode_rate"] = rate
    if mttr is not None:
        n.links["common_mode_mttr_hours"] = np.nan
        n.links.at["import_poc", "common_mode_mttr_hours"] = mttr
    if basis is not None:
        n.links["common_mode_basis"] = ""
        n.links.at["import_poc", "common_mode_basis"] = basis
    if not grid_sampled:
        n.add("Carrier", "grid_mix")
        n.generators.at["grid_supply", "carrier"] = "grid_mix"
        for c in ("outage_rate_value", "mttr_hours"):
            n.generators.at["grid_supply", c] = np.nan
    if firm_link:
        for c in ("outage_rate_value", "mttr_hours"):
            n.links[c] = np.nan
        n.links["outage_rate_basis"] = ""
    if islanded:
        n.links.at["import_poc", "p_max_pu"] = 0.0
    return n


def _blocks(z):
    return Z.simulate_zonal_blocks(z, draws=DRAWS, seed=SEED)


def _eue(blocks):
    return sum(v[1] for v in blocks.values())


def _oracle(z):
    a = Z.single_area(z)
    return legacy_zonal_blocks(z.hub, a.grid, a.import_idx, a.firm_import_mw,
                               a.delivery_ratio, draws=DRAWS, seed=SEED)


def _same(a, b):
    for k in a:
        np.testing.assert_array_equal(a[k][0], b[k][0])
        np.testing.assert_array_equal(a[k][1], b[k][1])


def test_no_common_mode_data_changes_nothing():
    frozen = _freeze(_weak())
    assert frozen.scope["import_common_mode"] == []
    _same(_blocks(frozen.zonal_inputs), _oracle(frozen.zonal_inputs))


def test_a_common_mode_event_raises_eue_draw_by_draw():
    base = _freeze(_weak())
    cm = _freeze(_weak(rate=0.05, mttr=24.0))
    [entry] = cm.scope["import_common_mode"]
    assert entry == {"link": "import_poc", "rate": 0.05, "mttr_hours": 24.0,
                     "basis": "FOR", "area": 0, "applied": True,
                     "reason": None}
    e_cm, e_base = _eue(_blocks(cm.zonal_inputs)), _eue(_blocks(base.zonal_inputs))
    assert np.all(e_cm >= e_base - 1e-9)
    assert e_cm.mean() > e_base.mean()


def test_the_basis_is_carried_when_given():
    cm = _freeze(_weak(rate=0.05, mttr=24.0, basis="EFORd"))
    assert cm.scope["import_common_mode"][0]["basis"] == "EFORd"


def test_a_zero_rate_draws_nothing_and_changes_nothing():
    """
    Q = 0 consumes no stream (the ``sample_capacity`` contract), so the
    run is bit-identical to having no common-mode data at all.
    """
    frozen = _freeze(_weak(rate=0.0, mttr=24.0))
    assert frozen.scope["import_common_mode"][0]["applied"] is True
    _same(_blocks(frozen.zonal_inputs), _oracle(frozen.zonal_inputs))


def test_a_rate_without_mttr_is_reported_not_guessed():
    frozen = _freeze(_weak(rate=0.05))
    [entry] = frozen.scope["import_common_mode"]
    assert entry["applied"] is False and "MTTR" in entry["reason"]
    _same(_blocks(frozen.zonal_inputs), _oracle(frozen.zonal_inputs))


def test_an_unusable_rate_refuses_the_snapshot_naming_the_link():
    frozen = _freeze(_weak(rate=1.5, mttr=24.0))
    assert frozen.mc_inputs is None
    assert "import_poc" in frozen.mc_error and "[0, 1)" in frozen.mc_error


def test_an_islanded_link_does_not_apply_its_common_mode():
    frozen = _freeze(_weak(rate=0.05, mttr=24.0, islanded=True))
    [entry] = frozen.scope["import_common_mode"]
    assert entry["applied"] is False and "islanded" in entry["reason"]
    assert frozen.zonal_inputs is None


def test_a_v1_hub_gains_the_event_through_the_two_area_engine():
    """
    Grid without occurrence data: no sampled area, but the common-mode
    chain still needs the two-area engine (the area is unbounded).
    """
    v1 = _freeze(_weak(grid_sampled=False))
    cm = _freeze(_weak(grid_sampled=False, rate=0.05, mttr=24.0))
    assert v1.zonal_inputs is None
    assert cm.zonal_inputs is not None
    assert cm.scope["import_model"] == "sampled_unit"
    assert cm.zonal_inputs.areas[0].grid is None
    e_cm = _eue(_blocks(cm.zonal_inputs))
    e_v1 = _eue(MC._simulate_blocks(v1.mc_inputs, draws=DRAWS, seed=SEED))
    assert np.all(e_cm >= e_v1 - 1e-9) and e_cm.mean() > e_v1.mean()


def test_the_copt_folds_the_event_into_the_link_rate():
    cm = _freeze(_weak(rate=0.05, mttr=24.0))
    base = _freeze(_weak())
    assert cm.copt_metrics["lole_hours"] > base.copt_metrics["lole_hours"]
    link = next(u for u in cm.screening_units if u.name == "link:import_poc")
    assert link.q == pytest.approx(1.0 - (1.0 - 0.03) * (1.0 - 0.05))


def test_a_firm_link_with_common_mode_becomes_a_screening_unit():
    cm = _freeze(_weak(firm_link=True, rate=0.05, mttr=24.0))
    base = _freeze(_weak(firm_link=True))
    assert cm.copt_metrics["lole_hours"] > base.copt_metrics["lole_hours"]
    assert cm.scope["import_units"] == {"link:import_poc": "import_poc"}
    # The MC keeps the firm block (its common-mode chain is sampled in the
    # engine), so no Link unit enters the MC fleet.
    assert "link:import_poc" not in [u.name for u in cm.mc_inputs.units]
