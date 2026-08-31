"""Top-k snapshot selection over the metrics table.

Selection is a UNION of per-criterion extremes, not a single composite score.
A weighted score would need weights, and any weights chosen would be an
unfalsifiable modelling assumption sitting between the data and the study; a
union needs only the claim that each criterion is worth studying on its own.
The cost is that the selection size is not k but somewhere in [k, 4k] — hours
extreme in several criteria are selected once and carry several reasons.

The `reasons` list is what makes a selected snapshot defensible in the report:
each entry names the criterion that pulled the hour in, so "why is hour 4 831
in the study set" has an answer that is a lookup rather than a re-derivation.

Engine cage: pandas/numpy only, same as `metrics`.
"""
import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError

# (reason, metrics column, direction). The reason string is deliberately
# derived from the column it ranks, so the report appendix can be read without
# a second table mapping labels to definitions.
#
# min-inertia ranks on `inertia_excl_equiv_mws`, NOT `inertia_mws`: the
# aggregated interconnection equivalent contributes a near-constant ~50 000
# MW*s to every hour, which compresses the spread between hours to a few
# percent and lets an hour with a full conventional fleet outrank a genuinely
# thin one. Ranking on the equivalent-excluded column is a controller ruling
# recorded in the increment-2 ledger; `inertia_mws` stays in the metrics table
# as the absolute figure to quote.
_RANKING = (
    ("min_inertia_excl_equiv_mws", "inertia_excl_equiv_mws", "min"),
    ("max_ibr_share", "ibr_share", "max"),
    ("max_load_mw", "load_mw", "max"),
    ("max_import_mw", "import_mw", "max"),
)

CRITERIA = tuple(reason for reason, _col, _dir in _RANKING)
RANKED_COLUMNS = tuple(col for _reason, col, _dir in _RANKING)

SELECTION_COLUMNS = ("hour", "reasons")


def select_snapshots(metrics: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Select the union of the k most extreme hours under each criterion.

    Criteria, in the canonical order used for the `reasons` list:

    ``min_inertia_excl_equiv_mws``
        The k LOWEST `inertia_excl_equiv_mws` hours — the thinnest system
        strength once the aggregated interconnection equivalent is set aside.
    ``max_ibr_share``
        The k HIGHEST `ibr_share` hours — the most inverter-dominated.
    ``max_load_mw``
        The k HIGHEST `load_mw` hours — peak demand.
    ``max_import_mw``
        The k HIGHEST `import_mw` hours — greatest reliance on the external
        connection.

    Parameters
    ----------
    metrics
        The table from ``snapshot_metrics``, indexed by hour.
    k
        Snapshots per criterion, at least 1. If `k` exceeds the number of
        hours available, every hour is selected under that criterion; no
        error, and the result is simply the whole table with full reasons.

    Returns
    -------
    DataFrame with a fresh RangeIndex and columns `hour` (int64, ascending,
    unique) and `reasons` (list[str], non-empty, in the canonical criterion
    order above). An hour extreme under several criteria appears ONCE, with
    one entry per criterion that selected it — so ``len(result)`` lies between
    k and 4k, and the caller must not assume it equals either.

    Ties are broken by the earliest hour: the ranking sort is stable over an
    hour-ascending table, so two hours with identical metric values are taken
    in chronological order. Determinism matters more than the choice itself —
    a report that reruns must select the same snapshots.

    Raises
    ------
    ContractError
        If `k` is not a positive integer, `metrics` is empty, or `metrics` is
        missing a ranked column.
    """
    if isinstance(k, bool) or not isinstance(k, (int, np.integer)) or int(k) < 1:
        raise ContractError(f"k must be a positive integer, got {k!r}")
    missing = [c for c in RANKED_COLUMNS if c not in metrics.columns]
    if missing:
        raise ContractError(f"metrics missing ranked columns: {missing}")
    if len(metrics) == 0:
        raise ContractError("metrics table is empty; nothing to select")

    ordered = metrics.sort_index(kind="mergesort")
    reasons: dict = {}
    for reason, col, direction in _RANKING:
        # mergesort is stable, and `ordered` is hour-ascending, so ties resolve
        # to the earliest hour in both directions.
        ranked = ordered[col].sort_values(
            ascending=(direction == "min"), kind="mergesort"
        )
        for hour in ranked.index[: int(k)]:
            reasons.setdefault(hour, []).append(reason)

    hours = sorted(reasons)
    return pd.DataFrame(
        {
            "hour": pd.Series(hours, dtype="int64"),
            "reasons": [reasons[h] for h in hours],
        }
    )


def validate_selection(selection: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Contract check on a selection table, returning it unchanged.

    Enforces that the selection is non-empty, that every `hour` appears in the
    metrics index exactly once, and that every `reasons` entry is a non-empty
    list drawn from ``CRITERIA``. The reason vocabulary is checked as an
    explicit allowlist rather than a shape test, because a reason string is
    quoted into the report: a typo would be published as a criterion.

    Raises
    ------
    ContractError
        On any of the above.
    """
    missing = [c for c in SELECTION_COLUMNS if c not in selection.columns]
    if missing:
        raise ContractError(f"selection missing columns: {missing}")
    if len(selection) == 0:
        raise ContractError("selection is empty")

    dup = selection.duplicated(subset=["hour"])
    if dup.any():
        raise ContractError(
            f"selection has duplicate hours: {sorted(set(selection.loc[dup, 'hour']))}"
        )

    known_hours = set(metrics.index.tolist())
    stray = sorted(h for h in selection["hour"] if h not in known_hours)
    if stray:
        raise ContractError(f"selection hours not in the metrics index: {stray}")

    for hour, reasons in zip(selection["hour"], selection["reasons"]):
        if not isinstance(reasons, (list, tuple)):
            raise ContractError(
                f"selection hour {hour} has non-list reasons: {reasons!r}"
            )
        if len(reasons) == 0:
            raise ContractError(f"selection hour {hour} has empty reasons")
        unknown = [r for r in reasons if r not in CRITERIA]
        if unknown:
            raise ContractError(
                f"selection hour {hour} has unknown reasons {unknown}; "
                f"allowed {list(CRITERIA)}"
            )
    return selection
