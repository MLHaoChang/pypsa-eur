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
import re

import numpy as np
import pandas as pd

from gridspine.handoff.dyr_writer import _machines
from gridspine.handoff.raw_writer import _bus_numbers
from gridspine.schema.contingency import COMPOUND_SEPARATORS, validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import branch_contingencies

ELEMENT_SEP = "|"

COLUMNS = [
    "contingency_id", "kind", "order", "element_ids",
    # branch (order 1) identity — names, numbers, pandapower element type
    "from_bus", "to_bus", "ckt", "from_bus_number", "to_bus_number", "element_type",
    # unit identity — the RAW's (I, ID); a bus section fills bus and bus_number only
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
        if r.kind == "branch":
            # an N-2 pair carries its two branch ids in element_ids only
            f, t = getattr(r, "from_bus", None), getattr(r, "to_bus", None)
            if int(r.order) == 1 and isinstance(f, str) and isinstance(t, str):
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
        elif r.kind == "bus_section":
            bus = r.element_ids[0]
            if bus not in nums:
                raise ContractError(f"contingency {r.contingency_id}: bus {bus} is not a bus on the net")
            row.update(bus=bus, bus_number=float(nums[bus]))
        elif r.kind in COMPOUND_SEPARATORS:
            pass  # N-M-1 / N-1-1: the two branch ids in element_ids, as for an N-2 pair
        else:
            raise ContractError(f"contingency {r.contingency_id}: kind {r.kind!r} has no contingencies.csv form")
        rows.append(row)

    out = pd.DataFrame(rows, columns=COLUMNS)
    for col in _FLOAT_COLS:
        out[col] = out[col].astype("float64")
    out.to_csv(path, index=False)
    return out


# --------------------------------------------------------------------------
# PSS/E .con / .sub / .mon (GC-04 PR4)
# --------------------------------------------------------------------------

#: The study subsystem every .con/.mon refers to.
SUBSYSTEM = "STUDY"
#: Kinds a .con block cannot carry: an N-1-1 / N-M-1 is a SEQUENCE (an outage,
#: re-dispatch, then a second outage), which one PSS/E contingency block does
#: not express. They are returned as not written, never silently dropped.
CON_NOT_WRITTEN = tuple(sorted(COMPOUND_SEPARATORS))


def _con_label(cid: str) -> str:
    """The block label: the contingency id itself, which must be one token of
    [A-Za-z0-9_-], at most 32 characters, so GC-03's reader maps it back to
    the same id."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid):
        raise ContractError(f"contingency {cid!r}: not a .con label ([A-Za-z0-9_-], 1-32 characters)")
    return cid


def write_con(contingency_set: pd.DataFrame, net, path) -> list:
    """Write ``path`` as a PSS/E contingency description file; return the ids
    NOT written (the compound kinds, ``CON_NOT_WRITTEN``), for the manifest.

    Identity is the RAW's: a branch is ``OPEN BRANCH FROM BUS i TO BUS j
    CIRCUIT 'c '`` with the RAW's numbers and CKT (``branch_contingencies``,
    the table GC-03's reader matches against); a unit is ``REMOVE MACHINE
    'id' FROM BUS n`` with the RAW's per-bus machine ID; a bus section is
    ``DISCONNECT BUS n``. An element not on the network is refused and
    nothing is written (S2). Labels are the contingency ids themselves.
    """
    cset = validate_contingency_set(contingency_set)
    nums = _bus_numbers(net)
    branch = {}
    for r in branch_contingencies(net).itertuples(index=False):
        branch[r.element_ids[0]] = (nums[r.from_bus], nums[r.to_bus], str(r.ckt))
    machine = {uid: (num, mid) for uid, num, mid, _mb, _sync in _machines(net)}
    lines, skipped = [], []
    for r in cset.itertuples(index=False):
        if r.kind in CON_NOT_WRITTEN:
            skipped.append(r.contingency_id)
            continue
        body = []
        for el in r.element_ids:
            if r.kind == "branch":
                if el not in branch:
                    raise ContractError(f"contingency {r.contingency_id}: branch {el} is not on the network")
                i, j, ckt = branch[el]
                body.append(f"   OPEN BRANCH FROM BUS {i} TO BUS {j} CIRCUIT '{ckt:<2s}'")
            elif r.kind == "unit":
                if el not in machine:
                    raise ContractError(f"contingency {r.contingency_id}: unit {el} is not a machine on the network")
                num, mid = machine[el]
                body.append(f"   REMOVE MACHINE '{mid:<2s}' FROM BUS {num}")
            elif r.kind == "bus_section":
                if el not in nums:
                    raise ContractError(f"contingency {r.contingency_id}: bus {el} is not a bus on the network")
                body.append(f"   DISCONNECT BUS {nums[el]}")
            else:
                raise ContractError(f"contingency {r.contingency_id}: kind {r.kind!r} has no .con form")
        lines += [f"CONTINGENCY {_con_label(r.contingency_id)}", *body, "END"]
    lines.append("END")
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return skipped


def write_sub(net, path) -> None:
    """The study subsystem: every bus of the network, by its kV range."""
    kv = net.bus["vn_kv"].astype(float)
    if kv.empty or not np.isfinite(kv).all() or not (kv > 0).all():
        raise ContractError("the .sub kV range needs at least one bus, every vn_kv finite and > 0")
    text = f"SUBSYSTEM '{SUBSYSTEM}'\n   KVRANGE {float(kv.min()):.3f} {float(kv.max()):.3f}\nEND\nEND\n"
    with open(path, "w", newline="\n") as f:
        f.write(text)


def write_mon(path, v_min_pu, v_max_pu) -> None:
    """Monitor every branch and every bus voltage of the study subsystem
    against ``[v_min_pu, v_max_pu]``, the caller's planning band (no default:
    the band is the study's, not this writer's)."""
    for v in (v_min_pu, v_max_pu):
        bad_type = isinstance(v, bool) or not isinstance(v, (int, float, np.floating))
        if bad_type or not (np.isfinite(v) and v > 0):
            raise ContractError(f"monitor voltage band must be finite and > 0, got {v}")
    if not v_min_pu < v_max_pu:
        raise ContractError(f"monitor band needs v_min < v_max, got {v_min_pu}, {v_max_pu}")
    text = (
        f"MONITOR BRANCHES IN SUBSYSTEM '{SUBSYSTEM}'\n"
        f"MONITOR VOLTAGE RANGE SUBSYSTEM '{SUBSYSTEM}' {v_min_pu:.3f} {v_max_pu:.3f}\n"
        "END\n"
    )
    with open(path, "w", newline="\n") as f:
        f.write(text)
