"""Client-supplied dispatch snapshots (CSV/Excel) -> the stage-1 dispatch table.

The spec's second stage-1 producer, beside `pypsa_nodal`. A client who already
has a dispatch — out of their own market model, their own UC, a spreadsheet from
an operator — should not have to re-solve it in PyPSA to study it, and after this
they do not.

What "same table" means here
----------------------------
Stage 2 onward speaks canonical detailed-grid unit ids and nothing else, so this
module's entire job is to get a client's file in front of `validate_dispatch`
without losing information on the way, and to refuse anything it cannot land on
that contract exactly. It emits the same object `tables_from_network` emits, so
nothing downstream can tell — or needs to tell — which producer ran.

No pypsa import, deliberately: the engine cage confines `pypsa` to
`producers/pypsa_nodal.py`, and an external dispatch has nothing to do with it.
pandas only.

The two directions of the unit check
------------------------------------
Mirrors `tables_from_network`'s check (`pypsa_nodal.py:461`) because the hazard
is identical. A file naming a unit the grid lacks is obvious. A file that merely
OMITS a grid unit is the dangerous direction: it parses, validates, ranks, and
ranks a grid that is not the one being studied, with the omitted machine absent
from every snapshot. Completeness is checked per (unit, hour) for the same
reason — a single missing hour leaves one snapshot quietly wrong.

Demand is required, not assumed
-------------------------------
A snapshot is generation AND demand, and a client dispatch carries only the
generation side. Neither route that exists elsewhere applies: a PyPSA network
brings its own loads (`tables_from_network`), and a generated year synthesises
them from `LOAD_SHAPE` (`dispatch_year`). Pairing a client's generation with
gridspine's synthetic demand would leave the mismatch to the external grid's
slack — the load flow would converge, and its flows and N-1 severities would
describe a grid state that never existed. So a loads file is required and its
absence is a refusal. Either two files, or one Excel workbook with `dispatch`
and `loads` sheets, because a client exporting one file is the common case and
making them split it invites exactly the hand-edit this module's aliases exist
to avoid.

Coercion is the enemy
---------------------
`validate_dispatch` guards the values AS SUPPLIED, before `astype`, precisely
because `astype("int64")` turns a relaxed commitment status of 0.5 into 0 and a
half-committed unit recorded as offline changes the inertia and fault level of
every snapshot it appears in. This module therefore does no numeric cleaning of
its own: it maps column NAMES and hands the values over untouched. Everything
the contract rejects stays rejected.
"""
from pathlib import Path

import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import (
    DISPATCH_COLUMNS,
    LOADS_COLUMNS,
    validate_dispatch,
    validate_loads,
)

#: Client spellings -> contract columns. Lower-cased and stripped of spaces,
#: underscores and bracketed units before lookup, so "P [MW]", "p_mw" and
#: "P(MW)" all arrive at the same key. Exists so a client need not rename
#: columns by hand — an edit to evidence, and the step most likely to be done
#: wrong. Every value MUST be a contract column; a test asserts that, because a
#: typo here would silently drop a column the client believed was read.
EXTERNAL_ALIASES = {
    "unitid": "unit_id",
    "unit": "unit_id",
    "unitname": "unit_id",
    "generator": "unit_id",
    "machine": "unit_id",
    "hour": "hour",
    "h": "hour",
    "timestep": "hour",
    "snapshot": "hour",
    "pmw": "p_mw",
    "p": "p_mw",
    "activepower": "p_mw",
    "qmvar": "q_mvar",
    "q": "q_mvar",
    "reactivepower": "q_mvar",
    "status": "status",
    "committed": "status",
    "commitment": "status",
    "online": "status",
}

#: The same idea for the demand table. `bus` rather than `unit_id`, and no
#: status: demand is not committed.
LOADS_ALIASES = {
    "bus": "bus",
    "busname": "bus",
    "busid": "bus",
    "node": "bus",
    "hour": "hour",
    "h": "hour",
    "timestep": "hour",
    "snapshot": "hour",
    "pmw": "p_mw",
    "p": "p_mw",
    "load": "p_mw",
    "demand": "p_mw",
    "activepower": "p_mw",
    "qmvar": "q_mvar",
    "q": "q_mvar",
    "reactivepower": "q_mvar",
}

#: Sheet names looked for when one workbook carries both tables.
DISPATCH_SHEET = "dispatch"
LOADS_SHEET = "loads"

_CSV_SUFFIXES = {".csv", ".txt"}
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def _normalise(name: object) -> str:
    """A client header reduced to its alias key: case, spaces, underscores and
    any bracketed unit dropped. `"P [MW]"` and `"p_mw"` both become `"pmw"`."""
    text = str(name).strip().lower()
    for cut in ("[", "("):
        if cut in text:
            text = text.split(cut, 1)[0]
    return "".join(ch for ch in text if ch.isalnum())


def _read_frame(path: Path, sheet: str | None = None) -> pd.DataFrame:
    """The file as a frame, or a ContractError. Never a parser traceback: a bad
    client file is a stage failure the UI and copilot render, so it has to
    arrive as the same typed error every other refusal here uses."""
    suffix = path.suffix.lower()
    if suffix in _CSV_SUFFIXES:
        if sheet is not None:
            raise ContractError(
                f"external dispatch: {path.name} is a CSV, so it cannot carry a "
                f"{sheet!r} sheet; supply the two tables as two files"
            )
        reader, kwargs = pd.read_csv, {}
    elif suffix in _EXCEL_SUFFIXES:
        reader, kwargs = pd.read_excel, ({} if sheet is None else {"sheet_name": sheet})
    else:
        raise ContractError(
            f"external dispatch: unsupported file type {path.suffix!r}; "
            f"expected one of {sorted(_CSV_SUFFIXES | _EXCEL_SUFFIXES)}"
        )
    try:
        frame = reader(path, **kwargs)
    except Exception as exc:  # noqa: BLE001 — any reader failure is one refusal
        where = path.name if sheet is None else f"{path.name} sheet {sheet!r}"
        raise ContractError(
            f"external dispatch: {where} could not be read: {exc}"
        ) from exc
    if frame.empty:
        where = path.name if sheet is None else f"{path.name} sheet {sheet!r}"
        raise ContractError(f"external dispatch: {where} has no rows")
    return frame


def _map_columns(frame: pd.DataFrame, filename: str, aliases: dict,
                 contract: dict) -> pd.DataFrame:
    """Client headers -> contract columns, refusing ambiguity rather than
    picking. Two headers mapping to one contract column means one of them would
    be discarded silently, and the client would have no way to know which."""
    mapped: dict[str, list[object]] = {}
    for column in frame.columns:
        target = aliases.get(_normalise(column))
        if target is not None:
            mapped.setdefault(target, []).append(column)

    ambiguous = {t: cols for t, cols in mapped.items() if len(cols) > 1}
    if ambiguous:
        detail = "; ".join(
            f"{t} <- {[str(c) for c in cols]}" for t, cols in sorted(ambiguous.items())
        )
        raise ContractError(
            f"external dispatch: {filename} has more than one column for the "
            f"same field, so reading either would discard the other: {detail}"
        )

    missing = sorted(set(contract) - set(mapped))
    if missing:
        raise ContractError(
            f"external dispatch: {filename} is missing {missing}; "
            f"recognised {sorted(mapped)} from headers "
            f"{[str(c) for c in frame.columns]}"
        )
    # Values pass through untouched — see the module docstring on coercion.
    return pd.DataFrame({target: frame[cols[0]] for target, cols in mapped.items()})


def _check_units(dispatch: pd.DataFrame, registry: pd.DataFrame) -> None:
    """The unit set, both directions, then per-(unit, hour) completeness."""
    supplied = set(dispatch["unit_id"].astype(str))
    expected = set(registry.index.astype(str))
    missing = sorted(expected - supplied)
    unknown = sorted(supplied - expected)
    if missing or unknown:
        raise ContractError(
            "external dispatch does not map to the detailed grid's units: "
            f"missing {missing}, unknown {unknown}"
        )

    hours = sorted(pd.Series(dispatch["hour"]).unique().tolist())
    per_unit = dispatch.groupby(dispatch["unit_id"].astype(str))["hour"].nunique()
    short = sorted(per_unit[per_unit != len(hours)].index.tolist())
    if short:
        raise ContractError(
            f"external dispatch covers {len(hours)} hour(s) but these units are "
            f"not given for every hour: {short}"
        )


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _hours_of(frame: pd.DataFrame) -> list[int]:
    return sorted(int(h) for h in pd.Series(frame["hour"]).unique())


def tables_from_external(dispatch_src, loads_src, registry: pd.DataFrame):
    """`(dispatch, loads, dispatch_source)` from a client's own tables.

    `dispatch_src` is a CSV/Excel dispatch; `loads_src` is the matching demand,
    or `None` when `dispatch_src` is one Excel workbook carrying both a
    `dispatch` and a `loads` sheet. Demand is not optional — see the module
    docstring on why synthesising it would produce a grid state that never
    existed.

    `dispatch` and `loads` are exactly what `validate_dispatch` and
    `validate_loads` return, so they are interchangeable with every other
    producer's output. `dispatch_source` is the provenance record the manifest
    carries: both files, both sha256s, the hours and the unit count, so a
    handoff bundle built from this is traceable to the files it came from.
    """
    dispatch_path = Path(dispatch_src)
    if not dispatch_path.is_file():
        raise ContractError(f"external dispatch: {dispatch_path} is not a file")

    if loads_src is None:
        # One workbook, two sheets. A CSV cannot carry a second table, and
        # saying so beats a KeyError from the reader.
        if dispatch_path.suffix.lower() not in _EXCEL_SUFFIXES:
            raise ContractError(
                "external dispatch: a loads table is required — a snapshot is "
                "generation AND demand, and synthesising the demand would "
                "describe a grid state that never existed. Supply a loads file "
                f"beside {dispatch_path.name}, or one Excel workbook with "
                f"{DISPATCH_SHEET!r} and {LOADS_SHEET!r} sheets."
            )
        loads_path = dispatch_path
        dispatch_frame = _read_frame(dispatch_path, DISPATCH_SHEET)
        loads_frame = _read_frame(loads_path, LOADS_SHEET)
    else:
        loads_path = Path(loads_src)
        if not loads_path.is_file():
            raise ContractError(f"external dispatch: loads file {loads_path} is not a file")
        dispatch_frame = _read_frame(dispatch_path)
        loads_frame = _read_frame(loads_path)

    dispatch = validate_dispatch(
        _map_columns(dispatch_frame, dispatch_path.name, EXTERNAL_ALIASES, DISPATCH_COLUMNS)
    )
    loads = validate_loads(
        _map_columns(loads_frame, loads_path.name, LOADS_ALIASES, LOADS_COLUMNS)
    )
    _check_units(dispatch, registry)

    # The two tables must describe the SAME hours. Demand for an hour the
    # dispatch does not cover, or a dispatched hour with no demand, is a
    # half-specified snapshot either way — and the load flow would quietly
    # balance it on the slack.
    d_hours, l_hours = _hours_of(dispatch), _hours_of(loads)
    if d_hours != l_hours:
        only_dispatch = sorted(set(d_hours) - set(l_hours))
        only_loads = sorted(set(l_hours) - set(d_hours))
        raise ContractError(
            "external dispatch and loads cover different hours: "
            f"dispatched with no demand {only_dispatch}, "
            f"demand with no dispatch {only_loads}"
        )

    source = {
        "external": str(dispatch_path),
        "external_sha256": _sha256(dispatch_path),
        "loads": str(loads_path),
        "loads_sha256": _sha256(loads_path),
        "hours": len(d_hours),
        "units": int(pd.Series(dispatch["unit_id"]).nunique()),
    }
    return dispatch, loads, source
