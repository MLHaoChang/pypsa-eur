"""
The MARGIN loop — `services/adequacy/margin_lever.py` + GET/POST
`/results/margin_loop` (+ `/abort`), margin-loop spec §§1–2 and §4.

The controller underneath is `run_coupling_loop`, UNCHANGED (spec §0): the
margin is driven through the substitution ``x = 1/(1+m)``, which makes every
comparison in `coupling.py` — the multiplicative shrink, ``assert e > 0``, the
geometric midpoint, the ``miss > met`` test and the ``(cost, -x)`` tie-break —
correct for a lever that gets STRICTER as it grows. So the tests here are
about the two things the substitution does not buy: the ROUTE's bindings
(``cap_mwh=None``, the margin's own ``binding``, the pre-positioned ``x0``,
the ``validation_failed`` mapping, the payload translation) and the refusals
that must happen before a single solve is spent.

Driven over the REAL HTTP stack with an authenticated TestClient, never by
calling the handler functions directly — a direct call bypasses auth, CSRF and
the per-session project context the worker thread's state writes have to agree
with.
"""
from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager

import pandas as pd
import pypsa
import pytest

from services.adequacy.coupling import MAX_LOOP_SOLVES
from services.adequacy.margin_lever import (
    MAX_MARGIN,
    STEP_OVERSHOOT,
    format_lever_value,
    to_margin,
    to_x,
)
from services.adequacy.mc import MAX_DRAWS, MC_WARNING_V1

LOOP_URL = "/api/results/margin_loop"
ABORT_URL = "/api/results/margin_loop/abort"

VOLL = 3000.0
DRAWS = 16
SEED = 5


# ══ §1 — the substitution ═════════════════════════════════════════════════

def test_the_substitution_round_trips_and_is_strictly_antitone():
    """★ A5 (spec §1): the phase's only new mathematics, pinned.

    Bite: return ``1/(1+m)`` from ``to_margin`` too (a plausible copy-paste)
    — the round trip then holds only at m = 0 and the ordering inverts, which
    would send the controller's every comparison the wrong way while every
    other test still saw a plausible-looking number on the wire.
    """
    ms = [0.0, 1e-9, 1e-4, 0.01, 0.05, 0.1, 0.15, 0.3, 0.5, 1.0, 1.5,
          2.0, 3.0, 4.0, 4.999, 5.0]
    for m in ms:
        x = to_x(m)
        assert 0.0 < x <= 1.0, (m, x)
        assert to_margin(x) == pytest.approx(m, abs=1e-12, rel=1e-12)
    # strict antitone, both directions
    for m1, m2 in zip(ms, ms[1:]):
        assert m1 < m2
        assert to_x(m1) > to_x(m2), (m1, m2)
    for m1 in ms:
        for m2 in ms:
            if m1 < m2:
                assert to_x(m1) > to_x(m2)
            elif m1 == m2:
                assert to_x(m1) == to_x(m2)


def test_the_substitution_refuses_its_out_of_domain_inputs():
    """m < 0 is not a margin and x <= 0 is not a point of the search; both are
    a caller bug, and a silent NaN would reach `dataclasses.replace` and be
    read as "no standard at all" by `_prm_margin`."""
    for bad in (-1e-12, -0.1, -5.0):
        with pytest.raises(ValueError):
            to_x(bad)
    for bad in (0.0, -1e-12, -1.0):
        with pytest.raises(ValueError):
            to_margin(bad)
    with pytest.raises(ValueError):
        to_x(float("nan"))
    with pytest.raises(ValueError):
        to_margin(float("nan"))


# ── ★ the SPELLING of a certified margin, shared with the panel ───────────

#: The contract between `format_lever_value` and the panel's
#: ``String(Number(v.toPrecision(12)))``, pinned as data on BOTH sides of the
#: wire. `MarginLoopPanel.test.tsx` asserts the same table against
#: `MARGIN_LEVER.format`, so a change to either spelling fails a test in the
#: language that made it. Generated from `node` and re-checked against it
#: below wherever node is installed.
LEVER_SPELLINGS = [
    (0.0, "0"),
    (1e-9, "1e-9"),                   # both exponential; only the padding
    (1.23456789e-7, "1.23456789e-7"), # differed (`repr` writes ``e-07``)
    (1e-6, "0.000001"),
    (1.23456e-5, "0.0000123456"),     # JS fixed where `repr` goes exponential
    (1e-4, "0.0001"),
    (0.05, "0.05"),
    (0.6716004307251234, "0.671600430725"),   # the margin that found the bug
    (1.0, "1"),                       # JS drops the ``.0`` that `repr` keeps
    (1.357, "1.357"),
    (1.4925760000000001, "1.492576"),
    (0.1 + 0.2, "0.3"),               # 12 sig figs absorbs the binary noise
    (1 / 3, "0.333333333333"),
    (5.0, "5"),                       # MAX_MARGIN
]


@pytest.mark.parametrize("value,spelled", LEVER_SPELLINGS)
def test_a_certified_margin_is_spelled_the_way_the_panel_spells_it(
        value, spelled):
    """★ The verdict and the panel print ONE number for one margin.

    Bite: `%g` (six significant figures) — the shipped spelling, which turned
    0.671600430725 into 0.6716 in a verdict sitting two lines under a panel
    printing all twelve. Also bitten by plain `repr`, which gets the integral
    case (``1.0``), the ``1e-6 … 1e-4`` window and the exponent padding wrong.
    """
    assert format_lever_value(value) == spelled


def test_the_margin_spelling_is_javascripts_spelling():
    """★ The table above is not this module's opinion — it is `node`'s.

    A pinned table can only catch a change on ONE side; this catches the day
    the table itself is wrong. Skipped where node is absent (the table still
    guards), because the backend suite must not require a JS runtime.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the pinned table still guards")
    values = [v for v, _ in LEVER_SPELLINGS]
    rng = random.Random(20260829)
    values += [rng.uniform(0.0, MAX_MARGIN) for _ in range(300)]
    values += [rng.uniform(0.0, 1e-4) for _ in range(100)]
    values += [1.0 / rng.uniform(0.2, 1.0) - 1.0 for _ in range(100)]
    proc = subprocess.run(
        [node, "-e",
         "const v = JSON.parse(process.argv[1]);"
         "console.log(JSON.stringify("
         "v.map(x => String(Number(x.toPrecision(12))))))",
         json.dumps(values)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    expected = json.loads(proc.stdout)
    mismatches = [(v, format_lever_value(v), js)
                  for v, js in zip(values, expected)
                  if format_lever_value(v) != js]
    assert not mismatches, mismatches[:5]


# ══ fixtures ══════════════════════════════════════════════════════════════

def _network(p_nom_max: float = 200.0, firm_mw: float = 60.0) -> pypsa.Network:
    """100 MW flat load, occurrence-bearing firm plant and one extendable
    peaker with a FINITE ``p_nom_max`` — so the firm-capacity ceiling is a
    real number rather than the ordinary network's ``inf`` (plan §2.2: an
    acceptance test that exercises the ceiling needs exactly this).

    Unit snapshot weights make the resolution floor exactly ``1/draws``.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=firm_mw,
          marginal_cost=10.0,
          outage_rate_value=0.1, outage_rate_basis="EFORd", mttr_hours=4.0)
    n.add("Generator", "peak", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_min=0.0, p_nom_max=p_nom_max,
          capital_cost=400.0, marginal_cost=250.0,
          outage_rate_value=0.1, outage_rate_basis="EFORd", mttr_hours=4.0)
    return n


def _unbounded_network() -> pypsa.Network:
    """The ordinary network: PyPSA's default ``p_nom_max = inf``, where the
    ceiling degrades to the schema's ``le=5``."""
    return _network(p_nom_max=float("inf"))


def _tight_network() -> pypsa.Network:
    """A FINITE candidate set that cannot reach even a zero margin: 60 MW of
    firm plant plus 30 MW of candidate, derated, against a 100 MW peak."""
    return _network(p_nom_max=30.0)


def _barren_network() -> pypsa.Network:
    """No resolvable occurrence data anywhere — nothing to sample."""
    n = _network()
    for g in ("firm", "peak"):
        n.generators.at[g, "outage_rate_value"] = float("nan")
        n.generators.at[g, "carrier"] = "unobtainium"
    n.add("Carrier", "unobtainium")
    return n


def _unpriceable_network() -> pypsa.Network:
    """A samplable fleet PLUS one asset the margin cannot price: no outage
    data and no availability profile, so the standard would be enforced
    against a fleet the tool silently shrank."""
    n = _network()
    n.add("Carrier", "unobtainium")
    n.add("Generator", "mystery", bus="b", carrier="unobtainium", p_nom=25.0,
          marginal_cost=5.0)
    return n


def _setup(client, install_network, network=None, **cfg):
    install_network(network if network is not None else _network())
    body = {"solver_name": "highs", "voll": VOLL}
    body.update(cfg)
    r = client.put("/api/simulation/solver_config", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@contextmanager
def _fake_running(state: dict, key: str):
    """A REAL live daemon thread under ``state[key]`` — every guard tests
    ``thread.is_alive()``, so a sentinel dict would prove nothing."""
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name=f"fake-{key}")
    t.start()
    state[key] = {"status": "running", "result": None, "rows": [], "points": [],
                  "iterations": [], "error": None, "started_at": time.time(),
                  "thread": t}
    try:
        yield
    finally:
        release.set()
        t.join(timeout=5)
        state.pop(key, None)


def _poll(client, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    body: dict | None = None
    while time.time() < deadline:
        r = client.get(LOOP_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("status") not in ("running",):
            return body
        time.sleep(0.02)
    raise AssertionError(f"the margin loop never finished: {body!r}")


# ══ stub collaborators ════════════════════════════════════════════════════

PEAK_MW = 100.0


def _report(margin: float, *, firm_mw: float, cost: float, ens_mwh: float,
            binding: bool, cap_mwh: float = 0.0,
            target_binding: str = "voll") -> dict:
    """A margin-shaped adequacy report.

    ``target.system.cap_mwh`` is ``0.0`` — the value a margin-only run really
    publishes, since the ENS cap's per-period loop never executes. That zero
    is the whole reason spec §2.2 exists.
    """
    return {
        "engine": "lp_proxy",
        "fidelity": "deterministic_proxy",
        "reserve_margin": {
            "margin": margin,
            "horizon_wide": True,
            "by_period": [{
                "period": "ALL",
                "peak_mw": PEAK_MW,
                "required_mw": (1.0 + margin) * PEAK_MW,
                "firm_mw": firm_mw,
                "margin_achieved": firm_mw / PEAK_MW - 1.0,
                "met": firm_mw >= (1.0 + margin) * PEAK_MW - 1e-9,
                "binding": bool(binding),
                "n_peak_hours": 1,
                "peak_snapshots": ["2030-01-01 00:00:00"],
                "max_achievable_mw": 234.0,
                "max_achievable_unbounded": False,
            }],
            "assets": [],
            "derating_bases": {},
        },
        "target": {
            "basis": "energy",
            "binding": target_binding,
            "system": {"cap_mwh": cap_mwh, "achieved_ens_mwh": ens_mwh,
                       "achieved_shed_hours": 2.0, "by_period": []},
            "zones": [],
        },
        "metrics": {"ens_mwh": ens_mwh, "shed_hours": 2.0,
                    "time_basis": "hours_per_horizon",
                    "horizon_years": 4.0 / 8760.0},
        "cost": {"total_system_cost_eur": cost, "period_basis": "horizon"},
    }


def _metrics(lole: float, *, floor: float = 1.0 / DRAWS,
             by_period=None) -> dict:
    return {
        "lole_hours": lole,
        "lole_ci": (max(lole - 0.1, 0.0), lole + 0.1),
        "eue_mwh": lole * 10.0,
        "eue_ci": (0.0, lole * 20.0),
        "by_period": by_period if by_period is not None
        else {"ALL": {"lole_hours": lole, "eue_mwh": lole * 10.0}},
        "n_samples": DRAWS,
        "converged": True,
        "time_basis": "hours_per_horizon",
        "horizon_years": 4.0 / 8760.0,
        "resolution_floor_h": floor,
        "warning": MC_WARNING_V1,
    }


class _Stubs:
    """A deterministic LP + MC in ~20 lines.

    The incumbent plan carries ``firm_base`` MW of derated firm capacity, so
    it is TIGHT at ``m_tight = firm_base/peak − 1`` and unchanged for every
    margin at or under it. Above that the margin binds exactly, the plan grows
    to ``(1+m)·peak`` and the cost grows with it — which is the shape the
    informed step of spec §2.3 is reasoning about.
    """

    def __init__(self, *, firm_base: float = 130.0, lole_fn=None,
                 delay: float = 0.0, cap_mwh: float = 0.0,
                 fail_above: float | None = None,
                 target_binding: str = "voll"):
        self.firm_base = firm_base
        self.m_tight = firm_base / PEAK_MW - 1.0
        self.lole_fn = lole_fn or (lambda m: 9.0 if m <= 0.5 else 0.2)
        self.delay = delay
        self.cap_mwh = cap_mwh
        self.fail_above = fail_above
        self.target_binding = target_binding
        self.margins: list[float] = []
        self.cfgs: list[object] = []
        self.mc_kwargs: list[dict] = []
        self.restore_cfgs: list[object] = []
        self.by_period = None

    # the LP
    def solve_once(self, cfg, n, lock, log_queue, sink):
        if self.delay:
            time.sleep(self.delay)
        m = float(getattr(cfg, "reserve_margin", 0.0) or 0.0)
        self.margins.append(m)
        self.cfgs.append(cfg)
        if self.fail_above is not None and m > self.fail_above:
            # What an out-of-reach margin really does: `_check_reserve_margin`
            # is a BLOCKING error, so the solve never runs.
            sink["_status"] = "error"
            sink["_condition"] = "validation_failed"
            return
        binding = m > self.m_tight
        firm = (1.0 + m) * PEAK_MW if binding else self.firm_base
        sink["_status"] = "ok"
        sink["_condition"] = "optimal"
        sink["adequacy_report"] = _report(
            m, firm_mw=firm, cost=1000.0 + 40.0 * max(0.0, firm - self.firm_base),
            ens_mwh=40.0, binding=binding, cap_mwh=self.cap_mwh,
            target_binding=self.target_binding)

    # the MC — the plan the last solve produced
    def mc_adequacy(self, inputs, **kw):
        self.mc_kwargs.append(dict(kw))
        return _metrics(self.lole_fn(self.margins[-1]), by_period=self.by_period)

    def run_simulation(self, cfg, n, lock, stop_event, log_queue, **kw):
        self.restore_cfgs.append(cfg)
        return "ok", "optimal"


def _install_stubs(monkeypatch, stubs: _Stubs) -> None:
    import services.adequacy.mc as mc_mod
    import services.adequacy.sweep as sweep_mod
    import services.solver_service as solver_mod

    monkeypatch.setattr(sweep_mod, "_solve_once", stubs.solve_once)
    monkeypatch.setattr(mc_mod, "mc_adequacy", stubs.mc_adequacy)
    monkeypatch.setattr(solver_mod, "run_simulation", stubs.run_simulation)


def _start(client, **body):
    payload = {"target_lole_h": 1.0, "draws": DRAWS, "seed": SEED}
    payload.update(body)
    r = client.post(LOOP_URL, json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ══ §2 — the surface ══════════════════════════════════════════════════════

def test_get_margin_loop_is_204_before_any_run(client, install_network):
    _setup(client, install_network)
    r = client.get(LOOP_URL)
    assert r.status_code == 204, r.text
    assert r.content == b""


# ── ★ the synchronous refusals (§2.4) ─────────────────────────────────────

_422_CASES = [
    ("target_missing", _network, {}, {}, ["target_lole_h"]),
    ("target_zero", _network, {}, {"target_lole_h": 0.0}, ["target_lole_h"]),
    ("target_negative", _network, {}, {"target_lole_h": -3.0},
     ["target_lole_h"]),
    ("nothing_to_sample", _barren_network, {}, {"target_lole_h": 1.0},
     ["nothing to sample"]),
    ("draws_zero", _network, {}, {"target_lole_h": 1.0, "draws": 0},
     ["draws"]),
    ("draws_over_cap", _network, {},
     {"target_lole_h": 1.0, "draws": MAX_DRAWS + 1}, ["draws"]),
    ("max_solves_zero", _network, {},
     {"target_lole_h": 1.0, "max_solves": 0}, ["max_solves"]),
    ("max_solves_over_cap", _network, {},
     {"target_lole_h": 1.0, "max_solves": MAX_LOOP_SOLVES + 1},
     ["max_solves", str(MAX_LOOP_SOLVES)]),
    ("restore_unknown", _network, {},
     {"target_lole_h": 1.0, "restore": "sideways"}, ["restore"]),
    ("strategy_rolling", _network, {"solve_strategy": "rolling"},
     {"target_lole_h": 1.0}, ["rolling"]),
    # weights are 1.0, so the floor is exactly 1/draws = 0.05 h.
    ("below_floor", _network, {},
     {"target_lole_h": 0.001, "draws": 20}, ["20", "floor"]),
    ("unreachable_ceiling", _tight_network, {}, {"target_lole_h": 1.0},
     ["margin"]),
    ("unpriceable", _unpriceable_network, {}, {"target_lole_h": 1.0},
     ["mystery"]),
]


@pytest.mark.parametrize(
    "case_id,factory,cfg,body,fragments",
    _422_CASES, ids=[c[0] for c in _422_CASES])
def test_the_synchronous_refusal_set_is_complete(
        client, install_network, case_id, factory, cfg, body, fragments):
    """★ Every one of these is knowable from the config and one snapshot, so
    discovering it minutes into a background run is a choice.

    Bite (the two that matter most): drop the ceiling refusal and the
    unpriceable refusal. Without the first, a network whose candidate set
    cannot reach ANY margin spends its whole budget on iterates the validator
    refuses; without the second, every iterate fails validation identically
    and the run ends ``budget_exhausted`` advising "raise max_solves", which
    can never work.
    """
    _setup(client, install_network, network=factory(), **cfg)
    r = client.post(LOOP_URL, json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    for frag in fragments:
        assert frag in detail, f"{case_id}: {frag!r} missing from {detail!r}"
    assert client.get(LOOP_URL).status_code == 204


def test_a_voll_free_network_is_accepted(client, install_network, monkeypatch):
    """★ Bite: copy the cap loop's ``voll > 0`` refusal.

    The margin is a CONSTRAINT, not a price: it forces firm capacity into the
    plan whether or not unserved energy has a price attached, so a margin loop
    on a VoLL-free network is well defined. Refusing it would deny the
    configuration the standard exists for.
    """
    stubs = _Stubs()
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, voll=0.0)
    _start(client, max_solves=1)
    body = _poll(client)
    assert body["status"] in ("met", "budget_exhausted", "unreachable"), body
    assert body["error"] is None, body


def test_myopic_is_allowed_and_only_rolling_is_refused(
        client, install_network, monkeypatch):
    """★ Bite: refuse both strategies, as the cap loop does.

    The margin's own validator refuses ``rolling`` (each window's peak becomes
    the denominator — a weaker standard under the same name) and DOWNGRADES
    ``myopic`` to a warning with a stated reason: a myopic iteration's
    snapshots are exactly one investment period, which is the peak the
    standard is defined against. Copying the cap loop's blanket refusal would
    deny a supported configuration.
    """
    stubs = _Stubs()
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, solve_strategy="rolling")
    assert client.post(LOOP_URL, json={"target_lole_h": 1.0}).status_code == 422

    _setup(client, install_network, solve_strategy="myopic",
           multi_investment_periods=True)
    _start(client, max_solves=1)
    body = _poll(client)
    assert body["status"] != "running"
    assert body["error"] is None, body


def test_the_unpriceable_refusal_reuses_the_validators_sentence_verbatim(
        client, install_network):
    """★ Bite: write the route's own sentence.

    Two sentences for one fact drift apart on the first change to the
    derating chain, and the user then reads a different account of the same
    exclusion depending on which surface told them. The validator's message
    IS the message.
    """
    from services.solver_service import SolverConfig
    from services.validation_service import _check_reserve_margin

    _setup(client, install_network, network=_unpriceable_network())
    r = client.post(LOOP_URL, json={"target_lole_h": 1.0})
    assert r.status_code == 422, r.text

    # The validator's own sentence, from the validator itself. Built on an
    # identical network here rather than on the live one: `_state` and
    # `PyPSAService.get_network()` resolve to the PROCESS foreground when read
    # from the test thread, which is a different context from the one the
    # client's requests resolve to — and the message depends only on which
    # assets cannot be priced.
    issues = [i for i in _check_reserve_margin(
                  _unpriceable_network(),
                  SolverConfig(solver_name="highs", voll=VOLL,
                               reserve_margin=0.15))
              if i.code == "reserve_margin_unpriceable_assets"]
    assert issues, "the fixture no longer trips the validator's own check"
    assert r.json()["detail"] == issues[0].message


def test_the_ceiling_refusal_names_the_ceiling_and_spends_no_solves(
        client, install_network, monkeypatch):
    """★ A3 (spec §2.4, §4): the ceiling, on a fixture with a FINITE
    ``p_nom_max``.

    ``max_achievable`` is ``inf`` on the ordinary network (PyPSA's default),
    where the sanitizer nulls it — so a ceiling test on that network could not
    fail. Here the whole fleet derated tops out under the peak, so no margin
    at all is reachable and the refusal must come before a solve is spent.

    Bite: drop the refusal. The loop then runs, every iterate fails validation
    (an unreachable margin is a BLOCKING preflight error, not an infeasible
    LP), and the verdict is ``unreachable`` — the same word for "your
    candidate set is too small", with eight solves' wall-clock attached.
    """
    stubs = _Stubs()
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, network=_tight_network())

    r = client.post(LOOP_URL, json={"target_lole_h": 1.0, "draws": DRAWS})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "margin" in detail
    # The ceiling itself, by name: -19 % (81 MW of derated fleet, 100 MW peak).
    assert "%" in detail
    assert stubs.margins == [], "the refusal spent a solve"
    assert client.get(LOOP_URL).status_code == 204


# ── ★ the 409 mesh, both directions ───────────────────────────────────────

def test_409_mesh_covers_every_study_pair_and_the_solve_entrypoints(
        client, install_network, session_state):
    """★ Bite: register ``margin_loop`` nowhere but its own route.

    The margin loop re-solves the foreground network once per iterate and
    reads it once per evaluation, exactly like the coupling loop — so it needs
    the SAME mesh in both directions, including the two foreground solve
    entrypoints on the other side of the router boundary. A study that blocks
    others but is not blocked BY them is a half-mesh: whichever lost the race
    would be measuring a network the other was rebuilding.
    """
    _setup(client, install_network)
    st = session_state(client)
    body = {"target_lole_h": 1.0}

    with _fake_running(st, "margin_loop"):
        assert client.post(LOOP_URL, json=body).status_code == 409
        assert client.post("/api/results/coupling_loop",
                           json=body).status_code == 409
        assert client.post("/api/results/frontier", json={}).status_code == 409
        assert client.post("/api/results/mc", json={}).status_code == 409
        assert client.post("/api/results/fmea_sweep",
                           json={"scenarios": []}).status_code == 409
        rr = client.post("/api/simulation/run")
        assert rr.status_code == 409, rr.text
        assert "margin" in rr.json()["detail"]
        assert client.post("/api/simulation/run_ac_pf").status_code == 409

    for key in ("frontier", "mc", "fmea_sweep", "coupling_loop"):
        with _fake_running(st, key):
            r = client.post(LOOP_URL, json=body)
            assert r.status_code == 409, f"{key}: {r.text}"

    st["status"] = "running"
    try:
        assert client.post(LOOP_URL, json=body).status_code == 409
    finally:
        st["status"] = "idle"


def test_the_study_key_is_registered_in_the_shared_mesh_module(client):
    """The mesh predicate lives in ONE module so it cannot differ between
    callers; a key registered only in `results.py` is invisible to
    `blocking_study_detail`, which is what the solve entrypoints consult."""
    from services import study_state

    assert "margin_loop" in study_state.STUDY_KEYS
    assert "margin" in study_state.STUDY_LABELS["margin_loop"]


# ── ★ §2.2 — cap_mwh=None is mandatory ────────────────────────────────────

def test_the_cap_is_never_passed_through_to_the_controller(
        client, install_network, monkeypatch):
    """★ Bite (spec §2.2): pass ``cap_mwh=float(sysblk["cap_mwh"])`` instead
    of ``None``.

    On a margin-only report that number is ``0.0`` — the ENS cap's per-period
    loop never runs, so ``SystemTarget.cap_mwh`` is emitted as its initialised
    zero. `coupling.py` ends the search with ``unreachable`` when
    ``row["cap_mwh"] is not None and row["cap_mwh"] < ENERGY_FLOOR_MWH``, so
    the bite fires that test on the FIRST miss and EVERY margin run ends
    ``unreachable`` after one solve — indistinguishable in the payload from
    the real thing. ``None`` makes the test a genuine no-op, which is the only
    correct reading for a lever with no energy cap.
    """
    stubs = _Stubs(lole_fn=lambda m: 9.0)          # never meets
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    _start(client, max_solves=3)
    body = _poll(client)

    # Under the bite the FIRST miss ends the run: `0.0 < ENERGY_FLOOR_MWH`.
    assert body["solves_used"] == 3, body
    assert len(body["iterations"]) == 3, body
    assert all(row["cap_mwh"] is None for row in body["iterations"]), body
    assert not (body["status"] == "unreachable"
                and body["solves_used"] == 1), body


# ── ★ §2.1 — binding comes from the margin's own block ────────────────────

def test_binding_is_read_from_the_margins_own_block(
        client, install_network, monkeypatch):
    """★ Bite: forward ``rep["target"]["binding"]``.

    That field is computed purely from the ENS caps and reads ``"voll"`` on
    every margin run — the stub says so explicitly. The controller's
    ``reusable`` pre-test is ``binding != "system_cap"``, so under the bite it
    is ALWAYS true and the plan-hash plateau reuse is offered on iterates
    where the margin demonstrably rebuilt the plan. Reading the margin's own
    per-period ``binding`` gives that pre-test real information.
    """
    stubs = _Stubs(lole_fn=lambda m: 0.2 if m > 0.5 else 9.0,
                   target_binding="voll")
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    _start(client, max_solves=2)
    body = _poll(client)

    rows = body["iterations"]
    assert rows, body
    for row in rows:
        m = row["lever_value"]
        expected = "system_cap" if m > stubs.m_tight else "voll"
        assert row["binding"] == expected, (m, row["binding"])
    assert any(r["binding"] == "system_cap" for r in rows), (
        "no iterate ever bound on the margin — the fixture proves nothing")


# ── ★ §2.3 — the informed step ────────────────────────────────────────────

def test_the_informed_step_reaches_a_binding_margin_in_one_solve(
        client, install_network, monkeypatch):
    """★ A2 (spec §2.3): a non-binding start reaches a binding margin in ONE
    solve.

    ``firm_mw / peak_mw − 1`` is the smallest margin at which the incumbent
    plan is TIGHT — at exactly that value the plan is feasible, unchanged,
    same hash, same LOLE, and flagged ``binding`` while nothing moved. So the
    step must STRICTLY exceed it.

    Bite: drop ``STEP_OVERSHOOT`` (start at ``m_tight`` exactly). The first
    iterate is then the incumbent plan under a new name — same cost, not
    binding — and the loop has spent a full solve learning nothing.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    _start(client, max_solves=1)
    body = _poll(client)

    # The probing solve, then exactly one loop iterate.
    assert body["probe_solves"] == 1, body
    assert body["solves_used"] == 1, body
    assert len(stubs.margins) == 2, stubs.margins

    m_first = body["iterations"][0]["lever_value"]
    assert m_first == pytest.approx(stubs.m_tight * (1.0 + STEP_OVERSHOOT))
    assert m_first > stubs.m_tight, "the step did not exceed the tight margin"
    assert body["margin0"] == pytest.approx(m_first)

    row = body["iterations"][0]
    assert row["binding"] == "system_cap", (
        "the first iterate did not bind — the step did not clear the "
        "incumbent plan's own tight margin")
    assert row["cost_eur"] > 1000.0, "the plan did not change"


def test_the_starting_margin_is_the_minimum_over_periods(
        client, install_network, monkeypatch):
    """★ Bite: aggregate ``m_tight`` with ``max``.

    The constraint is installed PER PERIOD, so the first period to bind is
    the binding one; ``max`` would step past the margin one period already
    makes binding and overshoot the bracket entirely. Two periods, tight at
    0.10 and 0.40: the start must come off the 0.10.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0)

    def _two_periods(cfg, n, lock, log_queue, sink):
        stubs.solve_once(cfg, n, lock, log_queue, sink)
        rep = sink.get("adequacy_report")
        if not rep:
            return
        rows = rep["reserve_margin"]["by_period"]
        base = dict(rows[0])
        rows[:] = [
            {**base, "period": "2030", "firm_mw": 1.10 * PEAK_MW},
            {**base, "period": "2040", "firm_mw": 1.40 * PEAK_MW},
        ]

    monkeypatch.setattr("services.adequacy.sweep._solve_once", _two_periods)
    import services.adequacy.mc as mc_mod
    import services.solver_service as solver_mod
    monkeypatch.setattr(mc_mod, "mc_adequacy", stubs.mc_adequacy)
    monkeypatch.setattr(solver_mod, "run_simulation", stubs.run_simulation)
    _setup(client, install_network, reserve_margin=0.05)

    _start(client, max_solves=1)
    body = _poll(client)
    assert body["margin0"] == pytest.approx(0.10 * (1.0 + STEP_OVERSHOOT))


# ── ★ §2.5 — validation_failed is final for this lever ────────────────────

def test_validation_failed_ends_the_search_when_the_margin_is_the_cause(
        client, install_network, monkeypatch):
    """★ Bite (spec §2.5): leave ``validation_failed`` alone.

    An out-of-reach margin surfaces as ``validation_failed``, which
    ``_is_infeasible`` matches on neither the status nor the condition — so
    the controller treats it as a TRANSIENT failure, keeps stepping, and ends
    ``budget_exhausted`` advising "raise max_solves", which can never work.
    Mapping it to ``infeasible`` — only when ``reserve_margin_facts`` confirms
    the margin is the cause — lets the nesting logic stop the search on the
    first proof.

    The fixture's real ceiling is ~134 % (60 MW firm + 200 MW candidate, both
    derated by 0.9, against a 100 MW peak), so the blind step's second iterate
    is genuinely out of reach and the validator would genuinely refuse it.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0, fail_above=2.0)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    _start(client, max_solves=6)
    body = _poll(client)

    assert body["status"] == "unreachable", body
    # THREE, not two, and the third is the point. Before the ceiling clamp
    # the loop stepped straight past `m_ceiling`, had that solve relabelled
    # `infeasible`, and concluded `unreachable` WITHOUT ever evaluating the
    # strictest reachable margin — a verdict about the search, dressed as a
    # verdict about the network. It now clamps to the ceiling, evaluates
    # there, and only then refuses anything stricter. Found live in S19.3,
    # where the ceiling was 271%, the last evaluated margin 18%, and a plan
    # meeting the target sat between them.
    assert body["solves_used"] == 3, body
    assert body["iterations"][-1]["lever_value"] > 2.0
    assert "infeasible" in str(body["iterations"][-1]["condition"]).lower()
    assert body["verdict"], body


def test_validation_failed_is_left_alone_when_the_margin_is_not_the_cause(
        client, install_network, monkeypatch):
    """★ The other half of §2.5's "only when".

    A validation failure that has nothing to do with the margin (a malformed
    asset, a bad transformer type) is NOT monotone in the margin and proves
    nothing about tighter ones. Mapping every ``validation_failed`` to
    ``infeasible`` would report ``unreachable`` — "no plan meets this
    standard" — for a run that never tested the standard at all.

    Bite: map unconditionally. The status flips to ``unreachable``.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0, fail_above=0.0)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    # One solve only: the single iterate sits at ~0.105, far under the
    # fixture's ~134 % ceiling, so the facts CANNOT confirm the margin.
    _start(client, max_solves=1)
    body = _poll(client)

    assert body["status"] == "budget_exhausted", body
    assert body["solves_used"] == 1, body
    assert "infeasible" not in str(body["iterations"][0]["condition"]).lower()


def test_the_search_stops_at_the_schemas_own_margin_bound(
        client, install_network, monkeypatch):
    """The ordinary network has ``p_nom_max = inf``, so the fleet ceiling is
    ``+inf`` and the schema's ``le=5`` is the only bound left. The blind step
    walks ``m`` up by a factor of ~4 per iterate, so without this the loop
    would solve — and, on ``restore="final"``, PERSIST — a margin the config
    schema would refuse on the next PUT.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, network=_unbounded_network(),
           reserve_margin=0.10)

    _start(client, max_solves=6)
    body = _poll(client)

    assert body["status"] == "unreachable", body
    assert stubs.margins, "nothing was solved at all"
    assert all(float(m) <= MAX_MARGIN + 1e-9 for m in stubs.margins), (
        "a solve was attempted above the schema's own bound: "
        f"{stubs.margins}")
    # The refused iterate is still REPORTED — the controller tried it, and a
    # row the user cannot see is a search step they cannot account for — but
    # it is reported as infeasible, naming the bound, having cost no solve.
    last = body["iterations"][-1]
    assert last["lever_value"] > MAX_MARGIN, body["iterations"]
    assert "infeasible" in str(last["condition"]).lower(), last
    assert f"{MAX_MARGIN:.0%}" in str(last["condition"]), last
    assert last["mc"] is None and last["cost_eur"] is None


# ── ★ §2.6 — the payload ──────────────────────────────────────────────────

def test_no_payload_field_anywhere_carries_an_x_value(
        client, install_network, monkeypatch):
    """★ Bite (spec §2.6): store the controller's rows unchanged.

    The controller's ``eps_permyriad`` is the substitution's ``x``, an
    internal coordinate with no meaning to a user — 0.76 is not a margin, not
    a percentage and not a per-myriad ENS cap, and a panel that renders it
    beside a ‱ column header would be showing a number that means nothing at
    all. Every row is translated to ``lever_value`` (a margin) before it is
    stored, and ``lever_star`` is the certified MARGIN.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 0.2)   # meets at once
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    _start(client, max_solves=2)
    body = _poll(client)

    assert body["status"] == "met", body
    assert body["study"] == "margin_loop"
    assert body["lever"] == "reserve_margin"
    assert body["lever_unit"] == "%"
    assert body["lever_label"]
    assert body["lever_star"] == pytest.approx(
        stubs.m_tight * (1.0 + STEP_OVERSHOOT))
    assert body["final"]["lever_value"] == pytest.approx(body["lever_star"])
    assert set(body["iterations"][0]) == {
        "lever_value", "solve_status", "condition", "cost_eur", "ens_mwh",
        "cap_mwh", "binding", "plateau", "mc"}

    forbidden_keys = {"eps_permyriad", "eps_star", "eps0", "x", "x0",
                      "x_star", "ens_cap_permyriad"}
    xs = [to_x(m) for m in stubs.margins]

    def _walk(node, path="body"):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, f"{path}.{k} is an x-space key"
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            for x in xs:
                assert abs(float(node) - x) > 1e-9, (
                    f"{path} = {node} is the controller's x for margin "
                    f"{to_margin(x):g}")

    _walk(body)
    assert MC_WARNING_V1 in body["warning"]
    assert body["resolution_floor_h"] == pytest.approx(1.0 / DRAWS)
    assert body["basis"] == "hours_per_horizon"
    assert stubs.mc_kwargs == [{"draws": DRAWS, "seed": SEED,
                                "max_draws": DRAWS}]


# ── ★ restore ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("restore", ["final", "base"])
def test_restore_writes_the_margin_and_never_touches_a_user_set_cap(
        client, install_network, monkeypatch, restore):
    """★ Bite: write ``ens_cap_permyriad`` on the ``final`` restore (the cap
    loop's line, copied).

    The margin loop tunes ONE lever. A user who also set an energy cap has
    asked for both standards, and the certified plan met both — writing the
    margin into the cap's field would silently replace one standard with a
    number from the other, and the plan the user is left holding would be
    built against neither.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 0.2)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10,
           ens_cap_permyriad=25.0)

    _start(client, restore=restore, max_solves=2)
    body = _poll(client)
    assert body["status"] == "met", body
    m_star = body["lever_star"]
    assert m_star == pytest.approx(stubs.m_tight * (1.0 + STEP_OVERSHOOT))
    assert body["base_restored"] is True

    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["ens_cap_permyriad"] == pytest.approx(25.0), (
        "the user's own energy cap was rewritten by a margin study")
    if restore == "final":
        assert cfg["reserve_margin"] == pytest.approx(m_star)
    else:
        assert cfg["reserve_margin"] == pytest.approx(0.10)

    assert len(stubs.restore_cfgs) == 1
    closing = stubs.restore_cfgs[0]
    assert getattr(closing, "ens_cap_permyriad") == pytest.approx(25.0)
    assert getattr(closing, "reserve_margin") == pytest.approx(
        m_star if restore == "final" else 0.10)
    # …and every iterate carried the user's cap too.
    assert all(getattr(c, "ens_cap_permyriad") == pytest.approx(25.0)
               for c in stubs.cfgs)


def test_a_met_verdict_names_both_standards_when_a_cap_was_also_set(
        client, install_network, monkeypatch):
    """★ Bite: reuse the cap loop's met copy.

    A margin run leaves a user-set ENS cap untouched throughout, so the
    certified plan met BOTH standards — and a verdict that mentions only the
    margin invites the reader to think the cap was dropped for the study.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 0.2)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10,
           ens_cap_permyriad=25.0)

    _start(client, restore="final", max_solves=2)
    body = _poll(client)
    assert body["status"] == "met", body
    verdict = body["verdict"]
    assert "reserve_margin" in verdict, verdict
    assert "both" in verdict.lower(), verdict
    assert "ens_cap_permyriad" in verdict or "energy cap" in verdict, verdict


def test_a_met_verdict_without_a_cap_does_not_claim_two_standards(
        client, install_network, monkeypatch):
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 0.2)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    _start(client, restore="base", max_solves=2)
    body = _poll(client)
    assert body["status"] == "met", body
    assert "both" not in body["verdict"].lower(), body["verdict"]
    # `restore="base"` must tell the user which FIELD to set to keep the plan.
    assert "reserve_margin" in body["verdict"], body["verdict"]


# ── ★ abort ───────────────────────────────────────────────────────────────

def test_abort_stops_the_loop_and_the_restore_still_runs(
        client, install_network, monkeypatch):
    """★ Bite: make the abort route a no-op.

    An abort that does not reach the worker is worse than no button: the user
    is told the study stopped while more solves keep mutating the network
    underneath them.
    """
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0, delay=0.2)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)

    assert client.post(ABORT_URL).status_code == 404

    _start(client, max_solves=8)
    a = client.post(ABORT_URL)
    assert a.status_code == 200, a.text
    assert client.post(ABORT_URL).status_code == 200      # idempotent

    body = _poll(client)
    assert body["status"] == "aborted", body
    assert body["base_restored"] is True
    assert len(stubs.restore_cfgs) == 1, "the closing restore did not run"
    assert body["solves_used"] < 8, "the abort did not shorten the run"


def test_the_record_never_puts_its_thread_or_stop_event_on_the_wire(
        client, install_network, monkeypatch):
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 0.2)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, reserve_margin=0.10)
    _start(client, max_solves=1)
    body = _poll(client)
    assert "thread" not in body and "stop_event" not in body


# ══ §4 — acceptance ═══════════════════════════════════════════════════════

@pytest.mark.slow
def test_the_margin_loop_meets_a_target_the_cap_loop_calls_unreachable(
        client, install_network):
    """★ A1 (spec §4): the phase's whole claim, live.

    S17's shape, minimally: firm capacity that COVERS demand deterministically
    (130 MW against a 100 MW peak), so the LP sheds nothing at any energy cap
    — the cap never binds, no ε changes the plan, and the coupling loop can
    only report ``unreachable``. The MC's loss of load comes entirely from
    OUTAGES the LP does not model, and the lever that buys firm capacity the
    LP sees no deterministic reason to build is the reserve MARGIN.

    The target is DERIVED, never chosen: the sequential MC measures the
    incumbent plan first, and the standard is a quarter of what that plan
    achieves — a standard the incumbent misses by construction, and one no
    tuning of this test can flatter.
    """
    n = _network(p_nom_max=500.0, firm_mw=130.0)
    n.generators.at["peak", "capital_cost"] = 50.0
    _setup(client, install_network, network=n)

    # A real solve, so both loops start from the same incumbent plan.
    r = client.post("/api/simulation/run")
    assert r.status_code == 200, r.text
    deadline = time.time() + 300
    while time.time() < deadline:
        st = client.get("/api/simulation/status").json()
        if st.get("status") not in ("running", "starting"):
            break
        time.sleep(0.1)
    assert st.get("condition") in ("ok", "optimal"), st

    # ── the target, derived from the incumbent plan's own MC ──
    r = client.post("/api/results/mc", json={"draws": 32, "seed": SEED})
    assert r.status_code == 200, r.text
    deadline = time.time() + 300
    while time.time() < deadline:
        mc = client.get("/api/results/mc").json()
        if mc.get("status") != "running":
            break
        time.sleep(0.1)
    assert mc["status"] == "done", mc
    base_lole = float(mc["result"]["metrics"]["lole_hours"])
    assert base_lole > 0, "the incumbent plan already loses no load — no study"
    target = base_lole / 4.0
    assert target > 1.0 / 32, "the derived target is under the resolution floor"

    # ── the cap loop: unreachable ──
    r = client.post("/api/results/coupling_loop",
                    json={"target_lole_h": target, "draws": 32, "seed": SEED,
                          "max_solves": 3, "eps0": 100.0})
    assert r.status_code == 200, r.text
    deadline = time.time() + 900
    while time.time() < deadline:
        cap = client.get("/api/results/coupling_loop").json()
        if cap.get("status") != "running":
            break
        time.sleep(0.1)
    assert cap["status"] == "unreachable", cap
    assert all(row["binding"] != "system_cap" for row in cap["iterations"]), (
        "the cap DID bind on this fixture — it is not S17-shaped")

    # ── the margin loop: met ──
    r = client.post(LOOP_URL, json={"target_lole_h": target, "draws": 32,
                                    "seed": SEED, "max_solves": 3})
    assert r.status_code == 200, r.text
    body = _poll(client, timeout=900.0)

    assert body["status"] == "met", body
    assert body["lever"] == "reserve_margin"
    assert body["lever_star"] is not None and body["lever_star"] > 0
    assert body["final"]["mc"]["lole_hours"] <= target
    assert body["error"] is None, body["error"]
    assert body["base_restored"] is True
    assert any(row["binding"] == "system_cap" for row in body["iterations"]), (
        "no iterate ever bound on the margin")


# ── ★ the number the user is told to type ─────────────────────────────────

@pytest.mark.parametrize("restore", ["base", "final"])
def test_the_verdict_names_the_margin_the_panel_tells_you_to_type(
        client, install_network, monkeypatch, restore):
    """★ One certified margin, one number — not two.

    Found by RENDERING the panel, which no unit test had done: the verdict
    said "set reserve_margin = 0.6716" while the restore explainer two lines
    above it said "reserve_margin = 0.671600430725". Same panel, same field,
    two instructions. A margin is a THRESHOLD on required firm capacity, so
    the shorter value is a strictly LOOSER standard — the user would be told
    to type a number that buys a cheaper, possibly non-compliant build than
    the one the study certified.

    Both restore modes, because both print the value and the "final" branch
    had the same `%g`.

    Bite (verified): restore either branch to `{m_star:g}`.
    """
    stubs = _Stubs(firm_base=123.456)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)
    _start(client, max_solves=6, restore=restore)
    body = _poll(client)
    assert body["status"] == "met", body
    m_star = float(body["lever_star"])
    assert m_star != float(f"{m_star:g}"), (
        "the fixture certified a margin that six significant figures already "
        f"round-trip ({m_star!r}) — this test cannot see the defect it exists "
        "for; choose a margin with more digits")
    panel = format_lever_value(m_star)
    assert f"reserve_margin = {panel}" in body["verdict"], (
        "the verdict names a different number from the one the panel tells "
        f"the user to type: verdict={body['verdict']!r} panel={panel!r}")
