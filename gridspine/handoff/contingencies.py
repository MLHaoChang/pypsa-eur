"""contingencies.csv — the outage set in the form a dynamics engineer reads.

Keyed on the SAME identity as everything else in the bundle: a branch is
``(from_bus, to_bus, ckt)`` exactly as the RAW writer stamps it, a unit is the
RAW's ``(bus, machine ID)`` — so a reader can walk this file and the .raw side
by side without a translation table. Bus NUMBERS are added beside the names
for the PSS/E user, from the same assignment ``write_raw`` uses; machine IDs
come from the same per-bus counter the .dyr writer walks, so ``G_BUS_33`` and
``W_BUS_33`` on one bus are ``'1'`` and ``'2'`` here, in the .raw and in the
.dyr alike.

``element_ids`` is a list in memory and CSV has no list type: joined on ``|``,
which is outside the canonical id charset, so the split is lossless — the same
convention ``selected.csv`` uses for ``reasons``.

Nothing here imports an engine; the net is read for its tables only.
"""
import numpy as np
import pandas as pd

from gridspine.handoff.dyr_writer import _machines
from gridspine.handoff.raw_writer import _bus_numbers
from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError

ELEMENT_SEP = "|"

COLUMNS = [
    "contingency_id", "kind", "order", "element_ids",
    # branch (order 1) identity — names, numbers, pandapower element type
    "from_bus", "to_bus", "ckt", "from_bus_number", "to_bus_number", "element_type",
    # unit identity — the RAW's (I, ID)
    "bus", "bus_number", "machine_id",
]
_FLOAT_COLS = ("from_bus_number", "to_bus_number", "bus_number")


def write_contingencies(contingency_set: pd.DataFrame, net, path) -> pd.DataFrame:
    cset = validate_contingency_set(contingency_set)
    nums = _bus_numbers(net)
    name_of_num = {n: name for name, n in nums.items()}
    machine = {uid: (num, mid) for uid, num, mid, _mb, _sync in _machines(net)}

    rows = []
    for r in cset.itertuples(index=False):
        row = {c: np.nan for c in COLUMNS}
        row.update(
            contingency_id=r.contingency_id, kind=r.kind, order=int(r.order),
            element_ids=ELEMENT_SEP.join(r.element_ids),
        )
        if r.kind == "branch" and int(r.order) == 1:
            f, t = getattr(r, "from_bus", None), getattr(r, "to_bus", None)
            if isinstance(f, str) and isinstance(t, str):
                row.update(
                    from_bus=f, to_bus=t, ckt=str(r.ckt),
                    from_bus_number=float(nums[f]), to_bus_number=float(nums[t]),
                    element_type=r.element_type,
                )
        elif r.kind == "unit":
            uid = r.element_ids[0]
            if uid not in machine:
                raise ContractError(
                    f"contingency {r.contingency_id}: unit {uid} is not a machine on the net"
                )
            num, mid = machine[uid]
            row.update(bus=name_of_num[num], bus_number=float(num), machine_id=mid)
        rows.append(row)

    out = pd.DataFrame(rows, columns=COLUMNS)
    for col in _FLOAT_COLS:
        out[col] = out[col].astype("float64")
    out.to_csv(path, index=False)
    return out
