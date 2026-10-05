"""
P22.9-BE bug 3 — a study or sweep must leave the live network's tables as the
user left them (spec 2026-09-27-guided-mode §2.1), plus the bug-2 backend
evidence (§2.2).

Found in the Expert click-through: after an FMEA sweep on the data-center
template every bus read ``control`` Slack, a ``sub_network`` id and a
``generator`` name it did not have before. The sweep solves the LIVE network in
place, and PyPSA's optimize post-processing runs ``determine_network_topology``
when no ``SubNetwork`` exists — writing those three Bus columns and adding
``SubNetwork`` rows. The closing base re-solve restored dispatch and
capacities, not those columns. ``preserve_bus_topology`` now wraps the whole
sweep, including that closing re-solve.

Every check here goes through HTTP (``TestClient``), so the network compared is
the one the session context resolves — the one the user's tables show.
"""
from __future__ import annotations

import time

import pytest

from tests.test_energy_hub_templates_e2e import _poll, _project_from_template

TID = "eh_datacenter"


_COMPONENTS = ("buses", "links", "generators")


def _tables(client) -> dict:
    out = {}
    for comp in _COMPONENTS:
        r = client.get(f"/api/network/{comp}")
        assert r.status_code == 200, r.text
        out[comp] = sorted(r.json(), key=lambda row: row["name"])
    return out


# The columns the sweep's closing base re-solve is MEANT to write: it is the
# user's own config solved (the same solve that leaves dispatch `fresh`, see
# the bug-2 test below), so the rows carry that solve's `*_nom_opt` exactly as
# a foreground solve would (accepted deviation, plan P22.9-BE phase note).
# They are optimisation OUTPUTS, not something the user set, and they are
# pinned separately rather than skipped: on a fixed asset `p_nom_opt` must
# equal the asset's own `p_nom`.
def _is_resolve_output(key: str) -> bool:
    return key.endswith("_nom_opt")


def _assert_tables_equal(after: dict, before: dict, *,
                         closing_resolve: bool = False) -> None:
    for comp in _COMPONENTS:
        assert [r["name"] for r in after[comp]] == [
            r["name"] for r in before[comp]], comp
        for a, b in zip(after[comp], before[comp]):
            diff = {k: (b.get(k), a.get(k)) for k in set(a) | set(b)
                    if not (closing_resolve and _is_resolve_output(k))
                    and a.get(k) != b.get(k)}
            assert not diff, f"{comp}/{a['name']} changed (before, after): {diff}"
    if closing_resolve:
        for comp in ("links", "generators"):
            for a in after[comp]:
                if not a["p_nom_extendable"]:
                    assert a["p_nom_opt"] == pytest.approx(a["p_nom"]), (
                        comp, a["name"])


def _start_sweep(client, name: str) -> None:
    reg = client.get(f"/api/projects/{name}/stress_scenarios").json()
    assert reg["error"] is None and reg["scenarios"]
    r = client.post("/api/results/fmea_sweep",
                    json={"scenarios": reg["scenarios"]})
    assert r.status_code == 200, r.text


def _run_sweep(client, name: str) -> dict:
    _start_sweep(client, name)
    sweep = _poll(client, "/api/results/fmea_sweep")
    assert sweep["status"] == "done", sweep
    assert sweep["base_restored"] is True, sweep
    return sweep


@pytest.fixture
def template(client, tmp_path, monkeypatch, tmp_projects_dir):
    return _project_from_template(client, tmp_path, monkeypatch, TID)


@pytest.mark.live_solve
def test_fmea_sweep_leaves_the_live_tables_equal(client, template, session_ctx):
    before = _tables(client)
    live = session_ctx(client).network
    sub_before = sorted(live.sub_networks.index)
    _run_sweep(client, template)
    _assert_tables_equal(_tables(client), before, closing_resolve=True)
    # No table serves SubNetwork, so the rows the topology pass added are
    # checked on the session's own network object.
    assert sorted(session_ctx(client).network.sub_networks.index) == sub_before


@pytest.mark.live_solve
def test_fmea_sweep_restores_topology_columns_after_abort(client, template):
    before = _tables(client)
    _start_sweep(client, template)
    r = client.post("/api/results/fmea_sweep/abort")
    assert r.status_code == 200, r.text
    sweep = _poll(client, "/api/results/fmea_sweep")
    # The stop event is checked between contingencies, after the base solve,
    # so the base solve (the one that writes the topology) always ran.
    assert sweep["status"] == "aborted", sweep
    # The closing re-solve still runs on the abort path.
    assert sweep["base_restored"] is True, sweep
    _assert_tables_equal(_tables(client), before, closing_resolve=True)


@pytest.mark.live_solve
def test_eh_study_leaves_the_live_tables_equal(client, template):
    # A GUARD: the study runs on a private copy and passes without the fix.
    # Kept so a future stage that solves the live object fails here.
    meta = client.get(f"/api/projects/{template}/eh_template").json()
    body = {"archetype": meta["recommended_archetype"],
            "stages": ["apply_pack", "ens_solve", "frontier", "fmea_top",
                       "assemble"],
            "budget_solves": 60}
    if meta["pack_overrides"]:
        body["pack_overrides"] = meta["pack_overrides"]
    before = _tables(client)
    r = client.post("/api/results/eh_study", json=body)
    assert r.status_code == 200, r.text
    study = _poll(client, "/api/results/eh_study")
    assert study["status"] == "done", study.get("error")
    _assert_tables_equal(_tables(client), before)


@pytest.mark.live_solve
def test_a_foreground_solve_still_works_after_a_sweep(client, template):
    _run_sweep(client, template)
    r = client.post("/api/simulation/run")
    assert r.status_code in (200, 202), r.text
    deadline = time.time() + 600
    while time.time() < deadline:
        st = client.get("/api/simulation/status").json()
        if st.get("status") in ("completed", "failed", "aborted"):
            break
        time.sleep(0.2)
    assert st["status"] == "completed", st
    assert st["condition"] == "optimal", st
    assert st["dispatch"] == "fresh", st


@pytest.mark.live_solve
def test_after_a_sweep_status_reports_dispatch_fresh_without_a_foreground_condition(
        client, template):
    # Bug 2's backend state, pinned: the sweep's closing base re-solve leaves
    # dispatch on the live network (`fresh`) but is not recorded as a
    # foreground solve (`condition` / `solve_time` null). The Expert greeting
    # (P22.9-FE) says exactly this; if the backend ever starts recording the
    # re-solve as a foreground solve, this fails and that copy is revisited.
    st = client.get("/api/simulation/status").json()
    assert st["condition"] is None and st["solve_time"] is None, st
    _run_sweep(client, template)
    st = client.get("/api/simulation/status").json()
    assert st["dispatch"] == "fresh", st
    assert st["condition"] is None, st
    assert st["solve_time"] is None, st


def test_preserve_bus_topology_restores_on_an_exception():
    # The unit contract under the HTTP tests: the columns come back and the
    # added SubNetwork rows go, even when the body raises.
    import pandas as pd
    import pypsa

    from services.adequacy.sweep import preserve_bus_topology

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", ["a", "b"])
    n.add("Generator", "g", bus="a", p_nom=10.0)
    n.add("Line", "l", bus0="a", bus1="b", x=0.1, s_nom=10.0)
    before = n.buses[["control", "sub_network", "generator"]].copy()
    line_before = n.lines["sub_network"].copy()
    gen_before = n.generators["control"].copy()
    assert n.sub_networks.empty
    with pytest.raises(RuntimeError, match="boom"):
        with preserve_bus_topology(n):
            n.determine_network_topology()
            n.buses.loc[:, "control"] = "Slack"
            n.generators.loc[:, "control"] = "Slack"
            assert not n.sub_networks.empty
            raise RuntimeError("boom")
    assert n.buses[["control", "sub_network", "generator"]].equals(before)
    assert n.lines["sub_network"].equals(line_before)
    assert n.generators["control"].equals(gen_before)
    assert n.sub_networks.empty


@pytest.mark.live_solve
def test_frontier_sweep_restores_bus_topology():
    # `run_frontier_sweep` solves the network it is handed in place (the
    # frontier route hands it the live one), closing re-solve included.
    import queue

    from services.adequacy.frontier import run_frontier_sweep
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    from tests.test_adequacy_frontier import VOLL, _network

    n = _network()
    PyPSAService.set_network(n)
    cols = ["control", "sub_network", "generator"]
    before = n.buses[cols].copy()
    res = run_frontier_sweep(
        n, PyPSAService.get_lock(),
        SolverConfig(solver_name="highs", voll=VOLL), [200.0, 50.0],
        log_queue=queue.SimpleQueue())
    assert res["base_restored"] is True, res
    assert n.buses[cols].equals(before), n.buses[cols]
    assert n.sub_networks.empty


@pytest.mark.live_solve
def test_coupling_loop_leaves_the_live_tables_equal(client, install_network):
    # The loop runners solve the LIVE network per iterate and in the closing
    # restore; the worker runs inside `preserve_bus_topology`.
    from tests.test_adequacy_coupling_endpoint import (
        DRAWS, LOOP_URL, SEED, _poll as _poll_loop, _setup)

    _setup(client, install_network)
    before = _tables(client)
    r = client.post(LOOP_URL, json={"target_lole_h": 4.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 2,
                                    "eps0": 50.0})
    assert r.status_code == 200, r.text
    body = _poll_loop(client, timeout=600.0)
    assert body["base_restored"] is True, body
    _assert_tables_equal(_tables(client), before, closing_resolve=True)


@pytest.mark.live_solve
def test_margin_loop_leaves_the_live_tables_equal(client, install_network):
    # Same wrapper pattern as the coupling loop (probe solve, iterates and the
    # closing restore all solve the LIVE network).
    from tests.test_adequacy_margin_loop import (
        DRAWS, LOOP_URL, SEED, _poll as _poll_loop, _setup)

    _setup(client, install_network)
    before = _tables(client)
    r = client.post(LOOP_URL, json={"target_lole_h": 4.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 2})
    assert r.status_code == 200, r.text
    body = _poll_loop(client, timeout=600.0)
    assert body["base_restored"] is True, body
    _assert_tables_equal(_tables(client), before, closing_resolve=True)


def _branchy_network():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "hv", v_nom=110.0)
    n.add("Bus", "mv1", v_nom=20.0)
    n.add("Bus", "mv2", v_nom=20.0)
    n.add("Generator", "grid", bus="hv", p_nom=100.0, marginal_cost=20.0)
    n.add("Transformer", "tr", bus0="hv", bus1="mv1", x=0.1, s_nom=80.0)
    n.add("Line", "ln", bus0="mv1", bus1="mv2", x=0.05, r=0.01, s_nom=60.0)
    n.add("Load", "ld", bus="mv2", p_set=30.0)
    return n


@pytest.mark.live_solve
def test_contingency_sweep_restores_branch_sub_network():
    # `determine_network_topology` also writes `sub_network` on every passive
    # branch (Line, Transformer); the sweep must put those back too.
    import queue

    from services.adequacy.sweep import run_contingency_sweep
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = _branchy_network()
    PyPSAService.set_network(n)
    bus_cols = ["control", "sub_network", "generator"]
    before_bus = n.buses[bus_cols].copy()
    before_branch = {c: n.static(c)["sub_network"].copy()
                     for c in sorted(n.passive_branch_components)}
    assert set(before_branch) >= {"Line", "Transformer"}
    res = run_contingency_sweep(
        n, PyPSAService.get_lock(),
        SolverConfig(solver_name="highs", voll=3000.0), [],
        log_queue=queue.SimpleQueue())
    assert res["base_restored"] is True, res
    assert n.buses[bus_cols].equals(before_bus), n.buses[bus_cols]
    for c, before in before_branch.items():
        after = n.static(c)["sub_network"]
        assert after.equals(before), (c, before.tolist(), after.tolist())
    assert n.sub_networks.empty


# ── P27a A1 — the restore runs under the runner's lock; live-network studies
# refuse edits (deferred spec 2026-09-28 §1.1) ─────────────────────────────

import threading  # noqa: E402

_RLockType = type(threading.RLock())


class _SpyLock(_RLockType):
    """A real RLock that records every acquire / release, and what the probe
    said at that moment — so a test can see WHAT ran between them."""

    def __init__(self, probe=lambda: None):
        super().__init__()
        self.events: list[tuple[str, object]] = []
        self._probe = probe

    def __enter__(self):
        r = super().__enter__()
        self.events.append(("enter", self._probe()))
        return r

    def __exit__(self, *exc):
        self.events.append(("exit", self._probe()))
        return super().__exit__(*exc)


def _topology_network():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", ["a", "b"])
    n.add("Generator", "g", bus="a", p_nom=10.0)
    n.add("Line", "l", bus0="a", bus1="b", x=0.1, s_nom=10.0)
    return n


def test_preserve_bus_topology_restores_under_the_passed_lock():
    from services.adequacy.sweep import preserve_bus_topology

    n = _topology_network()
    original = n.buses.at["a", "control"]
    spy = _SpyLock(probe=lambda: n.buses.at["a", "control"])
    with preserve_bus_topology(n, spy):
        n.determine_network_topology()
        n.buses.loc[:, "control"] = "Slack"
        assert spy.events == [], "the body must not run under the lock"
    # The write-back ran strictly between the acquire and the release: at
    # the acquire the solver's value is still there, at the release the
    # user's value is back.
    assert spy.events and spy.events[0] == ("enter", "Slack"), spy.events
    assert spy.events[-1] == ("exit", original), spy.events
    assert n.sub_networks.empty
    # lock=None keeps the old unlocked behaviour (the copy callers).
    spy2 = _SpyLock()
    with preserve_bus_topology(n):
        n.buses.loc[:, "control"] = "Slack"
    assert spy2.events == []
    assert n.buses.at["a", "control"] == original


def test_freeze_capacities_undo_runs_under_the_passed_lock():
    from services.adequacy.sweep import freeze_capacities

    n = _topology_network()
    n.generators.loc["g", "p_nom_extendable"] = True
    n.generators.loc["g", "p_nom_max"] = 50.0
    spy = _SpyLock(probe=lambda: float(n.generators.at["g", "p_nom_max"]))
    undo = freeze_capacities(n, spy)
    pinned = float(n.generators.at["g", "p_nom_max"])
    assert pinned != 50.0
    assert spy.events == [], "only the undo is locked"
    undo()
    assert spy.events and spy.events[0] == ("enter", pinned), spy.events
    assert spy.events[-1] == ("exit", 50.0), spy.events
    # lock=None: unlocked, still restores.
    undo2 = freeze_capacities(n)
    undo2()
    assert float(n.generators.at["g", "p_nom_max"]) == 50.0


class _LiveRecord:
    """A LIVE study record (real daemon thread: `record_is_running` tests
    `is_alive()`), installed in a GIVEN solver-state dict."""

    def __init__(self, state, key):
        self.state, self.key = state, key
        self.release = threading.Event()
        self.t = threading.Thread(target=self.release.wait,
                                  kwargs={"timeout": 30.0}, daemon=True,
                                  name=f"fake-{key}")

    def __enter__(self):
        self.t.start()
        self.state[self.key] = {"status": "running", "rows": [], "error": None,
                                "started_at": time.time(), "thread": self.t,
                                "stop_event": self.release}
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.t.join(timeout=5.0)
        self.state[self.key] = None


def _edit_net():
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=380.0, carrier="AC")
    n.add("Bus", "B2", v_nom=380.0, carrier="AC")
    n.add("Bus", "B3", v_nom=380.0, carrier="AC")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100.0)
    return n


def _bus_row(client, name):
    r = client.get("/api/network/buses")
    assert r.status_code == 200, r.text
    return next(b for b in r.json() if b["name"] == name)


def test_edit_during_a_sweep_is_refused_over_http(client, install_network,
                                                  session_state):
    install_network(_edit_net())
    before = _bus_row(client, "B1")
    with _LiveRecord(session_state(client), "fmea_sweep"):
        r = client.put("/api/network/buses/B1",
                       json={"name": "B1", "v_nom": 220.0})
        assert r.status_code == 409, r.text
        # The MIDDLEWARE refused it (its envelope carries `code`), before the
        # handler's own chat-path check could — every /api/network/* and
        # /api/io/* write meets this gate, not only the three CRUD handlers.
        assert r.json().get("code") == "study_in_flight", r.json()
        detail = r.json()["detail"]
        assert isinstance(detail, dict), detail
        assert detail["error_kind"] == "study_in_flight"
        assert detail["study"] == "fmea_sweep"
        assert detail["message"].startswith("Cannot edit the network while "
                                            "an FMEA sweep is running"), detail
        assert _bus_row(client, "B1") == before
    # …and once the sweep is gone the same edit goes through.
    r = client.put("/api/network/buses/B1", json={"name": "B1", "v_nom": 220.0})
    assert r.status_code == 200, r.text
    assert _bus_row(client, "B1")["v_nom"] == 220.0


def test_edit_during_an_eh_study_is_allowed(client, install_network,
                                            session_state):
    """`eh_study` solves `network.copy()` — it must not block an edit
    (kills LIVE_NETWORK_STUDIES → STUDY_KEYS)."""
    install_network(_edit_net())
    _bus_row(client, "B1")
    with _LiveRecord(session_state(client), "eh_study"):
        r = client.put("/api/network/buses/B1",
                       json={"name": "B1", "v_nom": 220.0})
        assert r.status_code == 200, r.text


def test_edit_during_an_mc_study_is_allowed(client, install_network,
                                            session_state):
    """`mc` snapshots under the lock and never mutates the network."""
    install_network(_edit_net())
    _bus_row(client, "B1")
    with _LiveRecord(session_state(client), "mc"):
        r = client.put("/api/network/buses/B1",
                       json={"name": "B1", "v_nom": 220.0})
        assert r.status_code == 200, r.text


def test_save_during_a_sweep_still_gets_the_save_sentence(client, api_project,
                                                          session_state):
    """`/api/projects/*` is NOT an edit prefix: a save keeps its own guard's
    sentence (kills widening `_STUDY_EDIT_PREFIXES` to `/api/projects/`)."""
    demo = api_project("demo")
    assert client.get(f"/api/projects/{demo}").status_code == 200
    with _LiveRecord(session_state(client), "fmea_sweep"):
        r = client.post(f"/api/projects/{demo}?force=true")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict), detail
        assert detail["error_kind"] == "study_in_flight"
        assert detail["message"].startswith("Cannot save the project"), detail
        assert not detail["message"].startswith("Cannot edit the network")


# The chat path never meets the middleware: the tools call the handlers in
# process. Harness = `test_chat_tools_dispatch.py`: `install_network(n,
# name=None)` + the autouse acting user, a bare `chat_tools.*` call, and the
# study record installed on the context THAT call resolves (outside a request:
# the active context, the same one `PyPSAService.get_network()` returns).

def _net_fingerprint(n):
    return (sorted(n.buses.index), sorted(n.lines.index),
            n.buses["v_nom"].to_dict())


def _chat_refusal(install_network, tool, args, kwargs):
    from fastapi import HTTPException

    from services import chat_tools
    from services.project_context import running_study_key
    from services.pypsa_service import PyPSAService

    n = install_network(_edit_net(), name=None)
    assert PyPSAService.get_network() is n, "the tool would resolve another ctx"
    before = _net_fingerprint(n)
    with _LiveRecord(PyPSAService.get_solver_state(), "fmea_sweep"):
        # The record is visible on the ctx the tool resolves.
        assert running_study_key(PyPSAService.get_solver_state()) == "fmea_sweep"
        with pytest.raises(HTTPException) as exc:
            getattr(chat_tools, tool)(*args, **kwargs)
        assert _net_fingerprint(PyPSAService.get_network()) == before, (
            f"{tool} changed the network while refusing")
    return exc.value


def test_edit_during_a_sweep_is_refused_from_the_chat_tool(install_network):
    err = _chat_refusal(install_network, "update_component", ("Bus", "B1"),
                        {"attrs": {"v_nom": 220.0}})
    assert err.status_code == 409, err.detail
    assert isinstance(err.detail, dict), err.detail
    assert err.detail["error_kind"] == "study_in_flight"
    assert err.detail["study"] == "fmea_sweep"
    assert err.detail["message"].startswith("Cannot edit the network")


# ── Every chat tool that reaches a network write, DERIVED (gate B1) ─────────
#
# The chat tools call their handlers in process, so the middleware never sees
# them; the three CRUD handlers are only part of the surface. The set is
# derived from the same rule as the dispatch seam's foreign-lock gate
# (`chat_tools._lock_gated_tool_names()`: every non-read tool that maps to a
# write route, plus the routeless mutators), narrowed to the study gate's
# prefixes, minus the tools that SWAP the network and keep their own guard.
# A tool added later against an `/api/network/*` route lands in this list —
# and fails below until it is refused — with nothing to remember.

_STUDY_PREFIXES = ("/api/network/", "/api/io/")
# Replace the whole network: `reset_network` (imports) and the re-cluster
# path refuse with the swap sentence over every study (Phase 11).
_SWAP_TOOLS = frozenset({"import_network_nc", "import_csv_bundle", "import_excel",
                         "import_matpower", "cluster_network"})


def _derived_chat_writes() -> list[str]:
    from services import chat_tools
    from services.chat_tools_schema import TOOL_ROUTES

    out = set()
    for name in chat_tools._lock_gated_tool_names():
        if name in _SWAP_TOOLS:
            continue
        if name in chat_tools._LOCK_GATE_SERVICE_CALL_MUTATORS:
            out.add(name)
            continue
        for route in TOOL_ROUTES.get(name, ()):
            if (isinstance(route, tuple) and route[0].upper() != "GET"
                    and route[1].startswith(_STUDY_PREFIXES)):
                out.add(name)
                break
    return sorted(out)


def _rich_net():
    """The gate reviewer's probe network: a day of hours, a load and an
    extendable generator, so the time-axis and profile tools have a target."""
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.add("Carrier", "AC")
    n.add("Bus", "B1", v_nom=380.0, carrier="AC", x=0, y=0)
    n.add("Bus", "B2", v_nom=380.0, carrier="AC", x=10, y=10)
    n.add("Bus", "B3", v_nom=380.0, carrier="AC", x=2, y=2)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100.0, length=5)
    n.add("Generator", "G1", bus="B1", p_nom=100, p_nom_extendable=True, carrier="AC")
    n.add("Link", "K1", bus0="B1", bus1="B3", p_nom=10, carrier="AC")
    n.add("Load", "D1", bus="B2", p_set=50)
    n.add("GlobalConstraint", "CO2", type="primary_energy", sense="<=",
          constant=500.0, carrier_attribute="co2_emissions")
    return n


def _rich_fingerprint(n):
    import pickle

    return pickle.dumps((
        n.buses.to_dict(), n.lines.to_dict(), n.generators.to_dict(),
        n.links.to_dict(), n.loads.to_dict(), n.carriers.to_dict(),
        n.global_constraints.to_dict(), list(n.snapshots),
        n.snapshot_weightings.to_dict(), list(n.investment_periods),
        {k: v.to_dict() for k, v in n.loads_t.items() if not v.empty},
        {k: v.to_dict() for k, v in n.generators_t.items() if not v.empty},
        {k: v.to_dict() for k, v in n.links_t.items() if not v.empty},
        getattr(n, "name", None)))


def _csv(n, col):
    import pandas as pd
    return pd.DataFrame({col: [0.42] * len(n.snapshots)}, index=n.snapshots).to_csv()


def _b64(text):
    import base64
    return base64.b64encode(text.encode()).decode()


# Real arguments per tool (the reviewer's probes, `qa27a/probe/`), so a tool
# that is NOT refused actually changes the network rather than failing on
# its arguments. The refusal happens before the handler, so after the fix
# the arguments no longer matter.
_WRITE_ARGS = {
    "create_component": lambda n: (("Bus", "B9", {"v_nom": 110.0}), {}),
    "create_carrier": lambda n: (("wind",), {}),
    "batch_create_components": lambda n: (("Bus", [{"name": "B9", "v_nom": 110.0}]), {}),
    "update_component": lambda n: (("Bus", "B1"), {"attrs": {"v_nom": 220.0}}),
    "bulk_update_components": lambda n: (("Bus", ["B1"], {"v_nom": 220.0}), {}),
    "delete_component": lambda n: (("Bus", "B3"), {}),
    "cascade_delete_bus": lambda n: (("B3",), {}),
    "batch_delete_components": lambda n: (("Bus", ["B3"]), {}),
    "undo_last": lambda n: ((), {}),
    "update_meta": lambda n: (("renamed",), {}),
    "recalculate_line_lengths": lambda n: ((), {}),
    "set_snapshots": lambda n: (("2025-01-01", "2025-01-02"), {}),
    "set_snapshot_weightings": lambda n: (({n.snapshots[0].isoformat(): {"objective": 2.0}},), {}),
    "upload_snapshot_weightings_csv": lambda n: ((_b64(
        __import__("pandas").DataFrame(
            {"objective": [2.0] * len(n.snapshots), "stores": [2.0] * len(n.snapshots),
             "generators": [2.0] * len(n.snapshots)}, index=n.snapshots).to_csv()),), {}),
    "sample_representative_weeks": lambda n: ((1,), {}),
    "set_multi_period_snapshots": lambda n: (([2030], "2025-01-01", "2025-01-02"), {}),
    "set_investment_periods": lambda n: (([2030],), {}),
    "set_investment_period_weightings": lambda n: (({"2030": {"objective": 2.0}},), {}),
    "set_vintage_bounds": lambda n: (("Generator", "G1", {"2030": {"p_nom_max": 5.0}}), {}),
    "delete_vintage_bounds": lambda n: (("Generator", "G1"), {}),
    "cleanup_orphan_vintages": lambda n: ((), {}),
    "upload_timeseries": lambda n: (("loads", "D1", "p_set", _csv(n, "D1")), {}),
    "generate_exemplary_timeseries": lambda n: (("loads", "D1", "p_set"), {}),
    "delete_timeseries": lambda n: (("loads", "D1", "p_set"), {}),
    "upload_load_profile": lambda n: ((_b64(_csv(n, "D1")),), {}),
    "upload_generator_profile": lambda n: ((_b64(_csv(n, "G1")),), {}),
    "upload_link_profile": lambda n: ((_b64(_csv(n, "K1")),), {"attribute": "p_max_pu"}),
    # Routeless mutators: their inputs are uploads this fixture has none of;
    # the refusal must come before any of that is read.
    "apply_demand_from_excel": lambda n: (("no-such-file", "t", "v", "D1"), {}),
    "reconstruct_network_from_image": lambda n: (("no-such-file",), {}),
}

# Tools whose effect needs something to act on first (outside the sweep).
_WRITE_SETUP = {
    "delete_timeseries": lambda: __import__("services.chat_tools", fromlist=["x"])
        .upload_timeseries("loads", "D1", "p_set", _csv(
            __import__("services.pypsa_service", fromlist=["x"])
            .PyPSAService.get_network(), "D1")),
}


def test_the_derived_chat_write_list_has_arguments_for_every_tool():
    """A tool added to the derived set must bring its own case here."""
    assert set(_derived_chat_writes()) == set(_WRITE_ARGS)


@pytest.mark.parametrize("tool", _derived_chat_writes())
def test_every_chat_network_write_is_refused_during_a_sweep(install_network, tool):
    """Through `chat_tools.DISPATCHERS` — the seam chat_service dispatches
    through — with the record on the ctx the tool resolves."""
    from fastapi import HTTPException

    from services import chat_tools
    from services.project_context import running_study_key
    from services.pypsa_service import PyPSAService

    n = install_network(_rich_net(), name=None)
    assert PyPSAService.get_network() is n
    if tool in _WRITE_SETUP:
        _WRITE_SETUP[tool]()
    args, kwargs = _WRITE_ARGS[tool](n)
    before = _rich_fingerprint(PyPSAService.get_network())
    with _LiveRecord(PyPSAService.get_solver_state(), "fmea_sweep"):
        assert running_study_key(PyPSAService.get_solver_state()) == "fmea_sweep"
        err = None
        try:
            chat_tools.DISPATCHERS[tool](*args, **kwargs)
        except HTTPException as e:
            err = e
        changed = _rich_fingerprint(PyPSAService.get_network()) != before
    assert not changed, f"{tool} changed the live network during a sweep"
    assert err is not None, f"{tool} was not refused during a sweep"
    assert err.status_code == 409, (tool, err.status_code, err.detail)
    assert isinstance(err.detail, dict), (tool, err.detail)
    assert err.detail["error_kind"] == "study_in_flight", (tool, err.detail)
    assert err.detail["study"] == "fmea_sweep"


def test_the_seam_study_gate_is_exactly_the_derived_set():
    from services import chat_tools

    assert chat_tools._study_gated_tool_names() == frozenset(_derived_chat_writes())


def test_read_tools_are_not_gated_and_still_answer_during_a_sweep(install_network):
    from services import chat_tools
    from services.chat_tools_schema import safety_tier_for
    from services.pypsa_service import PyPSAService

    gated = set(_derived_chat_writes())
    reads = {t for t in chat_tools.DISPATCHERS if safety_tier_for(t) == "read"}
    assert reads and not (reads & gated)
    assert not (reads & chat_tools._study_gated_tool_names())
    install_network(_rich_net(), name=None)
    with _LiveRecord(PyPSAService.get_solver_state(), "fmea_sweep"):
        buses = chat_tools.DISPATCHERS["list_components"]("Bus")
        assert "B1" in str(buses)
        assert chat_tools.DISPATCHERS["get_component"]("Bus", "B1")
        assert chat_tools.DISPATCHERS["get_meta"]() is not None


def test_swap_tools_keep_their_own_guard(install_network):
    """The imports and the re-cluster replace the whole network; they stay out
    of the edit gate and are refused by the swap guard's sentence instead."""
    import base64
    import tempfile

    from fastapi import HTTPException

    from services import chat_tools
    from services.pypsa_service import PyPSAService

    assert not (_SWAP_TOOLS & set(_derived_chat_writes()))
    assert not (_SWAP_TOOLS & chat_tools._study_gated_tool_names())
    n = install_network(_rich_net(), name=None)
    with tempfile.NamedTemporaryFile(suffix=".nc") as f:
        _topology_network().export_to_netcdf(f.name)
        data = base64.b64encode(open(f.name, "rb").read()).decode()
    with _LiveRecord(PyPSAService.get_solver_state(), "fmea_sweep"):
        with pytest.raises(HTTPException) as exc:
            chat_tools.DISPATCHERS["import_network_nc"](data)
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, str), exc.value.detail   # the swap sentence
    assert "an FMEA sweep" in exc.value.detail
    assert PyPSAService.get_network() is n


@pytest.mark.live_solve
def test_edit_after_a_sweep_is_kept(client, template):
    _run_sweep(client, template)
    after_sweep = _tables(client)
    bus = after_sweep["buses"][0]
    new_control = "PV" if bus["control"] != "PV" else "PQ"
    r = client.put(f"/api/network/buses/{bus['name']}",
                   json={"name": bus["name"], "control": new_control})
    assert r.status_code == 200, r.text
    # A restore that ran late would put the pre-sweep value back.
    time.sleep(0.5)
    now = _tables(client)
    after = next(b for b in now["buses"] if b["name"] == bus["name"])
    assert after["control"] == new_control, after
    # Everything else reads as it did right after the sweep. (`dispatch` on
    # /simulation/status goes `fresh` → `none` here, as after ANY edit: the
    # undo middleware invalidates dispatch on every /api/network/* write.)
    for comp in ("links", "generators"):
        assert now[comp] == after_sweep[comp], comp
    others = [b for b in now["buses"] if b["name"] != bus["name"]]
    assert others == [b for b in after_sweep["buses"] if b["name"] != bus["name"]]
    assert client.get("/api/simulation/status").json()["dispatch"] == "none"


def test_dtc_freezes_its_private_copy_without_the_lock():
    """`dtc` solves `nn = network.copy()`: nothing a foreground request can
    see, so its freeze / restore takes no lock — passing the runner's lock
    would serialise a private copy's undo against every foreground write for
    no protection. Pinned structurally (the spec's mutation target: "dtc.py
    passing the lock"); the DTC solve tests cannot tell the difference."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "services" / "adequacy"
           / "dtc.py").read_text(encoding="utf-8")
    calls = [c for c in ast.walk(ast.parse(src))
             if isinstance(c, ast.Call) and getattr(c.func, "id", None)
             in ("freeze_capacities", "preserve_bus_topology")]
    assert calls, "dtc no longer freezes its copy — revisit this pin"
    for c in calls:
        assert len(c.args) == 1 and not c.keywords, (
            f"dtc.py:{c.lineno} passes a lock to {c.func.id} on its private copy")


def test_the_live_study_call_sites_pass_the_runners_lock():
    """Gate finding 1: the four studies that solve the LIVE network hand
    their captured lock to the restore (spec §1.1 "Call sites"). The spy
    tests cover the functions; this pins the call sites, like the dtc pin."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "services" / "adequacy"
    want = {"sweep.py": {"preserve_bus_topology": 1, "freeze_capacities": 1},
            "frontier.py": {"preserve_bus_topology": 1},
            "coupling_loop_runner.py": {"preserve_bus_topology": 1},
            "margin_loop_runner.py": {"preserve_bus_topology": 1}}
    for fname, funcs in want.items():
        tree = ast.parse((root / fname).read_text(encoding="utf-8"))
        seen = {f: 0 for f in funcs}
        for c in ast.walk(tree):
            if isinstance(c, ast.Call) and getattr(c.func, "id", None) in funcs:
                seen[c.func.id] += 1
                assert len(c.args) == 2 and isinstance(c.args[1], ast.Name) \
                    and c.args[1].id == "lock", (
                        f"{fname}:{c.lineno} calls {c.func.id} without the runner's lock")
        assert seen == funcs, (fname, seen)


# Gate finding 2: the handler-level chokepoints the seam gate now shadows —
# bare `chat_tools.*` calls (in-process callers that bypass DISPATCHERS) still
# meet them: the global-constraint handlers, the bus cascade / rename and bulk.
_HANDLER_WRITES = [
    ("create_component", ("GlobalConstraint", "CO3"),
     {"attrs": {"type": "primary_energy", "sense": "<=", "constant": 1.0}}),
    ("update_component", ("GlobalConstraint", "CO2"), {"attrs": {"constant": 100.0}}),
    ("delete_component", ("GlobalConstraint", "CO2"), {}),
    ("update_component", ("Bus", "B1"), {"new_name": "B1_renamed"}),
    ("cascade_delete_bus", ("B3",), {}),
    ("bulk_update_components", ("Bus", ["B1"], {"v_nom": 220.0}), {}),
]


@pytest.mark.parametrize("tool,args,kwargs", _HANDLER_WRITES,
                         ids=[f"{t}-{a[0]}" for t, a, _ in _HANDLER_WRITES])
def test_the_handler_chokepoints_refuse_a_bare_call_during_a_sweep(
        install_network, tool, args, kwargs):
    from fastapi import HTTPException

    from services import chat_tools
    from services.pypsa_service import PyPSAService

    install_network(_rich_net(), name=None)
    before = _rich_fingerprint(PyPSAService.get_network())
    with _LiveRecord(PyPSAService.get_solver_state(), "fmea_sweep"):
        with pytest.raises(HTTPException) as exc:
            getattr(chat_tools, tool)(*args, **kwargs)
        assert _rich_fingerprint(PyPSAService.get_network()) == before
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "study_in_flight"
