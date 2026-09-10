"""Increment-2 Task 7: snapshot metrics and top-k selection.

The fixture below is deliberately tiny and every expected number is written
out longhand in the test, not recomputed from the implementation. That is the
point: `gridspine/ranking/metrics.py` docstrings are quoted verbatim in client
report appendices, so the definitions they state have to be pinned by
arithmetic a reviewer can redo on paper.

Fixture shape (4 hours, 5 units, 2 load buses):

    unit    kind      h_s    mbase   in_inertia   MW*s contribution
    G1      gen       4.0    100.0   True         400
    G2      gen       2.0     50.0   True         100
    EQV     gen     500.0    100.0   True         50000   <- aggregated equivalent
    SLK     ext_grid 30.0    100.0   False        0
    R1      res      (absent from unit_params)    0

    hour   G1    G2    EQV   SLK   R1    | loads A   B
    0      100    50   200    20     0   |     200  170
    1      100  OFF*   200    80   100   |     300  180
    2      OFF*   50   200    10   300   |     350  200
    3      100    50   200    40    50   |     400  200

    * status == 0 with p_mw == 0.0 (validate_dispatch forbids offline output)
"""
import ast

import pandas as pd
import pytest

from gridspine.ranking.metrics import (
    AGGREGATED_EQUIVALENT_H_S_THRESHOLD_S,
    snapshot_metrics,
)
from gridspine.ranking.select import CRITERIA, select_snapshots, validate_selection
from gridspine.schema.contracts import ContractError

# --- fixture --------------------------------------------------------------

# (unit_id, hour, p_mw, q_mvar, status)
_DISPATCH_ROWS = [
    ("G1", 0, 100.0, 10.0, 1),
    ("G2", 0, 50.0, 5.0, 1),
    ("EQV", 0, 200.0, 20.0, 1),
    ("SLK", 0, 20.0, 2.0, 1),
    ("R1", 0, 0.0, 0.0, 1),
    ("G1", 1, 100.0, 10.0, 1),
    ("G2", 1, 0.0, 3.0, 0),
    ("EQV", 1, 200.0, 20.0, 1),
    ("SLK", 1, 80.0, 8.0, 1),
    ("R1", 1, 100.0, 0.0, 1),
    ("G1", 2, 0.0, 4.0, 0),
    ("G2", 2, 50.0, 5.0, 1),
    ("EQV", 2, 200.0, 20.0, 1),
    ("SLK", 2, 10.0, 1.0, 1),
    ("R1", 2, 300.0, 0.0, 1),
    ("G1", 3, 100.0, 10.0, 1),
    ("G2", 3, 50.0, 5.0, 1),
    ("EQV", 3, 200.0, 20.0, 1),
    ("SLK", 3, 40.0, 4.0, 1),
    ("R1", 3, 50.0, 0.0, 1),
]

_LOAD_ROWS = [
    ("BUS_A", 0, 200.0),
    ("BUS_B", 0, 170.0),
    ("BUS_A", 1, 300.0),
    ("BUS_B", 1, 180.0),
    ("BUS_A", 2, 350.0),
    ("BUS_B", 2, 200.0),
    ("BUS_A", 3, 400.0),
    ("BUS_B", 3, 200.0),
]


@pytest.fixture
def dispatch():
    return pd.DataFrame(
        _DISPATCH_ROWS, columns=["unit_id", "hour", "p_mw", "q_mvar", "status"]
    )


@pytest.fixture
def loads():
    df = pd.DataFrame(_LOAD_ROWS, columns=["bus", "hour", "p_mw"])
    df["q_mvar"] = df["p_mw"] * 0.1
    return df


@pytest.fixture
def unit_params():
    df = pd.DataFrame.from_dict(
        {
            "G1": {"h_s": 4.0, "mbase_mva": 100.0, "include_in_inertia": True},
            "G2": {"h_s": 2.0, "mbase_mva": 50.0, "include_in_inertia": True},
            "EQV": {"h_s": 500.0, "mbase_mva": 100.0, "include_in_inertia": True},
            "SLK": {"h_s": 30.0, "mbase_mva": 100.0, "include_in_inertia": False},
        },
        orient="index",
    )
    df["source"] = "assumed"
    df.index.name = "unit_id"
    return df


@pytest.fixture
def registry():
    df = pd.DataFrame(
        {
            "unit_id": ["G1", "G2", "EQV", "SLK", "R1"],
            "bus": ["BUS_A", "BUS_A", "BUS_B", "BUS_B", "BUS_A"],
            "kind": ["gen", "gen", "gen", "ext_grid", "res"],
        }
    ).set_index("unit_id")
    return df


@pytest.fixture
def metrics(dispatch, loads, unit_params, registry):
    return snapshot_metrics(dispatch, loads, unit_params, registry)


@pytest.fixture
def ranked_metrics(metrics):
    """`snapshot_metrics` plus the fifth criterion the driver joins on — the
    AC screen's worst N-1 severity per hour (static/contingency.n1_severity_ac,
    follow-ups F2; DC until then). h3 is the most severe, so it gains a second
    reason and the k=1 selection stays {1, 2, 3}."""
    return metrics.assign(n1_severity_ac=[0.0, 0.1, 0.2, 0.5])


# --- metrics: shape -------------------------------------------------------


def test_metrics_is_indexed_by_hour_with_the_five_documented_columns(metrics):
    assert metrics.index.name == "hour"
    assert list(metrics.index) == [0, 1, 2, 3]
    assert list(metrics.columns) == [
        "load_mw",
        "import_mw",
        "inertia_mws",
        "inertia_excl_equiv_mws",
        "ibr_share",
    ]


# --- metrics: exact hand-computed values ----------------------------------


def test_load_mw_is_the_per_hour_sum_over_load_buses(metrics):
    # 200+170, 300+180, 350+200, 400+200
    assert list(metrics["load_mw"]) == [370.0, 480.0, 550.0, 600.0]


def test_import_mw_is_the_ext_grid_p_only(metrics):
    assert list(metrics["import_mw"]) == [20.0, 80.0, 10.0, 40.0]


def test_inertia_mws_counts_only_online_flagged_units(metrics):
    # h0 400+100+50000; h1 G2 offline; h2 G1 offline; h3 all back on.
    # SLK is include_in_inertia=False and R1 is absent from unit_params,
    # so neither contributes in any hour.
    assert list(metrics["inertia_mws"]) == [50500.0, 50400.0, 50100.0, 50500.0]


def test_inertia_excl_equiv_drops_the_aggregated_equivalent(metrics):
    # Same sums with EQV (h_s=500 >= 100) removed.
    assert list(metrics["inertia_excl_equiv_mws"]) == [500.0, 400.0, 100.0, 500.0]


def test_the_equivalent_threshold_is_a_named_constant_at_100_s():
    assert AGGREGATED_EQUIVALENT_H_S_THRESHOLD_S == 100.0


def test_res_units_absent_from_unit_params_contribute_zero_inertia(metrics):
    # R1 dispatches 300 MW at hour 2 and is not in unit_params at all;
    # hour 2 inertia is G2 alone (+EQV) rather than anything R1-derived.
    assert metrics.loc[2, "inertia_excl_equiv_mws"] == 100.0


def test_ibr_share_is_res_p_over_total_p_per_hour(metrics):
    got = list(metrics["ibr_share"])
    assert got[0] == 0.0                       # no res output at hour 0
    assert got[1] == pytest.approx(100 / 480)  # total 100+0+200+80+100
    assert got[2] == pytest.approx(300 / 560)  # total 0+50+200+10+300
    assert got[3] == pytest.approx(50 / 440)   # total 100+50+200+40+50
    assert ((0.0 <= metrics["ibr_share"]) & (metrics["ibr_share"] <= 1.0)).all()


def test_ibr_share_is_zero_when_the_registry_has_no_res_units(
    dispatch, loads, unit_params, registry
):
    no_res = registry.drop(index="R1")
    m = snapshot_metrics(
        dispatch[dispatch["unit_id"] != "R1"], loads, unit_params, no_res
    )
    assert list(m["ibr_share"]) == [0.0, 0.0, 0.0, 0.0]


def test_ibr_share_is_zero_rather_than_nan_when_nothing_generates(
    dispatch, loads, unit_params, registry
):
    """0/0 -> 0. A NaN here would propagate into the ranking as a silent
    'never selected' rather than the 'nothing running' it actually means."""
    idle = dispatch.copy()
    idle.loc[idle["hour"] == 0, ["p_mw", "status"]] = [0.0, 0]
    m = snapshot_metrics(idle, loads, unit_params, registry)
    assert m.loc[0, "ibr_share"] == 0.0
    assert not m["ibr_share"].isna().any()


# --- metrics: contract guards ---------------------------------------------


def test_metrics_rejects_a_dispatch_unit_missing_from_the_registry(
    dispatch, loads, unit_params, registry
):
    with pytest.raises(ContractError, match="not in the registry"):
        snapshot_metrics(dispatch, loads, unit_params, registry.drop(index="R1"))


def test_metrics_rejects_mismatched_hour_coverage(
    dispatch, loads, unit_params, registry
):
    with pytest.raises(ContractError, match="hour"):
        snapshot_metrics(dispatch, loads[loads["hour"] != 3], unit_params, registry)


def test_metrics_rejects_a_loads_table_missing_columns(
    dispatch, loads, unit_params, registry
):
    with pytest.raises(ContractError, match="loads table"):
        snapshot_metrics(dispatch, loads.drop(columns=["p_mw"]), unit_params, registry)


def test_metrics_propagates_the_dispatch_contract(
    dispatch, loads, unit_params, registry
):
    bad = dispatch.copy()
    bad.loc[0, "status"] = 2
    with pytest.raises(ContractError):
        snapshot_metrics(bad, loads, unit_params, registry)


def test_metrics_rejects_an_unknown_registry_kind(
    dispatch, loads, unit_params, registry
):
    bad = registry.copy()
    bad.loc["R1", "kind"] = "battery"
    with pytest.raises(ContractError, match="kind"):
        snapshot_metrics(dispatch, loads, unit_params, bad)


# --- selection ------------------------------------------------------------


def test_criteria_are_the_five_documented_column_keyed_reasons():
    assert CRITERIA == (
        "min_inertia_excl_equiv_mws",
        "max_ibr_share",
        "max_load_mw",
        "max_import_mw",
        "max_n1_severity",
    )


def test_k1_picks_the_known_extreme_hours(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1)
    assert list(sel.columns) == ["hour", "reasons"]
    assert list(sel["hour"]) == [1, 2, 3]
    # h1 max import (80), h2 min inertia_excl (100) AND max ibr (0.536),
    # h3 max load (600). h0 is extreme in nothing.
    assert list(sel["reasons"]) == [
        ["max_import_mw"],
        ["min_inertia_excl_equiv_mws", "max_ibr_share"],
        ["max_load_mw", "max_n1_severity"],
    ]


def test_an_hour_extreme_in_two_criteria_appears_once_with_both_reasons(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1)
    assert (sel["hour"] == 2).sum() == 1
    reasons = sel.loc[sel["hour"] == 2, "reasons"].iloc[0]
    assert reasons == ["min_inertia_excl_equiv_mws", "max_ibr_share"]


def test_reasons_follow_the_canonical_criteria_order(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=4)
    for reasons in sel["reasons"]:
        assert reasons == list(CRITERIA)


def test_selection_is_sorted_by_hour(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=2)
    assert list(sel["hour"]) == sorted(sel["hour"])
    assert list(sel["hour"]) == [1, 2, 3]


def test_k_larger_than_the_metrics_table_degrades_to_every_hour(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=10)
    assert list(sel["hour"]) == [0, 1, 2, 3]
    assert all(r == list(CRITERIA) for r in sel["reasons"])


def test_ties_are_broken_by_the_earliest_hour(ranked_metrics):
    # inertia_excl_equiv: h2=100 < h1=400 < h0=500 == h3=500.
    # k=3 on that criterion must take h0, not h3.
    sel = select_snapshots(ranked_metrics, k=3)
    by_hour = dict(zip(sel["hour"], sel["reasons"]))
    assert "min_inertia_excl_equiv_mws" in by_hour[0]
    assert "min_inertia_excl_equiv_mws" not in by_hour[3]


def test_min_inertia_ranks_on_the_equivalent_excluded_column(ranked_metrics):
    """The ranking must not be muted by the 50 000 MW*s constant floor the
    aggregated interconnection equivalent contributes to every hour."""
    shifted = ranked_metrics.copy()
    # Make inertia_mws order the exact reverse of inertia_excl_equiv_mws.
    shifted["inertia_mws"] = [1.0, 2.0, 9999.0, 3.0]
    sel = select_snapshots(shifted, k=1)
    by_hour = dict(zip(sel["hour"], sel["reasons"]))
    assert "min_inertia_excl_equiv_mws" in by_hour[2]
    assert 0 not in by_hour


def test_select_rejects_a_non_positive_k(ranked_metrics):
    with pytest.raises(ContractError, match="k"):
        select_snapshots(ranked_metrics, k=0)


def test_select_rejects_a_metrics_table_missing_a_ranked_column(ranked_metrics):
    with pytest.raises(ContractError, match="ibr_share"):
        select_snapshots(ranked_metrics.drop(columns=["ibr_share"]), k=1)


def test_select_rejects_an_empty_metrics_table(ranked_metrics):
    with pytest.raises(ContractError, match="empty"):
        select_snapshots(ranked_metrics.iloc[0:0], k=1)


# --- validate_selection ---------------------------------------------------


def test_validate_selection_accepts_a_real_selection(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=2)
    assert validate_selection(sel, ranked_metrics) is sel


def test_validate_selection_rejects_an_empty_selection(ranked_metrics):
    empty = select_snapshots(ranked_metrics, k=1).iloc[0:0]
    with pytest.raises(ContractError, match="empty"):
        validate_selection(empty, ranked_metrics)


def test_validate_selection_rejects_an_hour_outside_the_metrics_index(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1)
    sel.loc[0, "hour"] = 99
    with pytest.raises(ContractError, match="not in the metrics index"):
        validate_selection(sel, ranked_metrics)


def test_validate_selection_rejects_empty_reasons(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1)
    sel.at[0, "reasons"] = []
    with pytest.raises(ContractError, match="reasons"):
        validate_selection(sel, ranked_metrics)


def test_validate_selection_rejects_an_unknown_reason(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1)
    sel.at[0, "reasons"] = ["max_vibes"]
    with pytest.raises(ContractError, match="max_vibes"):
        validate_selection(sel, ranked_metrics)


def test_validate_selection_rejects_duplicate_hours(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1)
    dup = pd.concat([sel, sel.iloc[[0]]], ignore_index=True)
    with pytest.raises(ContractError, match="duplicate"):
        validate_selection(dup, ranked_metrics)


def test_validate_selection_rejects_missing_columns(ranked_metrics):
    sel = select_snapshots(ranked_metrics, k=1).drop(columns=["reasons"])
    with pytest.raises(ContractError, match="reasons"):
        validate_selection(sel, ranked_metrics)


# --- engine cage ----------------------------------------------------------


def test_ranking_imports_neither_engine():
    """`ranking/` is pure pandas/numpy over the stage-boundary artifacts.

    Parsed rather than grepped: the modules' own docstrings name pypsa and
    pandapower when explaining why they are absent, so a substring scan of
    the source flags its own documentation.

    The assertion is an explicit ALLOWLIST, not a "no engine" denylist. A
    denylist is fail-open for the next import someone adds -- `import
    pandapower.networks as x` under an alias, a transitive helper that pulls
    an engine in, a `gridspine.producers` import that is engine-backed. An
    allowlist makes every widening a decision.
    """
    import gridspine.ranking.metrics as metrics_mod
    import gridspine.ranking.select as select_mod
    import gridspine.ranking.severity as severity_mod

    allowed = {
        "numpy",
        "pandas",
        "gridspine.schema.contracts",
        "gridspine.schema.dispatch",
        "gridspine.schema.dc",
    }
    for mod in (metrics_mod, select_mod, severity_mod):
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported <= allowed, f"{mod.__name__} imports {sorted(imported - allowed)}"
