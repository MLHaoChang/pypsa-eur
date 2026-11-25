"""A new project starts from the DEFAULT solver config, never the previous one's.

`PyPSAService.reset_network` carried the outgoing context's whole solver_state
(including `solver_config`) onto every "fresh" swap, so a blank project inherited
the previous project's tariff, finance and `multi_investment_periods`
(template-e2e issue 32). Parametrised over every path that builds a new project.
"""
from __future__ import annotations

import pypsa
import pytest

from services.solver_service import SolverConfig


def _dirty_config(client) -> None:
    from services.pypsa_service import PyPSAService
    cfg = PyPSAService.get_solver_state()["solver_config"]
    cfg.multi_investment_periods = True
    cfg.discount_rate = 0.123
    cfg.solver_options = {"threads": 7}


def _after_blank_http(client):
    r = client.put("/api/simulation/solver_config",
                   json={"multi_investment_periods": True, "discount_rate": 0.123})
    assert r.status_code == 200, r.text
    assert client.post("/api/network/reset").status_code == 200
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["multi_investment_periods"] is False
    assert cfg["discount_rate"] == SolverConfig().discount_rate


def _after_service_reset(client):
    from services.pypsa_service import PyPSAService
    PyPSAService.reset_network()


def _after_import(client):
    from services.pypsa_service import PyPSAService
    PyPSAService.reset_network(network=pypsa.Network())


@pytest.mark.parametrize(
    "path", [_after_blank_http, _after_service_reset, _after_import],
    ids=["blank-http-reset", "template-or-load-reset", "import-reset"])
def test_new_project_starts_from_default_solver_config(client, install_network, path):
    install_network(pypsa.Network())
    _dirty_config(client)
    path(client)

    from services.pypsa_service import PyPSAService
    got = PyPSAService.get_solver_state()["solver_config"]
    assert got == SolverConfig(), "new project inherited the previous solver config"


def test_in_place_replace_keeps_solver_config(client, install_network):
    """carry="same"/"copy" (undo, io import into the open draft) keep the config."""
    install_network(pypsa.Network())
    _dirty_config(client)
    from services.pypsa_service import PyPSAService
    PyPSAService.reset_network(carry="same")
    assert PyPSAService.get_solver_state()["solver_config"].multi_investment_periods
