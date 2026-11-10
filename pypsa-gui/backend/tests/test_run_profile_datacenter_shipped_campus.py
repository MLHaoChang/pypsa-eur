# ruff: noqa: F811 - pytest fixtures (hub, user_and_db) are imported and re-bound as arguments
"""Study harness 10b, GK A CT-5c/CT-5d: what `dc.campus.contingency` reads on
the shipped data-centre templates, solved and run through the campus study
with the generic grid code published (kept apart: two real solves)."""

from __future__ import annotations

import pandas as pd
import pytest

from services import campus_electrical_service as ce
from services import run_actions, run_gates
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures
from tests.test_campus_scan_and_grid_code_since_run import _publish
from tests.test_run_profile_datacenter_staged import T, _cases, _contingency, _settle


# CT-5: what the shipped templates read, check by check, with the generic code published.
@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_on_the_shipped_templates_the_contingency_waits_for_the_users_bands(hub, tid):
    from services import project_registry

    n = T.BUILDERS[tid]()
    n.optimize(
        solver_name="highs",
        **({"multi_investment_periods": True} if len(n.investment_periods) else {}),
    )
    n.export_to_netcdf(str(project_registry.ensure_project_dir(hub) / "network.nc"))
    _publish(hub, "sagc_dc")
    ce.draft(hub)
    frozen = run_actions.freeze(hub)
    ce.run(hub, frozen["settings"])
    run = run_actions._project_dir(hub) / ce.SUBDIR / "run"
    table = pd.read_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV).set_index("check")
    read = {
        c: (table.at[c, "status_with_measures"], table.at[c, "source"])
        for c in run_actions.CONTINGENCY_CHECKS
    }
    assert read == {c: ("pass", "assumed") for c in run_actions.CONTINGENCY_CHECKS}
    step = {
        "id": "solve.3",
        "tool": run_actions.CAMPUS,
        "native": run_actions.KIND,
        "status": "completed",
        "frozen": frozen,
        "result": {"run_files": run_actions.run_files(run), "network_revision": 0},
    }
    result = _contingency(hub, step, revision=0)
    assert (
        result["verdict"] == "not_evaluated"
    )  # CT-5c: the generic code's voltage bands are assumed (D3)
    assert (
        "state the PCC voltage band" in result["reason"]
        and "state the campus voltage band" in result["reason"]
    )
    # The shipped campus has no parallel transformer, so no N-1 case was solved (E3).
    assert set(pd.read_csv(run / ce.cs.LF_PCC_CSV)["case"]) == {"intact"}
    # CT-5c/d: with the user's own bands and an N-1 case the pass names the resize the study bought.
    table = pd.read_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV)
    table.loc[table["check"].isin(["pcc_voltage", "campus_voltage"]), "source"] = "code"
    table.to_csv(run / ce.cs.COMPLIANCE_INVESTED_CSV, index=False)
    _cases(run)
    _settle(run, step)
    result = _contingency(hub, step, revision=0)
    resize = table.set_index("check").at["transformer_loading", "detail"]
    assert "MVA recommended" in resize
    assert result["verdict"] == "pass" and result["note"].startswith(
        run_gates.MEASURED_PASS + resize
    )
