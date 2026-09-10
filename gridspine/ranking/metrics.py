"""Per-snapshot system metrics: the four numbers that decide which hours of a
year are worth studying in detail.

The docstrings in this module are the normative definitions. They are quoted
verbatim into the client report's methodology appendix, so they state what is
summed, over which rows, and what happens at the edges — a definition that is
only true "in the usual case" is a defect here.

Engine cage
-----------
Nothing in ``gridspine.ranking`` imports pypsa or pandapower. The module reads
the stage-boundary artifacts (dispatch table, loads table, unit-parameter
template, unit registry) with pandas and numpy only, which is what makes the
metrics reproducible from the CSVs a client is handed rather than from a live
simulation object. ``tests/gridspine/test_ranking.py`` pins the cage with an
import allowlist parsed from this file's AST.
"""
import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch

# An "aggregated equivalent" is a single machine standing in for a whole
# neighbouring system: in the IEEE 39-bus case, G_BUS_39 (h_s = 500 s) is the
# New-York interconnection reduced to one generator, not a real power station.
# Its inertia contribution is ~50 000 MW*s, two orders of magnitude above any
# real unit in the pocket, so it is a near-constant floor under every hour and
# it mutes the commitment-driven variation that the inertia metric exists to
# surface. Any unit whose inertia constant is at or above this threshold is
# treated as such an equivalent and excluded from `inertia_excl_equiv_mws`.
#
# 100 s is a deliberate no-man's-land: the largest real synchronous machines
# top out near 10 s on their own rating (the case39 fleet spans 24-43 s on the
# 100 MVA system base), and aggregated equivalents are built from hundreds of
# machines. Nothing sits between. Raising or lowering this is a modelling
# decision that belongs in the assumptions ledger, not a tuning knob.
AGGREGATED_EQUIVALENT_H_S_THRESHOLD_S = 100.0

METRIC_COLUMNS = (
    "load_mw",
    "import_mw",
    "inertia_mws",
    "inertia_excl_equiv_mws",
    "ibr_share",
)

REGISTRY_KINDS = frozenset({"gen", "ext_grid", "res"})

_LOADS_REQUIRED = ("bus", "hour", "p_mw")


def _checked_loads(loads: pd.DataFrame) -> pd.DataFrame:
    """Minimal structural check on the loads artifact.

    The authoritative contract is ``schema.dispatch.validate_loads``, applied
    by whoever produces the table; ranking deliberately does not import it, so
    that a client can recompute these metrics from a supplied CSV without
    pulling the producer stack in. What is checked here is only what the
    arithmetic below actually depends on: the three columns it reads, finite
    real loads, and one row per (bus, hour) so that a duplicated bus cannot
    double-count into `load_mw`.
    """
    missing = [c for c in _LOADS_REQUIRED if c not in loads.columns]
    if missing:
        raise ContractError(f"loads table missing columns: {missing}")
    out = loads.copy()
    if out["bus"].isna().any():
        raise ContractError("loads table has null bus")
    p = pd.to_numeric(out["p_mw"], errors="coerce")
    if not np.isfinite(p).all():
        raise ContractError("loads table has non-finite p_mw")
    out["p_mw"] = p.astype("float64")
    hour = pd.to_numeric(out["hour"], errors="coerce")
    if hour.isna().any() or (hour % 1 != 0).any():
        raise ContractError(f"loads table hour must be integral, got {out['hour'].tolist()}")
    out["hour"] = hour.astype("int64")
    dup = out.duplicated(subset=["bus", "hour"])
    if dup.any():
        raise ContractError(
            f"loads table has duplicate (bus, hour) rows: {out.loc[dup, 'bus'].tolist()}"
        )
    return out


def _kinds_for(dispatch: pd.DataFrame, registry: pd.DataFrame) -> pd.Series:
    if "kind" not in registry.columns:
        raise ContractError("registry missing 'kind' column")
    bad_kind = sorted(set(registry["kind"].dropna()) - REGISTRY_KINDS)
    if bad_kind:
        raise ContractError(
            f"registry has unknown kind values {bad_kind}; allowed {sorted(REGISTRY_KINDS)}"
        )
    kinds = dispatch["unit_id"].map(registry["kind"])
    unknown = sorted(set(dispatch.loc[kinds.isna(), "unit_id"]))
    if unknown:
        # Fail closed. A dispatching unit with no registry row has no kind, so
        # it would be silently excluded from both `import_mw` and the RES
        # numerator of `ibr_share` while still inflating that share's
        # denominator -- a wrong number rather than a missing one.
        raise ContractError(f"dispatch units not in the registry: {unknown}")
    return kinds


def _sum_by_hour(values: pd.Series, hours: pd.Series, index: pd.Index) -> pd.Series:
    return values.groupby(hours).sum().reindex(index, fill_value=0.0).astype("float64")


def snapshot_metrics(
    dispatch: pd.DataFrame,
    loads: pd.DataFrame,
    unit_params: pd.DataFrame,
    registry: pd.DataFrame,
) -> pd.DataFrame:
    """Reduce a dispatch year to one row of system metrics per hour.

    Parameters
    ----------
    dispatch
        Stage-boundary dispatch table (`unit_id`, `hour`, `p_mw`, `q_mvar`,
        `status`), revalidated here via ``validate_dispatch``.
    loads
        Loads table (`bus`, `hour`, `p_mw`, `q_mvar`), one row per bus-hour.
    unit_params
        Unit-parameter template indexed by `unit_id` with `h_s` [s],
        `mbase_mva` [MVA] and `include_in_inertia` [bool]. Units may be absent
        (see `inertia_mws`).
    registry
        Unit registry indexed by `unit_id` with `kind` in {gen, ext_grid, res}.

    Returns
    -------
    DataFrame indexed by `hour` (int64, ascending, index name ``"hour"``) with
    columns, in order:

    ``load_mw``
        Total connected demand: the sum of `p_mw` over every row of the loads
        table in that hour. Reported as positive consumption.

    ``import_mw``
        Net power entering the modelled pocket from outside it: the sum of
        `p_mw` over dispatch rows whose registry `kind` is ``ext_grid``.
        Signed — a negative value is a net export. Not clipped, because the
        sign is the physically meaningful part.

    ``inertia_mws``
        Total stored rotational kinetic energy at nominal speed, in MW*s:

            sum over units of  h_s * mbase_mva

        restricted to units that are BOTH online in that hour (`status == 1`)
        AND flagged `include_in_inertia` in the unit-parameter template. A unit
        with no row in `unit_params` contributes exactly 0 (the join is a left
        join on the dispatch side, with `h_s`, `mbase_mva` and the flag filled
        with 0/0/False). That absence is the normal case for inverter-based
        resources — wind and solar rows are in the registry and the dispatch
        table but carry no `h_s`, and contributing zero is the correct physics
        for them, not a missing-data fallback. It is also why a hour can carry
        several hundred MW of RES output and still show falling inertia, which
        is the whole point of ranking on this metric.

    ``inertia_excl_equiv_mws``
        The same sum with aggregated-equivalent machines removed: units whose
        `h_s` is at or above ``AGGREGATED_EQUIVALENT_H_S_THRESHOLD_S``
        (100 s) are dropped. In the IEEE 39-bus case this excludes exactly
        G_BUS_39, the reduced representation of the neighbouring
        interconnection, whose ~50 000 MW*s would otherwise sit as a constant
        floor under every hour and compress the variation between them.
        `inertia_mws` remains the figure to quote for absolute system inertia;
        `inertia_excl_equiv_mws` is the figure to RANK on, and is what
        ``select_snapshots`` uses. Both are reported so a reader can see the
        equivalent's contribution as the difference.

    ``ibr_share``
        Share of instantaneous active-power output coming from inverter-based
        resources:

            sum(p_mw over kind == "res")  /  sum(p_mw over all units)

        in that hour. Two edge conventions, both deliberate:
        (a) when the denominator is zero or negative — no unit generating, or
        a net-importing pocket whose conventional output is outweighed — the
        share is 0.0, never NaN, so that the hour ranks as "no IBR stress"
        rather than dropping silently out of the ranking;
        (b) the result is clipped into [0, 1]. The unclipped ratio can exceed
        1 when the pocket is exporting hard enough that `ext_grid` p_mw is
        negative; 1.0 ("all of it") is the honest reading of that case.

    Raises
    ------
    ContractError
        If the dispatch table violates its contract, the loads table is
        structurally unusable, a dispatching unit has no registry row, the
        registry carries an unknown `kind`, or the two artifacts do not cover
        the same set of hours.
    """
    d = validate_dispatch(dispatch)
    ld = _checked_loads(loads)

    dispatch_hours = set(d["hour"].unique().tolist())
    load_hours = set(ld["hour"].unique().tolist())
    if dispatch_hours != load_hours:
        # Two artifacts covering different hours are two different runs. Taking
        # the union would report a load with no dispatch (inertia 0, share 0)
        # as a genuine zero-inertia snapshot -- the most extreme value the
        # ranking can see, produced entirely by a bookkeeping mismatch.
        only_d = sorted(dispatch_hours - load_hours)
        only_l = sorted(load_hours - dispatch_hours)
        raise ContractError(
            "dispatch and loads cover different hours: "
            f"{len(only_d)} only in dispatch (e.g. {only_d[:5]}), "
            f"{len(only_l)} only in loads (e.g. {only_l[:5]})"
        )

    index = pd.Index(sorted(dispatch_hours), dtype="int64", name="hour")
    kinds = _kinds_for(d, registry)

    for col in ("h_s", "mbase_mva", "include_in_inertia"):
        if col not in unit_params.columns:
            raise ContractError(f"unit params missing column: {col}")
    if unit_params["include_in_inertia"].isna().any():
        # A null flag is not a "no". `astype(bool)` would read NaN as True and
        # silently count a unit the template declined to declare.
        raise ContractError("unit params has null include_in_inertia")

    # Left join onto the dispatch side: units absent from the template
    # contribute nothing rather than raising. See `inertia_mws` above.
    h_s = pd.to_numeric(d["unit_id"].map(unit_params["h_s"]), errors="coerce").fillna(0.0)
    mbase = pd.to_numeric(
        d["unit_id"].map(unit_params["mbase_mva"]), errors="coerce"
    ).fillna(0.0)
    # Set membership rather than a mapped-then-filled bool column: mapping a
    # bool column over unit ids that are not in it yields object dtype with
    # NaN, and filling that is a deprecated silent downcast in pandas.
    flagged = unit_params.index[unit_params["include_in_inertia"].astype(bool)]
    counted = d["unit_id"].isin(flagged)

    online = d["status"] == 1
    contribution = (h_s * mbase).where(online & counted, 0.0)
    is_equivalent = h_s >= AGGREGATED_EQUIVALENT_H_S_THRESHOLD_S

    res_p = d["p_mw"].where(kinds == "res", 0.0)
    total_p = _sum_by_hour(d["p_mw"], d["hour"], index)
    res_by_hour = _sum_by_hour(res_p, d["hour"], index)
    share = np.divide(
        res_by_hour.to_numpy(),
        total_p.to_numpy(),
        out=np.zeros(len(index), dtype="float64"),
        where=total_p.to_numpy() > 0.0,
    )

    out = pd.DataFrame(
        {
            "load_mw": _sum_by_hour(ld["p_mw"], ld["hour"], index),
            "import_mw": _sum_by_hour(
                d["p_mw"].where(kinds == "ext_grid", 0.0), d["hour"], index
            ),
            "inertia_mws": _sum_by_hour(contribution, d["hour"], index),
            "inertia_excl_equiv_mws": _sum_by_hour(
                contribution.where(~is_equivalent, 0.0), d["hour"], index
            ),
            "ibr_share": pd.Series(np.clip(share, 0.0, 1.0), index=index),
        },
        index=index,
    )
    return out[list(METRIC_COLUMNS)]
