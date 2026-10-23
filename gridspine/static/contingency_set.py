"""Stage 2 input: the set of outages to study, enumerated from the net.

Identity is the whole job here. A branch contingency names a branch, and a
branch is identified by the SAME ``(from_bus, to_bus, ckt)`` triple that the
RAW writer stamps on its records and that ``LFResult.branch_flow`` carries —
so this module calls ``loadflow.branch_keys`` rather than reproducing the
counter a third time, and ``tests/gridspine/test_contingency_set.py`` locks the
result against the .raw the writer actually emits.

Ids are derived from names, never from pandapower indexes, so they are stable
across runs and platforms:

* N-1 branch: ``"{from_bus}-{to_bus}-{ckt}"`` — the record's own from/to order
  (the writer's I, J), not the sorted pair (that only keys the counter).
* N-1 unit:   the canonical ``unit_id``.
* N-2 pair:   ``"{a}--{b}"`` with ``a < b`` lexically, so ``(a, b)`` and
  ``(b, a)`` cannot both appear.
* Bus section: the bus name, bare as a unit id is. A unit and a bus that
  share a name collide, and the duplicate check names both kinds.

A hyphen is inside the canonical charset, so an id is a KEY and not a parse
target: the triple travels alongside as columns (``from_bus``, ``to_bus``,
``ckt``) with the pandapower handle (``element_type``, ``element_index``)
that stage 2 needs to apply the outage. Uniqueness is asserted, so a client
grid whose bus names collide under this scheme fails closed instead of
silently merging two outages.

Out-of-service branches are EXCLUDED from the set but NOT from the keying:
the writer still emits a dead branch (STAT=0) and its CKT counter still
counts it, so filtering has to happen after the keys are assigned or every
later circuit on that bus pair shifts by one.
"""
from itertools import combinations

import pandas as pd

from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.loadflow import branch_keys

UNIT_KINDS = frozenset({"gen", "ext_grid", "res"})
_OUTAGEABLE_UNIT_KINDS = frozenset({"gen", "res"})

#: LEDGER — why the slack is not a contingency.
EXT_GRID_EXCLUSION_LEDGER = (
    "unit contingencies exclude kind=ext_grid: outaging the slack leaves the "
    "pocket without a reference bus, which is an islanding study with its own "
    "frequency question, not an N-1 contingency (assumed scope)",
)

#: LEDGER — what a bus-section contingency is.
BUS_SECTION_LEDGER = (
    "a bus-section contingency is the loss of one model bus: every branch, "
    "unit, load and shunt on it goes with it. One model bus is one bus "
    "section, so a split busbar must be modelled as separate buses joined by "
    "a coupler to be studied section by section (assumed scope)",
    "bus-section contingencies exclude the ext_grid slack bus, for the reason "
    "unit contingencies exclude kind=ext_grid",
)

_SET_COLUMNS = ["contingency_id", "kind", "element_ids", "order"]


def _branch_element_id(from_bus: str, to_bus: str, ckt: str) -> str:
    return f"{from_bus}-{to_bus}-{ckt}"


def branch_contingencies(net) -> pd.DataFrame:
    """One N-1 row per IN-SERVICE line and transformer, keyed as the RAW is."""
    keys = branch_keys(net)  # every branch, dead ones included — see module docstring
    in_service = []
    for etype, eidx in zip(keys["element_type"], keys["element_index"]):
        table = net.line if etype == "line" else net.trafo
        in_service.append(bool(table.at[eidx, "in_service"]))
    keys = keys[pd.Series(in_service, index=keys.index)].reset_index(drop=True)

    element_id = [
        _branch_element_id(f, t, c)
        for f, t, c in zip(keys["from_bus"], keys["to_bus"], keys["ckt"])
    ]
    out = pd.DataFrame({
        "contingency_id": element_id,
        "kind": "branch",
        "element_ids": [[e] for e in element_id],
        "order": 1,
        "from_bus": keys["from_bus"].values,
        "to_bus": keys["to_bus"].values,
        "ckt": keys["ckt"].values,
        "element_type": keys["element_type"].values,
        "element_index": keys["element_index"].values,
    })
    out = out.sort_values("contingency_id", kind="mergesort").reset_index(drop=True)
    return validate_contingency_set(out)


def unit_contingencies(registry: pd.DataFrame) -> pd.DataFrame:
    """One N-1 row per gen and res unit. ext_grid is excluded — ledgered above."""
    unknown = sorted(set(registry["kind"]) - UNIT_KINDS)
    if unknown:
        raise ContractError(
            f"registry has unit kind(s) outside {sorted(UNIT_KINDS)}: {unknown}"
        )
    units = registry[registry["kind"].isin(_OUTAGEABLE_UNIT_KINDS)]
    out = pd.DataFrame({
        "contingency_id": list(units.index),
        "kind": "unit",
        "element_ids": [[u] for u in units.index],
        "order": 1,
        "bus": units["bus"].values,
    })
    out = out.sort_values("contingency_id", kind="mergesort").reset_index(drop=True)
    return validate_contingency_set(out)


def n2_candidates(n1: pd.DataFrame) -> pd.DataFrame:
    """Every unordered pair of an N-1 BRANCH set. C(46, 2) = 1035 on case39,
    which is why stage 2 prunes these with LODF rather than solving them all."""
    n1 = validate_contingency_set(n1)
    if (n1["order"] != 1).any():
        raise ContractError("n2_candidates takes an N-1 set; got rows with order != 1")
    if (n1["kind"] != "branch").any():
        raise ContractError("n2_candidates takes a branch set; got non-branch rows")
    ids = sorted(n1["contingency_id"])
    pairs = list(combinations(ids, 2))  # ids sorted, so every pair has a < b
    out = pd.DataFrame({
        "contingency_id": [f"{a}--{b}" for a, b in pairs],
        "kind": "branch",
        "element_ids": [[a, b] for a, b in pairs],
        "order": 2,
    })
    out = out.sort_values("contingency_id", kind="mergesort").reset_index(drop=True)
    return validate_contingency_set(out)


def bus_section_contingencies(net, buses=None) -> pd.DataFrame:
    """One row per bus section (``BUS_SECTION_LEDGER``): every in-service bus
    but the slack's, or the named ``buses``. A named bus that is out of
    service is skipped; an unknown or slack bus is refused."""
    names = net.bus["name"].astype(str)
    if names.duplicated().any():
        raise ContractError(f"bus names on the net are not unique: {sorted(set(names[names.duplicated()]))}")
    slack = {names.at[b] for b in net.ext_grid["bus"]}
    on = {names.at[b] for b in net.bus.index if bool(net.bus.at[b, "in_service"])}
    if buses is None:
        picked = sorted(on - slack)
    else:
        buses = [str(b) for b in buses]
        unknown = sorted(set(buses) - set(names))
        if unknown:
            raise ContractError(f"bus section(s) not on the net: {unknown}")
        at_slack = sorted(set(buses) & slack)
        if at_slack:
            raise ContractError(
                f"bus section(s) {at_slack} hold the ext_grid slack: {EXT_GRID_EXCLUSION_LEDGER[0]}"
            )
        picked = sorted(set(buses) & on)
    out = pd.DataFrame({
        "contingency_id": picked,
        "kind": "bus_section",
        "element_ids": [[b] for b in picked],
        "order": 1,
        "bus": picked,
    })
    return validate_contingency_set(out)
