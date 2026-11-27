# ruff: noqa: F811 -- imported shared fixtures.
"""guided-results 04 (GK B (72) cond. 2, 3): renaming a figure moves no identity hash, and
the report's LCOE row is the checked nominal fact. Kept apart from `test_figure_words.py`,
which needs no project."""

from __future__ import annotations

from services import figure_words as FW
from services.reports import chapters
from services.reports import formatting as F
from tests.test_investment_assurance import private_store, project  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


# ── labels are not identity (GK B (72) cond. 2) ─────────────────────────────


def _renamed(monkeypatch):
    for fid in ("project_npv", "expected_shortfall", "lcoe_real", "lcoe_nominal"):
        monkeypatch.setitem(FW.FIGURES[fid], "name", f"renamed {fid}")


def test_the_investment_digest_and_the_network_fingerprint_do_not_read_a_name(
    project, monkeypatch
):
    from routers.results import _ic_current_assumptions
    from services.finance.investment_case_runner import assumptions_digest
    from services.workflow_tools import _network_input_fingerprint

    args = dict(
        finance={"wacc_nominal": 0.07},
        commercial=None,
        dispatch="d",
        packs={"tax": "t"},
    )
    before = (
        assumptions_digest(**args),
        _ic_current_assumptions(),
        _network_input_fingerprint(project),
    )
    _renamed(monkeypatch)
    assert (
        assumptions_digest(**args),
        _ic_current_assumptions(),
        _network_input_fingerprint(project),
    ) == before


def test_the_report_lcoe_is_the_checked_nominal_fact(project, monkeypatch):
    """The chapter row reads `lcoe_finance_consistent_eur_per_mwh`, which finance/report.py's
    own builder (`assemble_finance_sections`) fills. The engine here returns the lcoe_hand
    metrics (inflation 0.02, so real and nominal differ); the row then equals the checked
    nominal fact and not the real one, so a builder that read the real metric fails."""
    from services.finance import engine
    from tests.test_investment_assurance import LCOE, lcoe_hand, published, store

    hand_metrics = {"cash": {}, "years": [0, 1, 2]}
    lcoe_hand(hand_metrics, inflation=0.02)
    nominal, real = hand_metrics["lcoe_nominal_per_mwh"], hand_metrics["lcoe_real_per_mwh"]
    assert real is not None and abs(real - nominal) > 0.1  # a swap shows at 1 dp
    run = engine.run_case

    def engine_with_the_hand_lcoe(*args, **kwargs):
        result = run(*args, **kwargs)
        result.metrics.update(lcoe_nominal_per_mwh=nominal, lcoe_real_per_mwh=real)
        return result

    monkeypatch.setattr(engine, "run_case", engine_with_the_hand_lcoe)
    store(project, lcoe_hand)  # the report is built by finance/report.py, then stored
    rep = project.solver_state["investment_case_report"]
    facts = published(project, {"lcoe_nominal": nominal, "lcoe_real": real}, LCOE)
    rows = dict(
        map(
            tuple,
            chapters.investment_table(
                chapters.investment_payload(rep, stale=False), "x"
            ).rows,
        )
    )
    assert rows[FW.name("lcoe_nominal")] == F.fmt_number(
        facts["lcoe_nominal"], unit="USD/MWh", digits=1
    )
    assert rows[FW.name("lcoe_nominal")] != F.fmt_number(
        facts["lcoe_real"], unit="USD/MWh", digits=1
    )
