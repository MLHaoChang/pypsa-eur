"""Study harness #42: a Link with a third port in one network must not break the next solve of a
network already in memory.

PyPSA 1.1 keeps one Link attribute table for the whole process. Adding a Link with ``bus2`` to any
network adds ``bus2``, ``efficiency2`` and ``p2`` to that table, and only that network gets the
matching frames. A network built earlier, still resident in the app's project registry, then fails
``consistency_check`` with ``KeyError: 'efficiency2'`` on its next solve. The pre-solve repair adds
the missing frames."""

from __future__ import annotations

import queue
import threading

import pypsa
import pytest

from services.solver.link_ports import ensure_link_port_frames


@pytest.fixture(autouse=True)
def _link_ports_stay_in_their_test():
    """Each test starts from a table with no extra Link ports (an earlier test file may have
    grown it, so ``_net()`` would already carry them and never be stale) and leaves the
    process-wide table exactly as it found it."""
    import re

    defaults = pypsa.Network().components.links.defaults
    saved = defaults.copy()
    extra = [
        a
        for a in defaults.index
        if (m := re.fullmatch(r"(bus|efficiency|p)(\d+)", a)) and int(m.group(2)) >= 2
    ]
    base = set(saved.index) - set(extra)
    defaults.drop(index=extra, inplace=True)
    try:
        yield
    finally:
        defaults.drop(index=[a for a in defaults.index if a not in base], inplace=True)
        for a in (
            extra
        ):  # PyPSA appends extra ports, so re-appending keeps the original order
            defaults.loc[a] = saved.loc[a]
        assert defaults.index.equals(saved.index)


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(2))
    n.add("Bus", ["a", "b", "c"])
    n.add("Generator", "g", bus="a", p_nom=5, marginal_cost=1)
    n.add("Link", "l", bus0="a", bus1="b", p_nom=1, marginal_cost=1)
    n.add("Load", "d", bus="b", p_set=0.5)
    return n


def _stale() -> pypsa.Network:
    """A resident network, then another network gets a three-port Link."""
    resident = _net()
    _net().add("Link", "three", bus0="a", bus1="b", bus2="c", p_nom=1)
    return resident


def test_stale_resident_network_fails_without_the_repair():
    """Guards the premise: if PyPSA fixes this upstream, this test says so."""
    with pytest.raises(KeyError, match="efficiency2"):
        _stale().consistency_check()


def test_repair_adds_the_missing_link_port_frames_and_the_network_solves():
    n = _stale()
    added = ensure_link_port_frames(n)
    assert sorted(added) == [
        "links.bus2",
        "links.efficiency2",
        "links_t.efficiency2",
        "links_t.p2",
    ]
    assert n.links.loc["l", "bus2"] == ""
    assert n.links.loc["l", "efficiency2"] == 1.0
    assert n.links_t.p2.index.equals(n.snapshots)
    n.consistency_check()
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    assert n.links_t.p0.loc[:, "l"].tolist() == pytest.approx([0.5, 0.5])


def test_repair_is_a_no_op_on_a_healthy_network():
    resident = _net()
    assert ensure_link_port_frames(resident) == []  # nothing grew the table yet
    _net().add("Link", "three", bus0="a", bus1="b", bus2="c", p_nom=1)
    assert ensure_link_port_frames(_net()) == []  # built after the table grew
    assert ensure_link_port_frames(resident)  # stale: repaired once
    assert ensure_link_port_frames(resident) == []  # then healthy


def test_run_simulation_solves_a_stale_resident_network():
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = _stale()
    PyPSAService.set_network(n)
    status, cond = run_simulation(
        SolverConfig(),
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **k: None,
    )
    assert status in ("ok", "optimal"), (status, cond)
    assert n.links_t.p0.loc[:, "l"].tolist() == pytest.approx([0.5, 0.5])


def test_every_port_the_table_names_is_repaired_bus3_and_bus4_too():
    resident = _net()
    other = _net()
    other.add("Bus", ["e", "f"])
    other.add("Link", "five", bus0="a", bus1="b", bus2="c", bus3="e", bus4="f", p_nom=1)
    added = ensure_link_port_frames(resident)
    for k in (2, 3, 4):
        assert {
            f"links.bus{k}",
            f"links.efficiency{k}",
            f"links_t.efficiency{k}",
            f"links_t.p{k}",
        } <= set(added)
    defaults = resident.components["Link"].defaults
    for attr in ("bus3", "efficiency3", "bus4", "efficiency4"):
        assert resident.links.loc["l", attr] == defaults.at[attr, "default"]
    resident.consistency_check()
    assert resident.optimize(solver_name="highs")[0] == "ok"


def test_the_repair_is_not_an_edit():
    """Fingerprint (so results and the IC digest stay current), revision and undo stack unchanged."""
    from services import dirty_state
    from services.project_context import ProjectContext
    from services.workflow_tools import _network_input_fingerprint

    n = _stale()
    ctx = ProjectContext(network=n)
    before = (
        _network_input_fingerprint(ctx),
        _network_input_fingerprint(ctx, exact=True),
        dirty_state.revision(ctx),
        len(ctx.undo.stack),
    )
    assert ensure_link_port_frames(n)
    after = (
        _network_input_fingerprint(ctx),
        _network_input_fingerprint(ctx, exact=True),
        dirty_state.revision(ctx),
        len(ctx.undo.stack),
    )
    assert after == before


def test_a_repaired_network_round_trips_through_netcdf(tmp_path):
    n = _stale()
    ensure_link_port_frames(n)
    assert n.optimize(solver_name="highs")[0] == "ok"
    p0, p1 = n.links_t.p0.copy(), n.links_t.p1.copy()
    path = tmp_path / "repaired.nc"
    n.export_to_netcdf(path)
    back = pypsa.Network(path)
    assert back.links_t.p0.to_numpy().tolist() == p0.to_numpy().tolist()
    assert back.links_t.p1.to_numpy().tolist() == p1.to_numpy().tolist()
    back.consistency_check()
    assert back.optimize(solver_name="highs")[0] == "ok"


def test_run_simulation_repairs_under_the_projects_lock(monkeypatch):
    from services.pypsa_service import PyPSAService
    from services.solver import link_ports
    from services.solver_service import SolverConfig, run_simulation

    lock = PyPSAService.get_lock()
    held = []
    real = link_ports.ensure_link_port_frames

    def spy(n):
        held.append(lock._is_owned())  # an RLock: held by this thread
        return real(n)

    monkeypatch.setattr(link_ports, "ensure_link_port_frames", spy)
    n = _stale()
    PyPSAService.set_network(n)
    status, cond = run_simulation(
        SolverConfig(),
        n,
        lock,
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **k: None,
    )
    assert status in ("ok", "optimal"), (status, cond)
    assert held and all(held)


# ── C1: no solve site can miss the repair ──────────────────────────────────

_SOLVE_CALLS = {
    "optimize",
    "pf",
    "lpf",
    "create_model",
    "optimize_security_constrained",
    "optimize_with_rolling_horizon",
}

#: Functions that solve but are reached only through ``run_simulation``, which repairs the
#: network under its lock before any of them runs (a copy taken there carries the frames).
_REACHED_THROUGH_RUN_SIMULATION = {
    ("services/solver/myopic.py", "_run_myopic_foresight"),
    ("services/solver/operational.py", "run_windows"),
    ("services/solver/operational_gap.py", "reference"),
    ("services/solver/process_uc.py", "_solve_window"),
}


def unrepaired_solve_sites(sources) -> list[str]:
    """``path:line function`` of every solve call that neither sits in an allow-listed
    function nor follows an ``ensure_link_port_frames`` call in the same function."""
    import ast

    out = []
    for rel, text in sources:
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
            repaired = [
                c.lineno
                for c in calls
                if getattr(c.func, "id", getattr(c.func, "attr", None))
                == "ensure_link_port_frames"
            ]
            # A solve is any reference to a solve method, called or not: ``opt = n.optimize``
            # then ``opt()`` (or ``getattr(n, "optimize")``) solves without a call on the attribute.
            refs = [
                node
                for node in ast.walk(fn)
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in _SOLVE_CALLS
                    # ``pypsa.options.params.optimize`` is a settings namespace, not a solve.
                    and getattr(node.value, "attr", None) != "params"
                )
                or (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in _SOLVE_CALLS
                )
            ]
            for c in refs:
                if (rel, fn.name) in _REACHED_THROUGH_RUN_SIMULATION:
                    continue
                if any(line < c.lineno for line in repaired):
                    continue
                out.append(f"{rel}:{c.lineno} {fn.name}")
    return sorted(set(out))


def _app_sources():
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    for root in ("services", "routers"):
        for path in sorted((backend / root).rglob("*.py")):
            yield path.relative_to(backend).as_posix(), path.read_text(encoding="utf-8")


def test_every_solve_site_repairs_the_link_port_frames_first():
    assert unrepaired_solve_sites(_app_sources()) == []


def test_the_guard_sees_a_site_whose_repair_was_removed():
    sources = dict(_app_sources())
    rel = "services/adequacy/island_survival.py"
    text = sources[rel].replace("ensure_link_port_frames(n)  #", "(n)  #", 1)
    assert text != sources[rel]
    sources[rel] = text
    assert any(s.startswith(rel) for s in unrepaired_solve_sites(sources.items()))


def test_the_guard_sees_an_aliased_solve():
    """``opt = n.optimize; opt()`` and ``getattr(n, "optimize")`` are solve sites too."""
    planted = {
        "alias": "def f(n):\n    opt = n.optimize\n    opt()\n",
        "getattr": "def f(n):\n    getattr(n, 'optimize')()\n",
    }
    for src in planted.values():
        assert unrepaired_solve_sites([("x.py", src)]), src
    repaired = (
        "def f(n):\n    ensure_link_port_frames(n)\n    opt = n.optimize\n    opt()\n"
    )
    assert unrepaired_solve_sites([("x.py", repaired)]) == []


def test_the_allow_list_names_functions_that_exist_and_solve():
    """A renamed or moved function must not leave a stale exemption behind."""
    import ast

    sources = dict(_app_sources())
    for rel, name in _REACHED_THROUGH_RUN_SIMULATION:
        fns = [
            f
            for f in ast.walk(ast.parse(sources[rel]))
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == name
        ]
        assert fns, (rel, name)
        assert any(
            isinstance(c, ast.Call) and getattr(c.func, "attr", None) in _SOLVE_CALLS
            for f in fns
            for c in ast.walk(f)
        ), (rel, name)
