# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""template-e2e issue 10: every study that needs a value of lost load refuses
VOLL 0 in the reliability study's plain words — the Solver settings label
first, then what it would run, then where to set it (RAW-NAMES-1) — and keeps
its typed refusal kind."""

import pytest
from fastapi import HTTPException

from services.solver_service import SolverConfig
from services.adequacy.coupling_loop_runner import (
    CouplingLoopRequest,
    start_coupling_loop,
)
from services.adequacy.eh_study_runner import VOLL_FIELD_LABEL, VOLL_REFUSAL
from services.adequacy.fmea_sweep_runner import FmeaSweepRequest, start_fmea_sweep
from services.adequacy.frontier_loop_runner import FrontierRequest, start_frontier


def _refuse(start, body):
    with pytest.raises(HTTPException) as exc:
        start(
            body,
            solver_state={"solver_config": SolverConfig(voll=0.0)},
            state_update=None,
            publish_study=None,
        )
    assert exc.value.status_code == 422
    return exc.value.detail


def test_the_contingency_sweep_refusal_names_the_field_and_the_sweep():
    detail = _refuse(start_fmea_sweep, FmeaSweepRequest())
    assert detail == (
        f"{VOLL_FIELD_LABEL}: must be above 0 to run the "
        "contingency sweep. Set it in Solver settings."
    )


def test_the_planning_loop_refusal_names_the_field_and_the_loop():
    detail = _refuse(start_coupling_loop, CouplingLoopRequest(target_lole_h=1.0))
    assert detail == (
        f"{VOLL_FIELD_LABEL}: must be above 0 to run the "
        "reliability-targeted planning loop. Set it in Solver settings."
    )


def test_the_frontier_refusal_names_the_field_and_keeps_its_unsupported_scope_kind():
    detail = _refuse(start_frontier, FrontierRequest())
    assert detail == {
        "error_kind": "unsupported_scope",
        "message": (
            f"{VOLL_FIELD_LABEL}: must be above 0 to run the cost vs "
            "availability study. Set it in Solver settings."
        ),
    }


def test_the_reliability_study_refusal_keeps_its_words():
    assert VOLL_REFUSAL == (
        f"{VOLL_FIELD_LABEL}: must be above 0 to run the "
        "reliability study. Set it in Solver settings."
    )
