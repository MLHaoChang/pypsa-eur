"""Every staged template cycles its storage within each investment period
(gatekeeper review of #304): one representative week stands for 3-10 years,
so state of charge carried from one period into the next is not physical.
PyPSA's per-period flags default to False; `eh_site.stage_network` and the
staged data center set them."""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_site as S  # noqa: E402
import eh_templates as T  # noqa: E402

STAGED = sorted(t for t in T.BUILDERS if t.endswith("_staged"))


def _staged_networks():
    yield "eh_site", S.build_eh_site({"site_type": "campus"})     # a battery and a UPS
    for tid in STAGED:
        yield tid, T.BUILDERS[tid]()


def test_every_staged_template_sets_the_per_period_flags():
    seen = []
    for tid, n in _staged_networks():
        assert len(n.investment_periods) > 1, tid
        if len(n.storage_units):
            assert n.storage_units["cyclic_state_of_charge"].all(), tid
            assert n.storage_units["cyclic_state_of_charge_per_period"].all(), tid
        if len(n.stores):
            assert n.stores["e_cyclic"].all() and n.stores["e_cyclic_per_period"].all(), tid
        seen.append(tid)
    assert "eh_datacenter_staged" in seen


@pytest.mark.live_solve
def test_the_staged_data_center_ends_every_period_where_it_started(reset_backend, monkeypatch,
                                                                    tmp_path):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.pypsa_service import PyPSAService
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    tid = "eh_datacenter_staged"
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG[tid]))
    isl = load_island_config(tmp_path / SIDECAR_NAME)
    isl["cuts"] = []
    monkeypatch.setattr(I, "load_project_island", lambda: isl)
    install_network_into_backend(T.BUILDERS[tid]())
    cfg = _solver_config_from_dict(T.solver_config(tid))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal"), (status, cond)
    for su in n.storage_units.index:
        eta_s = float(n.storage_units.at[su, "efficiency_store"])
        eta_d = float(n.storage_units.at[su, "efficiency_dispatch"])
        for y in n.investment_periods:
            soc = n.storage_units_t.state_of_charge[su].loc[y]
            p0 = float(n.storage_units_t.p[su].loc[y].iloc[0])
            step = -p0 / eta_d if p0 > 0 else -p0 * eta_s
            assert float(soc.iloc[0]) == pytest.approx(float(soc.iloc[-1]) + step, abs=1e-3), \
                (su, y)
