"""
Phase 12g — a NaN in ANY finite-default LP input is refused, not read as zero.

12f refused NaN in five bounds. Measuring the next backlog item ("three storage
constants mask an energy-balance row") widened it: on two fixtures a NaN in
twenty-three distinct finite-default attributes changes the plan silently and
one crashes the build. PyPSA's own `n.add(attr=None)` and `n.add(attr=NaN)`
write the class default for every one of them, so for a finite-default
attribute NaN is never "unset". The set is read from PyPSA's metadata (`status`
Input, numeric type, finite default) over a PINNED component set — the seven
LP components plus GlobalConstraint, whose NaN `constant` deletes a CO2 cap.

Every ★ names the broken variant it must fail against, and each was applied
and demonstrated RED before this file was allowed to go green.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

import routers.network as N
import services.validation_service as V
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig


def _errors(n):
    return [i for i in V.validate_for_run(n, SolverConfig()) if i.severity == "error"]


def _all_components_network():
    """One asset of every pinned component, validating CLEAN — the anti-gap
    test needs a fixture where the only error is the one it places."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Carrier", "gas", co2_emissions=1.0)
    n.add("Carrier", "AC")
    n.add("Bus", "b", v_nom=380.0)
    n.add("Bus", "b2", v_nom=380.0)
    n.add("Bus", "b3", v_nom=110.0)
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=200.0, marginal_cost=10.0)
    n.add("Generator", "slack", bus="b2", p_nom=5000.0, marginal_cost=999.0)
    n.add("Link", "lk", bus0="b", bus1="b2", p_nom=100.0, efficiency=0.95)
    n.add("Line", "ln", bus0="b", bus1="b2", s_nom=100.0, x=0.1, r=0.01)
    n.add("Transformer", "tr", bus0="b2", bus1="b3", s_nom=100.0, x=0.1, r=0.01)
    n.add("StorageUnit", "su", bus="b", p_nom=50.0, max_hours=2.0)
    n.add("Store", "st", bus="b", e_nom=100.0)
    n.add("GlobalConstraint", "co2", sense="<=", constant=100.0,
          carrier_attribute="co2_emissions")
    return n


_LIST = {"Generator": "generators", "Link": "links", "Line": "lines",
         "Transformer": "transformers", "StorageUnit": "storage_units",
         "Store": "stores", "Load": "loads", "GlobalConstraint": "global_constraints"}
_ASSET = {"Generator": "g", "Link": "lk", "Line": "ln", "Transformer": "tr",
          "StorageUnit": "su", "Store": "st", "Load": "l", "GlobalConstraint": "co2"}


# ── J1: the preflight, per category, through validate_for_run ─────────────

@pytest.mark.parametrize("comp,attr,code", [
    ("Generator", "marginal_cost", "nonfinite_cost"),
    ("StorageUnit", "standing_loss", "nonfinite_efficiency"),
    ("StorageUnit", "state_of_charge_initial", "nonfinite_storage_constant"),
    ("Store", "e_initial", "nonfinite_storage_constant"),
    ("GlobalConstraint", "constant", "nonfinite_storage_constant"),
    ("Generator", "sign", "nonfinite_input"),
    ("Link", "delay", "nonfinite_input"),
])
def test_J1a_a_static_nan_is_refused_with_its_category(comp, attr, code):
    """★ J1a. One code per consequence category, through `validate_for_run`
    (not the helper — this program's recurring error). Bite (verified):
    return `nonfinite_input` for every attribute."""
    n = _all_components_network()
    assert _errors(n) == [], [i.code for i in _errors(n)]
    getattr(n, _LIST[comp]).at[_ASSET[comp], attr] = np.nan
    errs = _errors(n)
    assert [i.code for i in errs] == [code], [(i.code, i.message) for i in errs]
    assert errs[0].component_class == comp and errs[0].name == _ASSET[comp]
    assert f"'{attr}'" in errs[0].message


def test_J1a2_the_sentences_the_review_corrected():
    """The three mechanism sentences plan v1 had wrong, pinned: a NaN
    `standing_loss` drops the carry-over (the store forgets, it is not
    lossless); `up_time_before` ADDS a constraint; `delay` drops the
    receiving end."""
    n = _all_components_network()
    n.storage_units.at["su", "standing_loss"] = np.nan
    n.generators.at["g", "up_time_before"] = np.nan
    n.links.at["lk", "delay"] = np.nan
    msgs = {(i.component_class, i.name): i.message for i in _errors(n)}
    assert "carry-over" in msgs[("StorageUnit", "su")]
    assert "adds a start-up ramp" in msgs[("Generator", "g")]
    assert "receiving end" in msgs[("Link", "lk")]


def test_J1b_a_partial_inflow_series_is_a_coverage_error():
    """★ J1b. The deferred defect of 12f's plan: an inflow series covering
    part of the horizon masks that hour's energy-balance row (measured:
    dispatch from an empty store). Bite (verified): judge dynamic columns
    only for the five bounds."""
    n = _all_components_network()
    n.storage_units_t["inflow"] = pd.DataFrame({"su": [1.0, 1.0]},
                                               index=n.snapshots[:2])
    errs = _errors(n)
    assert [i.code for i in errs] == ["nonfinite_storage_constant_partial_coverage"], \
        [(i.code, i.message) for i in errs]
    assert "covers 2 of 3 snapshots" in errs[0].message


def _cases():
    n = _all_components_network()
    out = []
    for comp in V.NONFINITE_INPUT_COMPONENTS:
        for attr, (dv, varying, typ) in V.finite_default_inputs(n.components[comp]).items():
            out.append((comp, attr, "static"))
            if varying:
                out.append((comp, attr, "dynamic"))
    return out


@pytest.mark.parametrize("comp,attr,where", _cases())
def test_J1c_every_finite_default_input_is_refused_exactly_once(comp, attr, where):
    """★ J1c — the anti-gap witness. Every finite-default input of every
    pinned component, static and (where varying) dynamic, is refused by
    EXACTLY one error naming the asset — whether by a specific check that
    owns it or by the generic walk. Derived from the installed PyPSA's
    metadata, so an attribute PyPSA adds fails this rather than slipping
    past. Bite (verified): restrict the walk to 12f's five — 100+ cases go
    silent."""
    n = _all_components_network()
    name = _ASSET[comp]
    if where == "static":
        getattr(n, _LIST[comp]).at[name, attr] = np.nan
    else:
        cur = float(getattr(n, _LIST[comp]).at[name, attr])
        getattr(n, f"{_LIST[comp]}_t")[attr] = pd.DataFrame(
            {name: [cur, np.nan, cur]}, index=n.snapshots)
    errs = [i for i in _errors(n) if i.name == name]
    assert len(errs) == 1, [(i.code, i.message) for i in errs]
    assert errs[0].component_class == comp


def test_J1d_two_nans_on_one_line_are_two_errors():
    """★ J1d. The review of plan v1 rejected a dedupe that text-matched the
    attribute name in an existing message: Line's attributes are single
    letters and `line_x_invalid`'s message contains `b` (in "be"). Ownership
    is a set, not a substring. Bite (verified): add `("Line", "b")` to the
    owned set — the `b` error vanishes."""
    n = _all_components_network()
    n.lines.at["ln", "x"] = np.nan
    n.lines.at["ln", "b"] = np.nan
    errs = [i for i in _errors(n) if i.name == "ln"]
    assert sorted(i.code for i in errs) == ["line_x_invalid", "nonfinite_input"], \
        [(i.code, i.message) for i in errs]


def test_J1e_the_golden_network_is_silent_and_the_set_is_the_metadata_s():
    """★ J1e. The ramp-limit lesson, stated twice: the golden fixture (eight
    NaN-default cells, zero finite-default ones) stays silent, AND the
    derived set is exactly the metadata's — the review of plan v1 found that
    dropping the `Input`-status or the numeric-type clause leaves the golden
    fixture clean too, so silence alone tests one clause of three. Bite
    (verified): drop the status clause — GlobalConstraint grows past 1 and
    Generator past 24."""
    from tests.golden import fixture as gf
    n = gf.build_golden_network()
    assert V._check_nonfinite_inputs(n) == []
    counts = {c: len(V.finite_default_inputs(n.components[c]))
              for c in V.NONFINITE_INPUT_COMPONENTS}
    assert counts == {"Generator": 24, "Link": 24, "Line": 14, "Transformer": 18,
                      "StorageUnit": 20, "Store": 15, "Load": 3,
                      "GlobalConstraint": 1}, counts
    assert "ramp_limit_up" not in V.finite_default_inputs(n.components["Generator"])
    assert "p_set" not in V.finite_default_inputs(n.components["Generator"])


def test_J1f_a_multi_port_attribute_is_judged_only_where_the_port_exists():
    """★ J1f. A multi-port link adds `efficiency2`/`delay2` to the Link
    metadata for the whole network; on a two-port link they are inert and
    `link_efficiency_invalid` already gates on `bus2`. Bite (verified): drop
    the `_port_absent` gate — the two-port link is refused."""
    n = _all_components_network()
    n.add("Bus", "b4")
    n.add("Link", "lk3", bus0="b", bus1="b2", bus2="b4", p_nom=10.0, efficiency2=0.5)
    assert "efficiency2" in V.finite_default_inputs(n.components["Link"])
    n.links.at["lk", "efficiency2"] = np.nan          # two-port: no bus2
    assert [i for i in _errors(n) if i.name == "lk"] == []
    n.links.at["lk3", "delay2"] = np.nan              # three-port: bus2 set
    errs = [i for i in _errors(n) if i.name == "lk3"]
    assert [i.code for i in errs] == ["nonfinite_input"], errs


def test_J1g_a_nominal_on_an_extendable_row_is_the_generic_walk_s():
    """★ J1g. `_check_extendable_bounds` refuses a NaN `p_nom` only on a
    non-extendable row; on an extendable one the LP never reads `p_nom`, so
    the generic walk refuses it with the neutral sentence. Bite (verified):
    own `p_nom` unconditionally — the extendable case is silent."""
    n = _all_components_network()
    n.generators.at["g", "p_nom"] = np.nan
    assert [i.code for i in _errors(n) if i.name == "g"] == ["generator_p_nom_invalid"]
    n.generators.at["g", "p_nom_extendable"] = True
    n.generators.at["g", "p_nom_max"] = 500.0
    n.generators.at["g", "capital_cost"] = 100.0
    errs = [i for i in _errors(n) if i.name == "g"]
    assert [i.code for i in errs] == ["nonfinite_input"], [(i.code, i.message) for i in errs]
    assert "cannot represent 'unset'" in errs[0].message


# ── J2: clearing a field writes PyPSA's default ───────────────────────────

@pytest.mark.parametrize("comp,cls,name,col,expect", [
    ("storage_units", "StorageUnit", "su", "state_of_charge_initial", 0.0),
    ("generators", "Generator", "g", "marginal_cost", 0.0),
    ("generators", "Generator", "g", "sign", 1.0),
    ("stores", "Store", "st", "e_initial", 0.0),
    ("transformers", "Transformer", "tr", "phase_shift_max", 0.0),
])
def test_J2a_bulk_clears_a_finite_default_input_to_its_default(comp, cls, name, col, expect):
    """★ J2a. 12f mapped five columns; every other numeric null still wrote
    NaN, and `phase_shift_max` (default 0.0) took the `_max` suffix rule and
    wrote `inf`, which the next solve refused. The metadata decides first.
    Bite (verified): restore the five-only mapping — `state_of_charge_initial`
    reads NaN, `phase_shift_max` reads inf."""
    n = _all_components_network()
    getattr(n, comp).at[name, col] = 0.37
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": cls, "names": [name], "updates": {col: None}})
    got = getattr(PyPSAService.get_network(), comp).at[name, col]
    assert got == expect and np.isfinite(got), got


def test_J2b_the_rule_does_not_widen_to_custom_or_nan_default_columns():
    """K1d and K1c, restated for the wider set: `discount_rate` (custom) and
    `ramp_limit_up` (NaN default) still clear to NaN."""
    n = _all_components_network()
    n.generators.at["g", "discount_rate"] = 0.07
    n.generators.at["g", "ramp_limit_up"] = 0.5
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"discount_rate": None, "ramp_limit_up": None}})
    g = PyPSAService.get_network().generators
    assert np.isnan(g.at["g", "discount_rate"]) and np.isnan(g.at["g", "ramp_limit_up"])


def test_J2c_an_int_column_keeps_its_dtype_after_a_clear():
    """J2c — a pin, not a ★. The plan review reported pandas 3 upcasting an
    int64 column on any `.loc` write, so a dtype restore was built and its
    bite did NOT bite: measured on 3.0.5, `.loc[[name], col] = 0.0` keeps
    int64 (only NaN upcasts). The restore was removed; this pins the
    behaviour the clear relies on, so a pandas that changes it fails here."""
    n = _all_components_network()
    n.generators.at["g", "build_year"] = 2030
    assert str(n.generators["build_year"].dtype) == "int64"
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"build_year": None}})
    g = PyPSAService.get_network().generators
    assert g.at["g", "build_year"] == 0 and str(g["build_year"].dtype) == "int64"


def test_J2d_a_non_finite_literal_in_any_finite_default_input_is_refused():
    """★ J2d. R5 widened: `NaN`/`Infinity` literals in `inflow` are 422; in
    `discount_rate` they still write (custom column, NaN is its unset). Bite
    (verified): restrict the literal check to the five."""
    from fastapi import HTTPException
    n = _all_components_network()
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.bulk_update({"component_class": "StorageUnit", "names": ["su"],
                       "updates": {"inflow": float("nan")}})
    assert e.value.status_code == 422
    assert PyPSAService.get_network().storage_units.at["su", "inflow"] == 0.0
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"discount_rate": float("nan")}})
    assert np.isnan(PyPSAService.get_network().generators.at["g", "discount_rate"])


# ── J3: the time-series boundary already follows the rule — pinned ────────

def test_J3a_a_null_inflow_hour_is_refused_at_the_put():
    """Pin of R4 for this phase's attributes: `storage_units/inflow` has a
    finite default, so a null is 422 at the boundary."""
    from fastapi import HTTPException
    n = _all_components_network()
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.set_timeseries("storage_units", "inflow",
                         {"index": [str(x) for x in n.snapshots], "columns": ["su"],
                          "data": [[1.0], [None], [1.0]]})
    assert e.value.status_code == 422


# ── J4: the create schemas ───────────────────────────────────────────────

def _finite_schema_fields():
    from models import schemas as S
    n = pypsa.Network()
    MAP = {"GeneratorCreate": "Generator", "LinkCreate": "Link", "LineCreate": "Line",
           "TransformerCreate": "Transformer", "StorageUnitCreate": "StorageUnit",
           "StoreCreate": "Store", "LoadCreate": "Load"}
    expected, annotated, ints = set(), set(), set()
    for cls, comp in MAP.items():
        m = getattr(S, cls)
        meta = V.finite_default_inputs(n.components[comp])
        for f, info in m.model_fields.items():
            if f not in meta:
                continue
            if meta[f][2] == "int":
                ints.add((cls, f))
                continue
            expected.add((cls, f))
            if any(getattr(md, "allow_inf_nan", None) is False for md in info.metadata):
                annotated.add((cls, f))
    return expected, annotated, ints


def test_J4a_the_finite_annotation_covers_exactly_the_metadata_s_float_fields():
    """★ J4a. 58 float fields, derived from PyPSA's table, not listed by
    hand; the 9 int fields refuse non-finite as `int` and stay int. Bite
    (verified): drop the annotation from `inflow`."""
    expected, annotated, ints = _finite_schema_fields()
    assert expected == annotated, sorted(expected ^ annotated)
    assert len(expected) == 58 and len(ints) == 9, (len(expected), len(ints))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "Infinity"])
def test_J4b_a_non_finite_value_in_a_finite_default_field_is_refused(value):
    import pydantic
    from models import schemas as S
    with pytest.raises(pydantic.ValidationError):
        S.StorageUnitCreate.model_validate({"name": "s", "bus": "b", "inflow": value})
    with pytest.raises(pydantic.ValidationError):
        S.GeneratorCreate.model_validate({"name": "g", "bus": "b", "build_year": value})
    assert S.StorageUnitCreate.model_validate({"name": "s", "bus": "b", "inflow": 3.0}).inflow == 3.0


# ── J6: end to end through run_simulation ────────────────────────────────

def test_J6a_run_simulation_refuses_a_partial_inflow_on_a_background_solve():
    """★ J6a. The bite the plan's review reproduced on shipped code: a 2-of-3
    inflow PUT is accepted, preflight was silent, the reapply reindexed to
    `[0, 0, NaN]`, and the solve ran `optimal` dispatching from an empty
    store. Now refused with the storage unit named. Bite (verified): restrict
    the walk to the five — `('ok', 'optimal')`."""
    from tests.test_nonfinite_bounds import _run
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", p_nom=200.0, marginal_cost=10.0)
    n.add("StorageUnit", "su", bus="b", p_nom=100.0, max_hours=2.0)
    n.storage_units_t["inflow"] = pd.DataFrame({"su": [0.0, 0.0]}, index=n.snapshots[:2])
    status, cond, lines = _run(n, SolverConfig(), foreground=False)
    assert (status, cond) == ("error", "validation_failed"), (status, cond)
    assert any("[VALIDATION] ERROR: StorageUnit 'su'" in ln and "inflow" in ln
               for ln in lines), lines


# ── J5: both planning loops refuse every category up front ────────────────

@pytest.mark.parametrize("which", ["coupling", "margin"])
@pytest.mark.parametrize("attr,code", [
    ("state_of_charge_initial", "nonfinite_storage_constant"),
    ("marginal_cost", "nonfinite_cost"),
    ("sign", "nonfinite_input"),
])
def test_J5a_both_loops_refuse_every_category_up_front(which, attr, code):
    """★ J5a. The margin loop's guard filtered on a literal tuple of 12f's two
    codes (plan-v1 review, finding 1), so every category 12g adds would have
    slipped past it: the loop starts, every iterate refuses, and it ends
    `budget_exhausted` advising "raise max_solves" — the K6 outcome. Filtered
    by prefix now. Bite (verified): restore the tuple — the margin cases
    start a study instead of raising."""
    import routers.results as R
    from fastapi import HTTPException
    from routers.simulation import _state
    from tests.test_nonfinite_bounds import _cfg_with_margin, _margin_ready_network

    n = _margin_ready_network()
    n.generators_t.p_max_pu["g"] = [1.0, 1.0, 1.0]        # 12f's own defect out
    n.add("StorageUnit", "su", bus="b", p_nom=10.0, max_hours=2.0, carrier="gas",
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=24.0)
    comp = "storage_units" if attr == "state_of_charge_initial" else "generators"
    name = "su" if comp == "storage_units" else "g"
    getattr(n, comp).at[name, attr] = np.nan
    PyPSAService.set_network(n)
    _state["solver_config"] = _cfg_with_margin()
    for _k in ("coupling_loop", "margin_loop", "mc", "frontier", "fmea_sweep"):
        _state.pop(_k, None)
    try:
        with pytest.raises(HTTPException) as e:
            if which == "coupling":
                R.post_coupling_loop(body=R.CouplingLoopRequest(target_lole_h=1.0))
            else:
                R.post_margin_loop(body=R.MarginLoopRequest(target_lole_h=1.0))
        assert e.value.status_code == 422
        assert attr in str(e.value.detail)
    finally:
        for _k in ("coupling_loop", "margin_loop", "mc", "frontier", "fmea_sweep"):
            rec = _state.pop(_k, None)
            th = (rec or {}).get("thread") if isinstance(rec, dict) else None
            if th is not None:
                ev = rec.get("stop_event")
                if ev is not None:
                    ev.set()
                th.join(timeout=30)
