"""
Dispatch-freshness helpers.

A PyPSA network can be in three states regarding result tables (``*_t.p``,
``*_t.p0`` etc.):

* ``none``     — no solve has ever populated dispatch tables (or they were
                 explicitly cleared). The network is unsolved.
* ``fresh``    — dispatch tables exist AND their column-set matches the
                 current topology. Results are consistent with the model.
* ``stale``    — dispatch tables exist but their column-set differs from
                 the current topology. Typical cause: user added/removed a
                 bus or generator AFTER a solve without re-running. PyPSA
                 does NOT auto-clear ``_t`` tables on topology mutations,
                 so the dispatch lingers under the old shape.

The third case is the foot-gun this module exists to detect. Without it,
``has_dispatch`` (just "not empty") reports True, ``is_solved`` reports
True (it persists across netcdf round-trips), and the UI shows charts
built on an inconsistent solution.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
import pypsa

DispatchStatus = Literal["none", "fresh", "stale"]


def network_has_dispatch(n) -> bool:
    """
    True when the network carries ANY result dispatch (generator power, line
    flow, or storage state-of-charge time-series).

    Looser than `dispatch_status` (which also checks topology-consistency) —
    this is the "did a solve ever populate results?" gate the save / load /
    import-bundle / restore-snapshot lifecycle uses to decide whether to mark a
    project 'completed' vs 'idle'. It was an inline copy at each of those 5
    sites (routers/projects.py ×4, routers/snapshots.py ×1); centralised here.
    """
    return (
        (not n.generators.empty    and not n.generators_t.p.empty) or
        (not n.lines.empty         and not n.lines_t.p0.empty)     or
        (not n.storage_units.empty and not n.storage_units_t.state_of_charge.empty)
    )

# The (component_df_attr, t_df_attr, t_col_attr) triples we check.
# Originally only covered generators/lines/storage_units/loads — but stores,
# links, and transformers ALSO carry dispatch tables that survive topology
# mutations (e.g. user renames a link → links_t.p0 columns still reference
# the old name). Without surveying these, `/results/links` / `/store_dispatch`
# could serve stale dispatch on a network that `dispatch_status` reports as
# fresh. Final-QA-flagged 🟡; coverage expanded to mirror the breadth of
# `_CLEARABLE_T_ATTRS` below (which `clear_dispatch` empties on mutation).
_DISPATCH_AXES: tuple[tuple[str, str, str], ...] = (
    ("generators",    "generators_t",    "p"),
    ("lines",         "lines_t",         "p0"),
    ("storage_units", "storage_units_t", "state_of_charge"),
    ("stores",        "stores_t",        "e"),
    ("loads",         "loads_t",         "p"),
    ("links",         "links_t",         "p0"),
    ("transformers",  "transformers_t",  "p0"),
)


def dispatch_status(n: pypsa.Network) -> DispatchStatus:
    """
    Classify ``n``'s dispatch state.

    Iterates the dispatch axes. If any axis has a populated dispatch
    DataFrame whose columns don't match its component DataFrame's index,
    the network is stale. If at least one axis has matching dispatch,
    it's fresh. Otherwise none.

    Cheap to call — just shape + index comparison, no data access.
    """
    has_any = False
    for comp, t_attr, col_attr in _DISPATCH_AXES:
        comp_df = getattr(n, comp)
        t_df = getattr(getattr(n, t_attr), col_attr)
        if comp_df.empty or t_df.empty:
            continue
        has_any = True
        # `t_df.columns` is the dispatch's view of the components. If it
        # diverges from `comp_df.index`, the dispatch is for a different
        # set of components than the network currently holds.
        if set(t_df.columns) != set(comp_df.index):
            return "stale"
    return "fresh" if has_any else "none"


def dispatch_status_detail(n: pypsa.Network) -> dict:
    """
    Richer sibling of ``dispatch_status`` that ALSO reports WHICH component
    classes carry stale dispatch.

    Returns ``{"state": <none|fresh|stale>, "mismatched_classes": [...]}``.
    The state is classified identically to ``dispatch_status`` (stale if any
    axis's dispatch columns diverge from its component index, else fresh if any
    dispatch exists, else none); ``mismatched_classes`` lists every such stale
    axis (not just the first). The plain string-returning ``dispatch_status`` is
    left untouched so its existing callers are unaffected — this is the shape the
    chat tool's schema promises.
    """
    has_any = False
    mismatched: list[str] = []
    for comp, t_attr, col_attr in _DISPATCH_AXES:
        comp_df = getattr(n, comp)
        t_df = getattr(getattr(n, t_attr), col_attr)
        if comp_df.empty or t_df.empty:
            continue
        has_any = True
        if set(t_df.columns) != set(comp_df.index):
            mismatched.append(comp)
    state = "stale" if mismatched else ("fresh" if has_any else "none")
    return {"state": state, "mismatched_classes": mismatched}


def has_fresh_dispatch(n: pypsa.Network) -> bool:
    """Convenience: True when status is exactly 'fresh'."""
    return dispatch_status(n) == "fresh"


# Component class → list of (t_accessor_attr, _t_df_attr) pairs whose dispatch
# tables should be cleared when the component's topology mutates. Built from
# the PyPSA conventions: each component has a `<component>_t` accessor holding
# its time-varying attributes (p, p0, p1, state_of_charge, marginal_price,
# etc.). Adding or deleting a component leaves these tables sized for the OLD
# component set — a foot-gun that the Phase 4 STALE badge detects but doesn't
# prevent. This map is the prevention layer used by `clear_dispatch`.
_CLEARABLE_T_ATTRS: dict[str, tuple[tuple[str, str], ...]] = {
    # Buses output: active + reactive power balance, marginal price, voltage.
    "Bus":         (("buses_t",         "p"),
                    ("buses_t",         "q"),
                    ("buses_t",         "marginal_price"),
                    ("buses_t",         "v_mag_pu"),
                    ("buses_t",         "v_ang")),
    # Generators output: dispatch + UC binary indicators + ramp/p_set dual
    # vars. UC outputs (`status`, `start_up`, `shut_down`) are written by
    # committable-mode runs and would carry stale values across topology
    # mutations.
    "Generator":   (("generators_t",    "p"),
                    ("generators_t",    "q"),
                    ("generators_t",    "status"),
                    ("generators_t",    "start_up"),
                    ("generators_t",    "shut_down"),
                    ("generators_t",    "mu_upper"),
                    ("generators_t",    "mu_lower"),
                    ("generators_t",    "mu_p_set"),
                    ("generators_t",    "mu_ramp_limit_up"),
                    ("generators_t",    "mu_ramp_limit_down")),
    "Line":        (("lines_t",         "p0"),
                    ("lines_t",         "p1"),
                    ("lines_t",         "q0"),
                    ("lines_t",         "q1"),
                    ("lines_t",         "mu_upper"),
                    ("lines_t",         "mu_lower")),
    # Link output: directional flows + UC indicators + dual vars. p2/p3 are
    # multi-bus link outputs (3-bus / 4-bus links). PyPSA exposes p4..pN for
    # higher-port links — getattr returns None for missing axes so we don't
    # need to enumerate every possible port.
    "Link":        (("links_t",         "p0"),
                    ("links_t",         "p1"),
                    ("links_t",         "p2"),
                    ("links_t",         "p3"),
                    ("links_t",         "status"),
                    ("links_t",         "start_up"),
                    ("links_t",         "shut_down"),
                    ("links_t",         "mu_upper"),
                    ("links_t",         "mu_lower"),
                    ("links_t",         "mu_p_set"),
                    ("links_t",         "mu_ramp_limit_up"),
                    ("links_t",         "mu_ramp_limit_down")),
    "Load":        (("loads_t",         "p"),
                    ("loads_t",         "q")),
    # StorageUnit output: power + SoC + spill (hydro reservoir overflow) +
    # dual vars including the SoC target binding constraint.
    "StorageUnit": (("storage_units_t", "p"),
                    ("storage_units_t", "q"),
                    ("storage_units_t", "p_dispatch"),
                    ("storage_units_t", "p_store"),
                    ("storage_units_t", "state_of_charge"),
                    ("storage_units_t", "spill"),
                    ("storage_units_t", "mu_upper"),
                    ("storage_units_t", "mu_lower"),
                    ("storage_units_t", "mu_energy_balance"),
                    ("storage_units_t", "mu_state_of_charge_set")),
    # Store output: power + energy state + reactive + dual vars.
    "Store":       (("stores_t",        "p"),
                    ("stores_t",        "q"),
                    ("stores_t",        "e"),
                    ("stores_t",        "mu_upper"),
                    ("stores_t",        "mu_lower"),
                    ("stores_t",        "mu_energy_balance")),
    "Transformer": (("transformers_t",  "p0"),
                    ("transformers_t",  "p1"),
                    ("transformers_t",  "q0"),
                    ("transformers_t",  "q1"),
                    ("transformers_t",  "mu_upper"),
                    ("transformers_t",  "mu_lower")),
    # ShuntImpedance has output p/q just like Loads. Easy to miss because
    # the GUI doesn't expose shunts via creation UI, but they exist in
    # imported networks and can carry stale dispatch.
    "ShuntImpedance": (("shunt_impedances_t", "p"),
                       ("shunt_impedances_t", "q")),
}


def affects_dispatch(component_class: str) -> bool:
    """
    Return True iff a topology mutation on this component class can
    invalidate solver dispatch. Excludes ``Carrier`` (no `_t` tables —
    a carrier's CO₂ value can affect the LP but only as a parameter, not as
    a component column-set). ``GlobalConstraint`` is also excluded here
    because constraints are gates on the LP, not dispatch-bearing components;
    the undo middleware handles invalidation for those endpoints separately.
    """
    return component_class in _CLEARABLE_T_ATTRS


def clear_dispatch(n: pypsa.Network) -> bool:
    """
    Empty every solver-output `_t` DataFrame on ``n``. Returns True iff
    anything was cleared.

    Why "clear everything" rather than "clear only the affected axis":
    PyPSA's solver populates a web of inter-dependent `_t` tables — clearing
    only `generators_t.p` after a generator add would leave a stale
    `buses_t.marginal_price` whose values were computed against the old
    generator set. Easier to nuke the lot and force a re-solve than to
    surgically maintain consistency.

    Note on `n.is_solved`: PyPSA exposes this as a read-only @property on
    modern versions (≥0.35) — backed by `n._objective is not None`. We
    don't try to set it directly (would AttributeError). The `_dispatch_ready`
    gate in routers/simulation.py also checks `dispatch_status(n) == 'fresh'`,
    so once the `_t` frames are emptied here, the gate flips to 'none' and
    Results endpoints correctly return 204 — regardless of the `is_solved`
    flag's value.

    Thread-safety: this function is NOT atomic. Callers MUST hold
    `PyPSAService.get_lock()` for the duration. All current call sites
    (the undo middleware in `main.py` + CRUD helpers in `routers/network.py`)
    satisfy this.
    """
    if n is None:  # defensive — `getattr(None, ...)` would crash
        return False
    cleared = False
    # Iterate every dispatch-bearing component and zero its `_t` frames.
    # The DataFrames are shape (snapshots, components); we replace with the
    # empty (snapshots, 0)-shaped frame PyPSA expects post-reset.
    for _comp, axes in _CLEARABLE_T_ATTRS.items():
        for t_attr, df_attr in axes:
            t_accessor = getattr(n, t_attr, None)
            if t_accessor is None:
                continue
            df = getattr(t_accessor, df_attr, None)
            if df is None or df.empty:
                continue
            # Replace with an empty DataFrame of the same row-index (snapshots)
            # so consumers that read `n.<comp>_t.<attr>.index` still see the
            # network's snapshot axis, not an empty index. Preserve column.name
            # = "name" to match PyPSA's stock empty-`_t` shape (per CLAUDE.md's
            # "xarray `dim_0`" entry — axis-name loss has caused LP errors
            # before).
            setattr(
                t_accessor, df_attr,
                pd.DataFrame(
                    index=df.index,
                    columns=pd.Index([], name="name"),
                    dtype=float,
                ),
            )
            cleared = True
    return cleared
