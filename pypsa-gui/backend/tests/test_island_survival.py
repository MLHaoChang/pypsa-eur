"""Study harness 12 (GK A): island survival of the plan as built, and the
microgrid's input rules. The fixed-design island LP on both shipped
microgrid templates, a seeded undersized battery, the solver's other
endings, and the tie and critical-load predicates."""

from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from services.adequacy import island_config as IC  # noqa: E402
from services.adequacy import island_survival as S  # noqa: E402
from services.solver import island as I  # noqa: E402

pytestmark = [pytest.mark.live_solve]
TEMPLATES = ("eh_microgrid", "eh_microgrid_staged")


def _island(tid):
    return IC.validate(T.ISLAND_CONFIG[tid])


def _own_solve(n, tid):
    """The template's own solve: the island rows only where its config has them on."""
    rows = (
        I.island_extra_functionality(_island(tid))
        if T.solver_config(tid).get("island_constraints")
        else None
    )
    kw = (
        {"multi_investment_periods": True}
        if isinstance(n.snapshots, pd.MultiIndex)
        else {}
    )
    assert n.optimize(solver_name="highs", extra_functionality=rows, **kw) == (
        "ok",
        "optimal",
    )
    return n


class _Solved(dict):
    """Each template's own solve, made when a test first asks for it."""

    def __missing__(self, tid):
        self[tid] = _own_solve(T.BUILDERS[tid](), tid)
        return self[tid]


@pytest.fixture(scope="module")
def solved():
    return _Solved()


def _sizes(n):
    return {
        f"{table}/{nom}": getattr(n, table)[
            [c for c in getattr(n, table).columns if c.startswith(nom)]
        ].copy()
        for table, nom in S._CAPACITY_ATTRS
    }


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_shipped_plan_survives_with_the_facts_of_its_own_rows(solved, tid):
    n = solved[tid]
    before, objective, meta = _sizes(n), float(n.objective), dict(n.meta)
    left = S.extendable(n)
    out = S.survival(n, _island(tid))
    assert (
        out["state"] == S.SURVIVES and out["reason"] is None and out["shortfalls"] == []
    )
    assert out["sustained_h"] == 24.0 and out["scenarios"] == ["seamless"]
    assert out["solves"] == 1 and out["solver"] == {
        "status": "ok",
        "condition": "optimal",
    }
    assert dict(n.meta) == meta
    assert (
        out["critical_loads"] == ["hospital_load"] and out["seamless_bridge"] == "held"
    )
    assert out["fuel"] == {
        "status": "not checked",
        "reason": "the gensets have no fuel bus",
    }
    assert sorted(out["fixed"]) == sorted(left) and left
    periods = [str(int(p)) for p in n.investment_periods] or ["None"]
    assert list(out["periods"]) == periods
    assert all(
        v["window_snapshots"] == 24 and v["critical_peak_mw"] > 0
        for v in out["periods"].values()
    )
    # I1: the caller's network is not written: sizes, extendability and objective as they were.
    assert S.extendable(n) == left and float(n.objective) == objective
    for key, frame in _sizes(n).items():
        pd.testing.assert_frame_equal(frame, before[key])
    own = getattr(n, "_island_facts", None)
    if own and not own.get("skipped"):
        # I2: the same rows as the own solve, so the same facts per period.
        assert I.island_shortfalls(own) == [] and len(I.not_islandable_hours(n)) == 0
        assert out["periods"] == {
            str(p): {
                "window_snapshots": v["window_snapshots"],
                "critical_peak_mw": v["critical_peak_mw"],
            }
            for p, v in own["periods"].items()
        }
        assert out["critical_loads"] == own["critical_loads"]
        assert (
            out["seamless_bridge"] == own["seamless_bridge"]
            and out["fuel"] == own["fuel"]
        )


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_check_solves_with_nothing_left_extendable_and_the_rows_added(solved, tid):
    seen = {}

    def solve(c, extra_functionality, **kw):
        seen["extendable"] = S.extendable(c)
        seen["fixed_design"] = getattr(c, I.FIXED_DESIGN_ATTR, None)
        seen["same"] = c is solved[tid]
        status = S._solve(c, extra_functionality, **kw)
        seen["rows"] = [
            name for name in c.model.constraints if name.startswith("Island-")
        ]
        seen["skipped"] = (c._island_facts or {}).get("skipped")
        return status

    assert S.survival(solved[tid], _island(tid), solve=solve)["state"] == S.SURVIVES
    assert (
        seen["extendable"] == []
        and seen["fixed_design"] is True
        and seen["same"] is False
    )
    assert seen["rows"] and seen["skipped"] is None


@pytest.mark.parametrize("table", [t for t, _ in S._CAPACITY_ATTRS])
def test_every_component_class_is_fixed_at_its_solved_size(table):
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(range(2))
    n.add("Bus", ["a", "b"])
    n.add("Generator", "g", bus="a", p_nom_extendable=True)
    n.add("StorageUnit", "su", bus="a", p_nom_extendable=True)
    n.add("Store", "st", bus="a", e_nom_extendable=True)
    n.add("Link", "lk", bus0="a", bus1="b", p_nom_extendable=True)
    n.add("Line", "ln", bus0="a", bus1="b", x=0.1, s_nom_extendable=True)
    n.add("Transformer", "tr", bus0="a", bus1="b", x=0.1, s_nom_extendable=True)
    nom = dict(S._CAPACITY_ATTRS)[table]
    for t, a in S._CAPACITY_ATTRS:
        getattr(n, t)[f"{a}_opt"] = 7.5
        getattr(n, f"{t}_t")[S._DISPATCH[t]] = pd.DataFrame(
            0.0, index=n.snapshots, columns=getattr(n, t).index
        )
    assert len(S.fix_design(n)) == 6 and S.extendable(n) == []
    assert float(getattr(n, table)[nom].iloc[0]) == 7.5
    # B5: a missing solved size is never fixed at 0.
    getattr(n, table)[f"{nom}_extendable"] = True
    getattr(n, table)[f"{nom}_opt"] = float("nan")
    with pytest.raises(S.NotSolved, match="is extendable and has no solved size"):
        S.fix_design(n)


def test_an_unsolved_network_is_not_solved_never_a_verdict():
    n = T.BUILDERS["eh_microgrid"]()
    out = S.survival(n, _island("eh_microgrid"))
    assert (
        out["state"] == S.NOT_SOLVED
        and out["reason"] == "the network has no solved plan"
    )
    assert out["solves"] == 0 and out["solver"] is None


def test_a_solved_network_missing_a_solved_size_is_not_solved(solved, monkeypatch):
    from services.study.findings import copy_network

    n = copy_network(solved["eh_microgrid"])
    monkeypatch.setattr(S, "_solved", lambda _n: True)
    n.storage_units.at["bess_new", "p_nom_opt"] = float("nan")
    out = S.survival(n, _island("eh_microgrid"))
    assert out["state"] == S.NOT_SOLVED
    assert out["reason"] == "bess_new is extendable and has no solved size"


def test_an_asset_added_since_the_solve_is_not_fixed_at_zero(solved):
    """B5: PyPSA holds ``p_nom_opt`` 0 for an asset the solve never saw; that is no solved size."""
    from services.study.findings import copy_network

    n = copy_network(solved["eh_microgrid"])
    n.add("Generator", "new_pv", bus="island", carrier="solar", p_nom_extendable=True)
    assert float(n.generators.at["new_pv", "p_nom_opt"]) == 0.0
    with pytest.raises(
        S.NotSolved, match="new_pv is extendable and has no solved size"
    ):
        S.fix_design(n)


def test_the_check_commits_only_the_gensets_the_own_solve_committed(solved):
    """I2: with linearised unit commitment on, a genset the own solve sized is not committed by the check."""
    raw = {**T.ISLAND_CONFIG["eh_microgrid"]}
    raw["requirements"] = {**raw["requirements"], "linearised_uc": True}
    island = IC.validate(raw)
    n = solved["eh_microgrid"]
    own_committed, own_skipped = I.uc_gensets(n, island)
    assert "diesel_new" in own_skipped and own_committed
    seen = {}

    def solve(c, extra_functionality, **kw):
        seen["committed"], seen["skipped"] = I.uc_gensets(c, island)
        seen["kw"] = kw
        return S._solve(c, extra_functionality, **kw)

    out = S.survival(n, island, solve=solve)
    assert seen["committed"] == own_committed and set(seen["skipped"]) == set(
        own_skipped
    )
    assert seen["kw"] == {"linearized_unit_commitment": True}
    assert out["state"] in (S.SURVIVES, S.FAILS) and "diesel_new" not in str(
        out["reason"]
    )


def _undersized():
    """The single-year island with its battery cut to 0.2 MW and no new one to buy."""
    n = T.BUILDERS["eh_microgrid"]()
    n.storage_units.at["island_battery", "p_nom"] = 0.2
    n.storage_units.at["bess_new", "p_nom_max"] = 0.0
    return _own_solve(n, "eh_microgrid")


def test_a_seeded_undersized_battery_fails_survival_with_the_row_named():
    out = S.survival(_undersized(), _island("eh_microgrid"))
    assert out["state"] == S.FAILS
    # The 0.2 MW battery cannot form the island at the instant the tie is lost: a fixed check, named.
    assert out["reason"].startswith(
        "seamless islanding needs grid-forming MVA online at the instant (GFM storage and spinning gensets) "
        "of (1 + gfm_margin) × the critical load: the plan as built is short by "
    )
    assert float(out["reason"].rsplit(" ", 1)[1]) > 0 and out["binding"] == []


@pytest.mark.parametrize("ending", [("warning", "infeasible"), ("ok", "optimal")])
def test_an_event_row_short_of_its_step_fails_with_the_shortfall_in_words(
    solved, ending
):
    """An event row the grid formers cannot pick up is a fail whatever the solver said, never a pass."""

    def solve(c, fn, **kw):
        c._island_facts = {
            "events": [
                {"row": "Island-e2-up", "step_mw": 7.5, "gfm_mw": 2.0, "genset_mw": 1.5}
            ],
            "critical_loads": ["hospital_load"],
        }
        return ending

    out = S.survival(solved["eh_microgrid"], _island("eh_microgrid"), solve=solve)
    sentence = (
        "the largest load step is 7.50 MW, but the grid formers can pick up at most 3.50 MW at the "
        "instant (GFM storage at its rating 2.00 MW, the online gensets' load step 1.50 MW): "
        "4.00 MW is missing."
    )
    assert out["state"] == S.FAILS and out["shortfalls"] == [
        {"row": "Island-e2-up", "sentence": sentence}
    ]
    assert out["reason"] == f"Island-e2-up: {sentence}" and out["critical_loads"] == [
        "hospital_load"
    ]


@pytest.mark.parametrize(
    "ending, state, reason",
    [
        (
            ("warning", "infeasible"),
            S.FAILS,
            "the plan as built cannot hold the island rows (the solve with them is infeasible)",
        ),
        (
            ("warning", "infeasible_or_unbounded"),
            S.FAILS,
            "the plan as built cannot hold the island rows (the solve with them is infeasible)",
        ),
        (
            ("warning", "time_limit"),
            S.NOT_EVALUATED,
            "the island check's solve ended time limit, not optimal",
        ),
        (
            ("warning", "unknown"),
            S.NOT_EVALUATED,
            "the island check's solve ended unknown, not optimal",
        ),
    ],
)
def test_the_solver_endings_stay_apart(solved, ending, state, reason):
    out = S.survival(
        solved["eh_microgrid"],
        _island("eh_microgrid"),
        solve=lambda c, fn, **kw: ending,
    )
    assert (out["state"], out["reason"]) == (state, reason)


def test_a_solver_error_is_not_evaluated_and_a_refused_row_fails(solved):
    def boom(c, fn, **kw):
        raise RuntimeError("highs died")

    out = S.survival(solved["eh_microgrid"], _island("eh_microgrid"), solve=boom)
    assert (out["state"], out["reason"]) == (
        S.NOT_EVALUATED,
        "the island check's solve stopped with an error (RuntimeError)",
    )
    assert out["solves"] == 1 and out["solver"] is None  # an attempted solve counts

    def refuse(c, fn, **kw):
        raise I.IslandRefusal(
            "sustained_h (48 h) is longer than the period (24 h) the ride-through is checked over"
        )

    # A refusal that is not a short row is a bad input: no verdict on the plan.
    out = S.survival(solved["eh_microgrid"], _island("eh_microgrid"), solve=refuse)
    assert (out["state"], out["reason"]) == (
        S.NOT_EVALUATED,
        "the island rows could not be built: sustained_h (48 h) is longer than the period "
        "(24 h) the ride-through is checked over",
    )


def test_a_short_fixed_check_names_its_row_with_or_without_a_refusal_text(solved):
    lhs, rhs = I._Lin(), I._Lin().plus(1.25)
    for refuse, row in (
        (None, "Island-power"),
        ("the gensets cannot carry the critical load",) * 2,
    ):
        with pytest.raises(I.IslandRefusal) as exc:
            I._row(None, lhs, rhs, "Island-power", None, refuse=refuse)
        assert (exc.value.row, exc.value.short) == (row, 1.25)
        assert (
            str(exc.value) == f"{row}: short by 1.25 with nothing this solve can size"
        )

    def refuse_row(c, fn, **kw):
        I._row(None, lhs, rhs, "Island-power", None)

    out = S.survival(solved["eh_microgrid"], _island("eh_microgrid"), solve=refuse_row)
    assert (out["state"], out["reason"]) == (
        S.FAILS,
        "Island-power: the plan as built is short by 1.25",
    )


def test_an_ok_solve_without_the_rows_is_not_a_pass(solved):
    out = S.survival(
        solved["eh_microgrid"],
        _island("eh_microgrid"),
        solve=lambda c, fn, **kw: ("ok", "optimal"),
    )
    assert (out["state"], out["reason"]) == (
        S.NOT_EVALUATED,
        "the island rows were not added to the solve",
    )


def test_the_rows_are_skipped_on_a_dispatch_only_solve_unless_checked(solved):
    from services.study.findings import copy_network

    for check, skipped in ((False, True), (True, False)):
        c = copy_network(solved["eh_microgrid"])
        S.fix_design(c)
        c.optimize(
            solver_name="highs",
            extra_functionality=I.island_extra_functionality(
                _island("eh_microgrid"), check=check
            ),
        )
        assert bool(c._island_facts.get("skipped")) is skipped


# ── the input rules ────────────────────────────────────────────────────────


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_shipped_tie_is_open_and_the_critical_load_agrees(tid):
    n = T.BUILDERS[tid]()
    assert S.tie_state(n) == {"state": "open", "ties": ["subsea_tie"], "carrying": []}
    assert S.critical_state(n, _island(tid)) == {
        "state": "tagged",
        "loads": ["hospital_load"],
        "network_only": [],
        "sidecar_only": [],
    }


def test_a_tie_that_can_carry_power_in_any_hour_or_direction_is_closed():
    def state(change):
        n = T.BUILDERS["eh_microgrid"]()
        change(n)
        return S.tie_state(n)

    def set_tie(attr, value):
        return lambda n: n.links.loc[["subsea_tie"], attr].pipe(
            lambda _old: n.links.__setitem__(
                attr, n.links[attr].where(n.links.index != "subsea_tie", value)
            )
        )

    assert state(set_tie("p_max_pu", 1.0))["carrying"] == ["subsea_tie"]
    # Export only: nothing flows in, but the island is not alone.
    out = state(set_tie("p_min_pu", -1.0))
    assert out["state"] == "closed" and out["carrying"] == ["subsea_tie"]

    def one_hour(n):
        series = pd.Series(0.0, index=n.snapshots)
        series.iloc[5] = 0.3
        n.links_t.p_max_pu["subsea_tie"] = series

    assert state(one_hour)["state"] == "closed"
    # A second tie left open, untagged: a Link and a Line on the PoC bus.
    out = state(
        lambda n: n.add("Link", "tie_2", bus0="mainland", bus1="island", p_nom=5.0)
    )
    assert out == {
        "state": "closed",
        "ties": ["subsea_tie", "tie_2"],
        "carrying": ["tie_2"],
    }
    out = state(
        lambda n: n.add(
            "Line", "cable", bus0="island", bus1="mainland", x=0.1, s_nom=5.0
        )
    )
    assert out["state"] == "closed" and out["carrying"] == ["cable"]
    out = state(
        lambda n: n.add(
            "Transformer", "tx", bus0="mainland", bus1="island", x=0.1, s_nom=5.0
        )
    )
    assert out["state"] == "closed" and out["carrying"] == ["tx"]
    # A Link whose third port is the mainland bus.
    out = state(
        lambda n: n.add(
            "Link", "three", bus0="island", bus1="hospital", bus2="mainland", p_nom=5.0
        )
    )
    assert out["state"] == "closed" and out["carrying"] == ["three"]

    def series(table, attr, name, value):
        def change(n):
            n.add(table[0], name, bus0="island", bus1="mainland", **table[1])
            s = pd.Series(0.0, index=n.snapshots)
            s.iloc[7] = value
            getattr(n, table[2])[attr][name] = s

        return change

    line = ("Line", {"x": 0.1, "s_nom": 5.0, "s_max_pu": 0.0}, "lines_t")
    assert state(series(line, "s_max_pu", "cable", 0.4))["carrying"] == ["cable"]
    link = ("Link", {"p_nom": 5.0, "p_max_pu": 0.0}, "links_t")
    assert state(series(link, "p_min_pu", "tie_2", -0.4))["carrying"] == ["tie_2"]
    # At zero size, or at zero in every hour, a second tie is still open.
    assert (
        state(
            lambda n: n.add("Link", "tie_2", bus0="mainland", bus1="island", p_nom=0.0)
        )["state"]
        == "open"
    )
    assert (
        state(
            lambda n: n.add(
                "Line",
                "cable",
                bus0="island",
                bus1="mainland",
                x=0.1,
                s_nom=5.0,
                s_max_pu=0.0,
            )
        )["state"]
        == "open"
    )
    # An extendable tie at 0 MW can still be built, unless its largest size is 0.
    assert (
        state(
            lambda n: n.add(
                "Link",
                "tie_2",
                bus0="mainland",
                bus1="island",
                p_nom=0.0,
                p_nom_extendable=True,
            )
        )["state"]
        == "closed"
    )
    assert (
        state(
            lambda n: n.add(
                "Link",
                "tie_2",
                bus0="mainland",
                bus1="island",
                p_nom=0.0,
                p_nom_extendable=True,
                p_nom_max=0.0,
            )
        )["state"]
        == "open"
    )


def test_no_poc_bus_and_no_tagged_link_is_no_tie():
    n = T.BUILDERS["eh_microgrid"]()
    n.buses["eh_poc"] = False
    n.links["eh_role"] = ""
    assert S.tie_state(n) == {"state": "no_tie", "ties": [], "carrying": []}


def test_the_critical_tag_and_the_sidecar_must_name_the_same_load():
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.buses["eh_critical"] = False
    assert S.critical_state(n, island) == {
        "state": "mismatch",
        "loads": [],
        "network_only": [],
        "sidecar_only": ["hospital_load"],
    }
    n.buses.at["island", "eh_critical"] = True
    assert S.critical_state(n, island) == {
        "state": "mismatch",
        "loads": [],
        "network_only": ["residential"],
        "sidecar_only": ["hospital_load"],
    }
    n = T.BUILDERS["eh_microgrid"]()
    raw = {
        **T.ISLAND_CONFIG["eh_microgrid"],
        "units": {
            **T.ISLAND_CONFIG["eh_microgrid"]["units"],
            "hospital_load": {"kind": "load", "critical": False},
        },
    }
    assert S.critical_state(n, IC.validate(raw)) == {
        "state": "mismatch",
        "loads": [],
        "network_only": ["hospital_load"],
        "sidecar_only": [],
    }
    n.buses["eh_critical"] = False
    assert S.critical_state(n, IC.validate(raw))["state"] == "none"


# ── cases the mutant run asked for ──────────────────────────────────────────


def test_an_infinite_solved_size_is_no_solved_size():
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(range(2))
    n.add("Bus", "a")
    n.add("Generator", "g", bus="a", p_nom_extendable=True)
    n.generators["p_nom_opt"] = float("inf")
    n.generators_t["p"] = pd.DataFrame(0.0, index=n.snapshots, columns=["g"])
    with pytest.raises(S.NotSolved, match="g is extendable and has no solved size"):
        S.fix_design(n)


@pytest.mark.parametrize("objective", [float("nan"), float("inf"), "n/a"])
def test_an_objective_that_is_not_a_finite_number_is_not_solved(objective):
    n = T.BUILDERS["eh_microgrid"]()
    n._objective = objective
    out = S.survival(n, _island("eh_microgrid"))
    assert (out["state"], out["reason"]) == (
        S.NOT_SOLVED,
        "the network has no solved plan",
    )
    assert out["fixed"] == [] and out["periods"] == {} and out["missing"] == {}


def test_rows_the_solve_skipped_are_not_a_pass_and_missing_units_are_reported(solved):
    def solve(c, fn, **kw):
        c._island_facts = {"skipped": "dispatch-only solve"}
        return ("ok", "optimal")

    out = S.survival(solved["eh_microgrid"], _island("eh_microgrid"), solve=solve)
    assert (out["state"], out["reason"]) == (
        S.NOT_EVALUATED,
        "the island rows were not added to the solve",
    )

    def solve(c, fn, **kw):
        c._island_facts = {"missing": {"genset": ["ghost"]}}
        return ("warning", "infeasible")

    out = S.survival(solved["eh_microgrid"], _island("eh_microgrid"), solve=solve)
    assert out["state"] == S.FAILS and out["missing"] == {"genset": ["ghost"]}


def test_a_branch_between_two_poc_buses_is_not_a_tie():
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Bus", "mainland_2")
    n.buses.at["mainland_2", "eh_poc"] = True
    n.add("Line", "grid_side", bus0="mainland", bus1="mainland_2", x=0.1, s_nom=5.0)
    assert S.tie_state(n) == {"state": "open", "ties": ["subsea_tie"], "carrying": []}


def test_only_the_port_columns_are_read_as_a_branch_s_ends():
    n = T.BUILDERS["eh_microgrid"]()
    n.links["bus_zone"] = "mainland"
    n.links["tag1"] = "mainland"
    assert S.tie_state(n)["ties"] == ["subsea_tie"]
