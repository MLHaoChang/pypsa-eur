"""AA-05 S5a (A4.6, AA05-FREEZE-1): the window identity an evidence record stores.

A checked fact names its window: start, end, the number of snapshots in it,
and, per metric, the snapshot weighting that multiplies it. The weight basis
is declared here for every metric in `harness.calculations.DEFINITIONS`. A
test holds the two key sets equal. The raw-data oracles in
tests/assurance_acceptance check one metric per profile module against it.

Tokens: "generators", "objective" or "stores" name a snapshot_weightings
column; "generators*years" is generator weights times the investment period's
years; "none" means no snapshot weighting (peaks, ratings, capacities, counts,
values read from a report); "mixed:<a>+<b>" combines two columns;
"derived" metrics are computed from other metrics only.
"""

import pandas as pd

WEIGHT_BASIS: dict[str, str] = {}  # filled below


def window(index, scope):
    """The record's window: exact boundaries, snapshot count and weight basis.

    `index` is the profile's snapshot index as captured (isoformat strings).
    The scope's end is inclusive, as `calculations._window` reads it: in index
    positions the window is [a, b + 1)."""
    from harness.calculations import requested_metrics

    start = scope.get("start", index[0] if index else None)
    end = scope.get("end", index[-1] if index else None)
    try:
        a = index.index(pd.Timestamp(start).isoformat())
        b = index.index(pd.Timestamp(end).isoformat())
        snapshots = b - a + 1 if b >= a else None
    except (ValueError, TypeError):
        snapshots = None
    return {
        "start": start,
        "end": end,
        "snapshots": snapshots,
        "weight_basis": {m: WEIGHT_BASIS[m] for m in requested_metrics(scope)},
    }
