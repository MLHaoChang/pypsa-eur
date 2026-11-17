# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 15, grid_only@1: the pins that solve or load the shipped grid
templates (slow; the fast pins and their helpers are in
`test_run_profile_grid_only`, which the mutant runs target)."""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from tests.test_run_profile_grid_only import _both, _branch_carriers, _calc, _templates


# ── no solve moves a Lines network's own input identity ───


@pytest.mark.parametrize("template", ["3bus", "ieee14", "belgium", "ieee39"])
@pytest.mark.parametrize("with_pf", [False, True])
def test_a_solve_leaves_the_input_fingerprint_where_it_was(template, with_pf):
    import logging
    import pathlib
    import queue

    from services import workflow_tools
    from services.ac_pf_service import run_ac_pf_stage
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "project_templates"
        / template
        / "network.nc"
    )
    if not path.exists():
        pytest.skip(f"{template} is not built here")
    logging.disable(logging.CRITICAL)
    try:
        n = pypsa.Network(str(path))
        ctx = PyPSAService.build_context()
        ctx.network = n
        before = [
            workflow_tools._network_input_fingerprint(ctx, exact=e)
            for e in (False, True)
        ]
        status, _ = n.optimize(solver_name="highs")
        assert status == "ok"
        if with_pf:
            run_ac_pf_stage(n, SolverConfig(), queue.Queue())
        after = [
            workflow_tools._network_input_fingerprint(ctx, exact=e)
            for e in (False, True)
        ]
    finally:
        logging.disable(logging.NOTSET)
    assert before == after


@pytest.mark.parametrize("template", ["3bus", "ieee14", "belgium", "ieee39"])
def test_the_in_place_flow_adds_no_edit_and_leaves_the_branch_carriers(template):
    """GK B SH-15 (2) A3: the AC power flow runs in place on the solved network; it pushes
    no undo entry, moves no edit counter and leaves the saved branch carriers as the LP left them."""
    import logging
    import pathlib
    import queue

    from services.ac_pf_service import run_ac_pf_stage
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "project_templates"
        / template
        / "network.nc"
    )
    if not path.exists():
        pytest.skip(f"{template} is not built here")
    logging.disable(logging.CRITICAL)
    n = pypsa.Network(str(path))
    ctx = PyPSAService.build_context()
    ctx.network = n
    token = PyPSAService.bind_request_context(ctx)
    try:
        assert n.optimize(solver_name="highs")[0] == "ok"
        carriers = {
            c: n.static(c).get("carrier", pd.Series(dtype=object)).copy()
            for c in ("Line", "Transformer")
        }
        undo, revision = len(ctx.undo.stack), ctx.network_revision
        run_ac_pf_stage(n, SolverConfig(), queue.Queue())
        assert len(ctx.undo.stack) == undo and ctx.network_revision == revision
        for c, before in carriers.items():
            pd.testing.assert_series_equal(
                n.static(c).get("carrier", pd.Series(dtype=object)), before
            )
    finally:
        PyPSAService.reset_request_context(token)
        logging.disable(logging.NOTSET)


@pytest.mark.parametrize("exact", [False, True])
def test_cb_a_every_shipped_template_without_an_empty_branch_carrier_hashes_as_on_master(
    monkeypatch, exact
):
    import logging

    names = _templates()
    if not names:
        pytest.skip("no template is built here (project_templates/_build.py)")
    logging.disable(logging.CRITICAL)
    try:
        checked = []
        for name in names:
            from pathlib import Path

            n = pypsa.Network(
                str(
                    Path(__file__).resolve().parents[1]
                    / "project_templates"
                    / name
                    / "network.nc"
                )
            )
            carriers = _branch_carriers(n)
            if any("" in s for s in carriers.values()):
                continue  # an empty carrier is case (b)
            now, master = _both(n, monkeypatch, exact=exact)
            assert now == master, name
            checked.append(name)
    finally:
        logging.disable(logging.NOTSET)
    assert checked


@pytest.mark.parametrize("exact", [False, True])
def test_cb_b_a_shipped_grid_template_hashes_as_masters_same_network_with_ac(
    monkeypatch, exact
):
    """The four grid templates ship Lines with carrier '' (ieee39's Transformers have no
    carrier column): each hashes as master hashes it with those carriers set to 'AC'."""
    import logging
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "project_templates"
    logging.disable(logging.CRITICAL)
    try:
        checked = []
        for name in _templates():
            n = pypsa.Network(str(root / name / "network.nc"))
            if not any("" in s for s in _branch_carriers(n).values()):
                continue
            now, _ = _both(n, monkeypatch, exact=exact)
            for c in ("Line", "Transformer"):
                static = n.components[c].static
                if "carrier" in static.columns:
                    static.loc[static["carrier"] == "", "carrier"] = "AC"
            assert now == _both(n, monkeypatch, exact=exact)[1], name
            checked.append(name)
    finally:
        logging.disable(logging.NOTSET)
    if not checked:
        pytest.skip("no grid template is built here (project_templates/_build.py)")


# ── GK B (61) (3): an edit around the flow never reads as the solve's own PF ───


@pytest.fixture
def solved_3bus(install_network):
    """The real 3bus template, solved and recorded as a PF-on run records it (the LP's
    fingerprints from `_record_solve_inputs`, then the state the run leaves)."""
    import logging
    import pathlib

    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "project_templates"
        / "3bus"
        / "network.nc"
    )
    if not path.exists():
        pytest.skip("3bus is not built here")
    logging.disable(logging.CRITICAL)
    n = install_network(pypsa.Network(str(path)))
    ctx = PyPSAService.get_active_context()
    config = ctx.solver_state["solver_config"]
    config.run_ac_pf_after_lopf = True
    assert n.optimize(solver_name="highs")[0] == "ok"
    workflow_tools._record_solve_inputs(n, config)
    ctx.solver_state.update(
        status="completed", condition="optimal", lopf_results=None, ac_pf_results=None
    )
    yield n, ctx, config
    logging.disable(logging.NOTSET)


def _flow(n, ctx, config):
    import queue

    from services.ac_pf_service import run_ac_pf_stage
    from services.pypsa_service import PyPSAService

    with PyPSAService.get_lock():
        out = run_ac_pf_stage(n, config, queue.Queue())
    ctx.solver_state.update(**out, status="completed", condition="lopf+ac_pf_ok")
    return out


def test_a_revision_bump_after_the_flow_makes_the_real_run_not_current(solved_3bus):
    from services import dirty_state

    n, ctx, config = solved_3bus
    out = _flow(n, ctx, config)
    assert out["ac_pf_network_revision"] == int(ctx.network_revision or 0)
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is True
    dirty_state.bump_revision(ctx)
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is False


def test_an_edit_between_the_lp_and_the_flow_is_caught_by_the_input_fingerprint(
    solved_3bus,
):
    """The run holds the network lock from before the LP to after `n.pf()`
    (`solver_service.run_simulation`'s `with lock:`), so no edit lands there; an edit
    in that gap is simulated here. The flow then reads the edited counter, so the
    revision compare cannot catch it: the input fingerprint does."""
    from services import dirty_state, workflow_tools

    n, ctx, config = solved_3bus
    n.lines.loc[n.lines.index[0], "x"] *= 1.5
    dirty_state.bump_revision(ctx)
    out = _flow(n, ctx, config)
    assert out["ac_pf_network_revision"] == int(
        ctx.network_revision or 0
    )  # not the catcher
    assert n.meta[
        "assistant_solve_input_fingerprint"
    ] != workflow_tools._network_input_fingerprint(ctx)  # the catcher
    assert _calc.solve_matches_inputs(ctx, after_pf=True) is False
    assert (
        workflow_tools.solve_freshness(ctx, after_pf=True)["results_current"] is False
    )


def _saved_inputs(n):
    """Every saved field the flow must leave alone: each component's static and series
    attributes whose status is not an output (results are PF values by design, issue 50,
    and are not pinned here, the slack generator's p included)."""
    fields = {}
    for c in n.components:
        if c.name == "SubNetwork":
            continue  # topology PyPSA rebuilds; never saved
        status = c.defaults["status"].astype(str)
        keep = [a for a in c.static.columns if "Output" not in status.get(a, "")]
        fields[(c.name, "static")] = c.static[keep].copy()
        for a, frame in c.dynamic.items():
            if "Output" not in status.get(a, "Output") and not frame.empty:
                fields[(c.name, a)] = frame.copy()
    fields["meta"] = dict(n.meta)
    return fields


@pytest.mark.parametrize("template", ["3bus", "ieee14", "belgium", "ieee39"])
def test_the_flow_moves_no_saved_field_but_the_results(template):
    """GK B SH-15 (2) A3, option A: after the LP, the in-place flow changes no saved
    input, series input or meta; only result fields move."""
    import logging
    import pathlib
    import queue

    from services.ac_pf_service import run_ac_pf_stage
    from services.solver_service import SolverConfig

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "project_templates"
        / template
        / "network.nc"
    )
    if not path.exists():
        pytest.skip(f"{template} is not built here")
    logging.disable(logging.CRITICAL)
    try:
        n = pypsa.Network(str(path))
        assert n.optimize(solver_name="highs")[0] == "ok"
        before = _saved_inputs(n)
        run_ac_pf_stage(n, SolverConfig(), queue.Queue())
        after = _saved_inputs(n)
    finally:
        logging.disable(logging.NOTSET)
    assert after.keys() == before.keys()
    for key, frame in before.items():
        if key == "meta":
            assert after[key] == frame
        else:
            pd.testing.assert_frame_equal(after[key], frame, obj=str(key))
