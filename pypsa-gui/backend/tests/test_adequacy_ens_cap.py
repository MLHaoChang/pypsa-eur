"""
System ENS cap (Phase 1 Task 1) + per-zone ceilings (Task 2).

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md
§5.1; plan: docs/superpowers/plans/2026-08-28-fmea-phase1-core-loop.md.

The reliability target: `ens_cap_permyriad` = parts per ten thousand of the
period's weighted ELECTRICAL demand. Enforced per investment period as a
linear constraint on the involuntary slack dispatch (the
_wrap_with_capex_budget pattern — a composed extra_functionality calling
n.model.add_constraints). Every test here is a LIVE HiGHS solve: the
standard is the constraint demonstrably binding, not the wrapper composing.

The economics that make the cap bind (fixture below): shedding at
voll=150 €/MWh is CHEAPER than the expensive backup at 200 €/MWh, so the
un-capped LP sheds the whole 40 MW gap; only the cap forces the expensive
unit on.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from services import validation_service as VS
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

WEIGHT = 3.0
N_SNAPSHOTS = 4
LOAD_MW = 100.0
CHEAP_MW = 60.0
VOLL = 150.0            # deliberately BELOW the backup's 200 €/MWh
BACKUP_MC = 200.0

# Weighted electrical demand D = 100 × 4 × 3 = 1200 MWh.
DEMAND_MWH = LOAD_MW * N_SNAPSHOTS * WEIGHT
# Un-capped optimum sheds the full gap: 40 × 4 × 3 = 480 MWh.
UNCAPPED_SHED_MWH = (LOAD_MW - CHEAP_MW) * N_SNAPSHOTS * WEIGHT
# Cap at 1000 ‱ = 10 % of demand = 120 MWh — well below the un-capped 480.
CAP_PERMYRIAD = 1000.0
CAP_MWH = CAP_PERMYRIAD / 1e4 * DEMAND_MWH


def _network(h2_side: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=CHEAP_MW,
          marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=LOAD_MW - CHEAP_MW, marginal_cost=BACKUP_MC)
    if h2_side:
        # An H2 bus with unserved demand and NO generator: its slack sheds
        # 50 MW every snapshot no matter what. It must neither consume the
        # electrical cap nor inflate the electrical demand denominator.
        n.add("Bus", "b_h2", carrier="H2")
        n.add("Load", "l_h2", bus="b_h2", p_set=50.0)
    return n


def _solve(n: pypsa.Network, **cfg_kw) -> tuple[dict, pypsa.Network]:
    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(voll=VOLL, **cfg_kw)
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
    )
    assert status in ("ok", "optimal"), (status, condition)
    return sink, n


def _weighted_shed(cap: dict, columns=None) -> float:
    ll = cap["lost_load_t"]
    if columns is not None:
        ll = ll[[c for c in columns if c in ll.columns]]
    return float(ll.clip(lower=0).to_numpy().sum()) * WEIGHT


def test_cap_binds():
    sink, _ = _solve(_network(), ens_cap_permyriad=CAP_PERMYRIAD)
    cap = sink["last_lost_load"]
    assert cap["lost_load_total_mwh"] == pytest.approx(CAP_MWH, rel=1e-3), (
        f"achieved {cap['lost_load_total_mwh']} MWh — un-capped would be "
        f"{UNCAPPED_SHED_MWH}, the cap must force it to {CAP_MWH}"
    )


def test_cap_loose_means_voll_is_the_standard():
    sink, _ = _solve(_network(), ens_cap_permyriad=9000.0)   # 90 % — never binds
    cap = sink["last_lost_load"]
    assert cap["lost_load_total_mwh"] == pytest.approx(UNCAPPED_SHED_MWH, rel=1e-3)


def test_cap_off_by_default():
    sink, _ = _solve(_network())
    cap = sink["last_lost_load"]
    assert cap["lost_load_total_mwh"] == pytest.approx(UNCAPPED_SHED_MWH, rel=1e-3)


def test_cap_scopes_to_electrical_buses_only():
    """The H2 slack sheds 600 MWh freely; the electrical cap (10 % of the
    ELECTRICAL 1200 MWh, not of 1800) still lands electrical shed at 120."""
    sink, _ = _solve(_network(h2_side=True), ens_cap_permyriad=CAP_PERMYRIAD)
    cap = sink["last_lost_load"]
    assert _weighted_shed(cap, columns=["b"]) == pytest.approx(CAP_MWH, rel=1e-3)
    assert _weighted_shed(cap, columns=["b_h2"]) == pytest.approx(
        50.0 * N_SNAPSHOTS * WEIGHT, rel=1e-3)


def test_cap_is_enforced_per_period():
    """Two periods; shedding is attractive only in period 1 (backup costs
    200 there, 120 < voll in period 2). A HORIZON cap of 2×Ē would let
    period 1 shed 240; the per-period cap must hold it to 120."""
    n = pypsa.Network()
    base = pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h")
    mi = pd.MultiIndex.from_product([[2030, 2035], base],
                                    names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = [2030, 2035]
    n.investment_period_weightings.loc[2030, "years"] = 1.0
    n.investment_period_weightings.loc[2035, "years"] = 1.0
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=CHEAP_MW,
          marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=LOAD_MW - CHEAP_MW, marginal_cost=10.0)
    # Time-varying backup cost: 200 in period 2030 (shed preferred), 120 in
    # 2035 (serving preferred).
    mc = pd.Series(120.0, index=mi)
    mc.loc[2030] = BACKUP_MC
    n.generators_t.marginal_cost = pd.DataFrame({"backup": mc})

    sink, _ = _solve(n, ens_cap_permyriad=CAP_PERMYRIAD,
                     multi_investment_periods=True)
    ll = sink["last_lost_load"]["lost_load_t"]
    per_period = ll.clip(lower=0).groupby(ll.index.get_level_values(0)).sum().sum(axis=1) * WEIGHT
    assert float(per_period.loc[2030]) == pytest.approx(CAP_MWH, rel=1e-3), (
        f"period 2030 shed {float(per_period.loc[2030])} — a horizon cap "
        f"would allow {2 * CAP_MWH}, the per-period cap must hold {CAP_MWH}"
    )
    assert float(per_period.get(2035, 0.0)) == pytest.approx(0.0, abs=1e-6)


# ── preflight coherence ───────────────────────────────────────────────────

def _issues(n, **cfg_kw):
    return VS.validate_for_run(n, SolverConfig(**cfg_kw))


def test_preflight_cap_without_voll_warns():
    issues = _issues(_network(), ens_cap_permyriad=CAP_PERMYRIAD, voll=0.0)
    assert any(i.code == "ens_cap_without_voll" and i.severity == "warning"
               for i in issues), [i.code for i in issues]


def test_preflight_generous_cap_warns_the_99_percent_trap():
    issues = _issues(_network(), ens_cap_permyriad=150.0, voll=VOLL)
    assert any(i.code == "ens_cap_generous" for i in issues), \
        [i.code for i in issues]
    # A realistic target must NOT warn.
    issues = _issues(_network(), ens_cap_permyriad=1.0, voll=VOLL)
    assert not any(i.code == "ens_cap_generous" for i in issues)


def test_preflight_blocks_rolling_and_myopic_with_cap():
    for strategy in ("rolling", "myopic"):
        issues = _issues(_network(), ens_cap_permyriad=CAP_PERMYRIAD,
                         voll=VOLL, solve_strategy=strategy)
        assert any(i.code == "ens_cap_unsupported_strategy"
                   and i.severity == "error" for i in issues), (strategy,
                   [i.code for i in issues])


# ── per-zone ceilings (Task 2) ────────────────────────────────────────────

ZONE_CAP_PERMYRIAD = 5000.0    # 50 % system target — loose
ZONE_MULTIPLE = 0.25           # ⇒ per-zone ceiling = 12.5 % of the zone's demand


def _two_zone_network(countries: tuple[str, str] = ("AA", "BB")) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    for tag, country in zip(("a", "b"), countries):
        n.add("Bus", f"bus_{tag}", carrier="AC", country=country)
        n.add("Load", f"load_{tag}", bus=f"bus_{tag}", p_set=LOAD_MW)
        n.add("Generator", f"cheap_{tag}", bus=f"bus_{tag}", carrier="gas",
              p_nom=CHEAP_MW, marginal_cost=10.0)
        n.add("Generator", f"backup_{tag}", bus=f"bus_{tag}", carrier="gas",
              p_nom=LOAD_MW - CHEAP_MW, marginal_cost=BACKUP_MC)
    return n


def _solve_logged(n: pypsa.Network, **cfg_kw) -> tuple[dict, list[str]]:
    PyPSAService.set_network(n)
    sink: dict = {}
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    cfg = SolverConfig(voll=VOLL, **cfg_kw)
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q,
        state_update=lambda **kw: sink.update(kw),
    )
    assert status in ("ok", "optimal"), (status, condition)
    lines: list[str] = []
    while True:
        try:
            lines.append(str(log_q.get_nowait()))
        except queue.Empty:
            break
    return sink, lines


def test_zone_ceiling_binds_below_the_loose_system_cap():
    """System cap 50 % (600 MWh of the 1200 zone-pair demand — never binds:
    un-capped shed is 480+480=960? No: 50 % of the FULL 2400 = 1200 > 960).
    Zone ceilings at 0.25 × 50 % = 12.5 % of each zone's own 1200 MWh
    = 150 MWh must hold each zone far below its un-capped 480."""
    per_zone_cap = ZONE_MULTIPLE * ZONE_CAP_PERMYRIAD / 1e4 * DEMAND_MWH
    sink, _ = _solve_logged(
        _two_zone_network(),
        ens_cap_permyriad=ZONE_CAP_PERMYRIAD,
        ens_zone_cap_multiple=ZONE_MULTIPLE,
    )
    ll = sink["last_lost_load"]["lost_load_t"]
    for bus in ("bus_a", "bus_b"):
        shed = float(ll[bus].clip(lower=0).sum()) * WEIGHT
        assert shed == pytest.approx(per_zone_cap, rel=1e-3), (
            f"{bus} shed {shed} MWh — un-capped would be "
            f"{UNCAPPED_SHED_MWH}, the zone ceiling must hold {per_zone_cap}"
        )


def test_without_zone_multiple_only_the_system_cap_exists():
    sink, _ = _solve_logged(
        _two_zone_network(), ens_cap_permyriad=ZONE_CAP_PERMYRIAD,
    )
    ll = sink["last_lost_load"]["lost_load_t"]
    total = float(ll.clip(lower=0).to_numpy().sum()) * WEIGHT
    # 50 % of 2400 = 1200 > un-capped 960 → VoLL economics rule.
    assert total == pytest.approx(2 * UNCAPPED_SHED_MWH, rel=1e-3)


def test_empty_country_collapse_is_loud():
    """Blank `country` everywhere ⇒ one unnamed zone ⇒ the ceiling is a
    second system cap. Must be solvable AND say so in the solver log."""
    sink, lines = _solve_logged(
        _two_zone_network(countries=("", "")),
        ens_cap_permyriad=ZONE_CAP_PERMYRIAD,
        ens_zone_cap_multiple=ZONE_MULTIPLE,
    )
    joined = "\n".join(lines)
    assert "blank `country`" in joined, "collapse warning missing from log"
    # The blank zone's ceiling (12.5 % of ALL demand = 300) still binds.
    ll = sink["last_lost_load"]["lost_load_t"]
    total = float(ll.clip(lower=0).to_numpy().sum()) * WEIGHT
    assert total == pytest.approx(
        ZONE_MULTIPLE * ZONE_CAP_PERMYRIAD / 1e4 * 2 * DEMAND_MWH, rel=1e-3)


def test_preflight_zone_multiple_without_cap_warns():
    issues = _issues(_two_zone_network(), voll=VOLL,
                     ens_zone_cap_multiple=ZONE_MULTIPLE)
    assert any(i.code == "ens_zone_multiple_without_cap" for i in issues), \
        [i.code for i in issues]


# ---------------------------------------------------------------------------
# API-boundary bounds on the reliability-target and DSR inputs.
#
# Found by driving a LIVE backend, not by these unit tests: every test above
# constructs `SolverConfig` directly, which is a plain dataclass and validates
# nothing. The API boundary is `SolverConfigSchema`, and until this commit it
# accepted values that downstream code then silently discarded.
#
# The sharp one is a negative `ens_cap_permyriad`. Both `_wrap_with_ens_cap`
# and `_check_ens_cap_coherence` short-circuit on `<= 0` — correct for 0/None,
# which mean "no target", but it made -1 mean "no target" too: the solve ran
# with no reliability constraint, /results/adequacy returned 204, and preflight
# emitted nothing. The user believed they had set a target and had not. That is
# precisely the silent coercion the design forbids, so it is rejected where the
# value is entered.
# ---------------------------------------------------------------------------

from pydantic import ValidationError  # noqa: E402

from models.schemas import SolverConfigSchema  # noqa: E402


@pytest.mark.parametrize(
    "field, value",
    [
        # A negative target is not "off" — it is a typo, and it used to be
        # indistinguishable from having set nothing at all.
        ("ens_cap_permyriad", -1.0),
        ("ens_cap_permyriad", -0.0001),
        # A negative multiple silently dropped the zone ceilings while the
        # system cap kept applying, so the plan looked constrained and the
        # per-zone standard the user asked for was never enforced.
        ("ens_zone_cap_multiple", -3.0),
        # Zero is a zero-ENS ceiling per zone, not "no ceilings"; None is how
        # you say "no ceilings", so zero is far likelier to be a mistake.
        ("ens_zone_cap_multiple", 0.0),
        # A negative price pays the model to curtail: the voluntary tier would
        # dispatch its full volume every hour and bank the revenue.
        ("dsr_price_eur_per_mwh", -100.0),
        # A share of load above 1 curtails more demand than exists.
        ("dsr_share_of_load", 5.0),
        ("dsr_share_of_load", 1.0001),
        ("dsr_share_of_load", -0.5),
    ],
)
def test_solver_config_schema_rejects_nonsense_reliability_inputs(field, value):
    with pytest.raises(ValidationError):
        SolverConfigSchema(**{field: value})


@pytest.mark.parametrize(
    "field, value",
    [
        # 0 and None are the DOCUMENTED "off" for the target — bounding the
        # field must not break turning it off.
        ("ens_cap_permyriad", 0.0),
        ("ens_cap_permyriad", None),
        ("ens_cap_permyriad", 20.0),
        ("ens_zone_cap_multiple", None),
        ("ens_zone_cap_multiple", 1.5),
        ("dsr_price_eur_per_mwh", 0.0),
        ("dsr_price_eur_per_mwh", 200.0),
        ("dsr_share_of_load", 0.0),
        # The boundary itself: curtailing 100% of load is extreme but not
        # incoherent, so it must remain expressible.
        ("dsr_share_of_load", 1.0),
    ],
)
def test_solver_config_schema_accepts_the_meaningful_range(field, value):
    cfg = SolverConfigSchema(**{field: value})
    assert getattr(cfg, field) == value


# ---------------------------------------------------------------------------
# An infeasible solve must produce NO adequacy report.
#
# Found by an end-to-end QA run on a small three-zone system, not by the tests
# above: every one of them asserts `status in ("ok", "optimal")` inside
# `_solve`, so the failed-solve path was never reachable from this file.
#
# What the bug looked like from the outside: set an ambitious reliability
# target, get an INFEASIBLE LP, and `/results/adequacy` still returned 200
# with a complete report — achieved ENS 0.0, shed hours 0.0, every zone at
# 0.0, target met. Not because nothing was shed, but because there is no
# dispatch to measure. The cost field carried over from the previous feasible
# solve, so the report was even internally consistent. A user reads
# "reliability target met" off a plan that does not exist — the exact
# inversion of the truth, and the worst direction for a reliability metric to
# be wrong in.
#
# `/results/lost_load` already returned 204 after a failed solve. The two
# surfaces disagreed and lost_load was the one that was right, so the report
# now follows the same convention.
# ---------------------------------------------------------------------------


def _solve_allowing_failure(n: pypsa.Network, **cfg_kw) -> tuple[dict, str, str]:
    """`_solve` asserts success; this one reports it instead."""
    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(**{"voll": VOLL, **cfg_kw})
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
    )
    return sink, status, condition


def _infeasible_network() -> pypsa.Network:
    """
    Real capacity strictly below demand, nothing extendable, and a VOLL that
    is set — so slack generators DO exist and the target IS enforced. The
    only way to serve the gap is the involuntary slack, which the cap then
    forbids: the LP is infeasible BECAUSE of the reliability target.

    That combination is what makes this fixture bite. A version with voll=0
    is infeasible too, but for the wrong reason: with no slack generators the
    cap wrapper never installs a constraint, `_ens_cap_targets` is never
    stashed, and no report would have been built with or without the guard —
    a test that passes either way.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "small", bus="b", carrier="gas", p_nom=CHEAP_MW,
          marginal_cost=10.0)
    return n


def test_infeasible_solve_emits_no_adequacy_report():
    sink, status, condition = _solve_allowing_failure(
        _infeasible_network(), ens_cap_permyriad=1e-4)
    assert status not in ("ok", "optimal"), (
        f"fixture is meant to be infeasible, got {status}/{condition}")
    assert sink.get("adequacy_report") is None, (
        "an infeasible solve produced an adequacy report; every number in it "
        "reads as target-met because there is no dispatch to measure")


def test_infeasible_solve_leaves_no_stale_target_marker():
    """
    The `_ens_cap_targets` marker is stashed on the network by the wrapper and
    consumed by the report builder. On the skip path it must still be cleared,
    or the NEXT solve — with a different target, or none — would build its
    report against this solve's stale targets.
    """
    n = _infeasible_network()
    _solve_allowing_failure(n, ens_cap_permyriad=1e-4)
    assert getattr(n, "_ens_cap_targets", None) is None


def test_feasible_solve_still_emits_the_report():
    """The guard must not cost the working case."""
    sink, status, _ = _solve_allowing_failure(_network(), ens_cap_permyriad=50.0)
    assert status in ("ok", "optimal")
    assert sink.get("adequacy_report") is not None
