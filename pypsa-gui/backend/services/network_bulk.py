"""
Bulk edit (`PATCH /api/network/_bulk`) — coerce rules and apply path.

Extracted verbatim from `routers/network.py`. The router keeps a thin
`bulk_update` handler (FastAPI decorator + docstring) and re-exports every
helper so existing imports and bite comments stay valid. Never imports
`routers.*`.

Do not fold the inlined coerce loop in `apply_bulk_update` into
`_coerce_bulk_value` in the same change — the MERGE NOTE documents why both
shapes exist.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
from fastapi import HTTPException

from services import change_log_service
from services.carrier_catalog import ensure_carrier
from services.pypsa_service import PyPSAService


# Map tab/component-class names → PyPSA's network-attribute name. The frontend
# only ever knows the component class (e.g. "Generator"), so the bulk endpoint
# resolves the corresponding DataFrame here. Keeps the API contract narrow:
# the client doesn't need to know PyPSA's internal attribute conventions.
_COMPONENT_ATTRS: dict[str, str] = {
    "Bus": "buses",
    "Carrier": "carriers",
    "Line": "lines",
    "Link": "links",
    "Transformer": "transformers",
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Load": "loads",
    "ShuntImpedance": "shunt_impedances",
}


# Phase 12f: the five LP bounds whose PyPSA class default is FINITE, so that
# clearing one has a real value to write. Mirrors
# `services.validation_service.FINITE_DEFAULT_BOUNDS`, which is the preflight
# that catches whatever gets past this route.
_FINITE_DEFAULT_BOUNDS = ("p_max_pu", "p_min_pu", "s_max_pu",
                          "e_max_pu", "e_min_pu")


def _finite_input_meta(component_class: str, col: str):
    """``(default, type)`` for ``col`` when it is a numeric INPUT attribute of
    ``component_class`` whose PyPSA class default is finite — the set Phase
    12g refuses NaN in — else ``None``. Read from PyPSA's own component
    metadata (`services.validation_service.finite_default_inputs`), so the
    two cannot drift; 12f's five bounds are the fallback so a PyPSA that
    reshapes `defaults` cannot turn a clear into a NaN write, which is the
    exact defect this exists to fix.
    """
    try:
        from services.pypsa_service import PyPSAService
        from services.validation_service import finite_default_inputs
        n = PyPSAService.get_network()
        meta = finite_default_inputs(n.components[component_class])
        if col in meta:
            dv, _varying, typ = meta[col]
            return float(dv), typ
        return None
    except Exception:                                         # noqa: BLE001
        if col not in _FINITE_DEFAULT_BOUNDS:
            return None
        if col == "p_min_pu":
            return (-1.0 if component_class == "StorageUnit" else 0.0), "float"
        return (0.0 if col == "e_min_pu" else 1.0), "float"


def _finite_bound_default(component_class: str, col: str) -> float:
    """12f's name, kept for its callers: the class default of one of the five
    bounds, through the metadata."""
    meta = _finite_input_meta(component_class, col)
    if meta is not None:
        return meta[0]
    if col == "p_min_pu":
        return -1.0 if component_class == "StorageUnit" else 0.0
    return 0.0 if col == "e_min_pu" else 1.0


def _bool_input_default(component_class: str, col: str) -> bool:
    """The class default of a BOOLEAN input column, read from PyPSA's own
    metadata — 12g's `finite_default_inputs` pattern for `type == "boolean"`.

    A hand-written "bools clear to False" list is wrong twice over: `active`
    defaults to True on EVERY class, and so does `Link.cyclic_delay`, which
    is bulk-editable. A custom GUI column (`p_max_pu_includes_outages`) is
    not in the table at all and falls back to False, its declared default.
    """
    try:
        from services.pypsa_service import PyPSAService
        comp = PyPSAService.get_network().components[component_class]
        d = getattr(comp, "defaults", None)
        if d is None:
            d = getattr(comp, "attrs", None)
        row = d.loc[col]
        if str(row.get("type", "")).strip() == "boolean" \
                and str(row.get("status", "")).strip().startswith("Input"):
            return bool(row.get("default"))
    except Exception:                                         # noqa: BLE001
        pass
    return False


def _coerce_bulk_value(df: pd.DataFrame, col: str, value: Any,
                       component_class: str) -> Any:
    """
    Coerce one bulk value to `col`'s existing dtype.

    MERGE NOTE (2026-09-10). This branch extracted the helper so the
    per-row form (spec D9) could apply identical semantics; master kept the
    logic inline and then GREW it — the Phase 12f/12g finite-default rules,
    the `active` 422, `_bool_input_default`, the non-finite refusal. Taking
    either side whole lost the other half: this branch's row form, or
    master's rules.

    Master's loop body is kept BYTE-FOR-BYTE below, wrapped in a one-pass
    loop so its `continue` statements still mean "this column is done".
    Rewriting them into `return`s was tried first and silently broke the
    numeric branch, whose later checks read back `coerced[col]`. Keeping
    the body verbatim means there is nothing to get wrong, and master's
    own tests (`test_nonfinite_bounds`, `test_nonfinite_inputs`,
    `test_includes_outages`) pin every rule.

    `component_class` is new to the signature and load-bearing: master's
    rules consult per-class metadata (`_finite_input_meta`,
    `_bool_input_default`) the old three-argument form could not see.
    """
    coerced: dict[str, Any] = {}
    for _ in (0,):
        col_dtype = df[col].dtype
        if pd.api.types.is_bool_dtype(col_dtype):
            if isinstance(value, str):
                if value.strip().lower() in ("true", "1", "yes"):
                    value = True
                elif value.strip().lower() in ("false", "0", "no"):
                    value = False
            if value is None:
                # Phase 12h: the bulk editor sends `null` for a blank
                # cell, and `df.loc[...] = None` upcasts the column to
                # `object` — the one shape netCDF refuses — so the next
                # project save is a 500. A null clears to the column's
                # CLASS DEFAULT, read from PyPSA's metadata rather than
                # assumed False.
                #
                # `active` is refused instead. Its default is True, so
                # clearing it would ACTIVATE every selected asset,
                # behind a confirm toast that reads "Set active =
                # (unset) on 200 generator(s)?". 422 is the shape this
                # route already uses for a value it could write but
                # refuses on what the write would MEAN (12g's non-finite
                # refusal); 400 is its wrong-type answer.
                if col == "active":
                    raise HTTPException(
                        422,
                        "Column 'active' cannot be cleared — send true "
                        "or false. Its PyPSA default is true, so "
                        "clearing it would ACTIVATE every selected "
                        "asset rather than leave it as it is.")
                coerced[col] = _bool_input_default(component_class, col)
                continue
            coerced[col] = bool(value)
            continue
        if pd.api.types.is_numeric_dtype(col_dtype):
            if value is None or value == "":
                # Blank-to-clear a bound should produce PyPSA's "no bound"
                # sentinel (±inf), matching how the per-row PUT path clears the
                # capacity/economic bounds via the schema aliases (_NoneToPosInf
                # on *_max / lifetime, _NoneToNegInf on e_sum_min). The
                # endswith("_max") predicate is intentionally a superset: it also
                # covers PyPSA's inf-default voltage bounds (v_mag_pu_max,
                # v_ang_max) — clearing those to inf is likewise their PyPSA
                # default, so the resulting network is valid. Everything else
                # keeps NaN ("missing"), as before.
                # Phase 12g: the finite-default metadata decides FIRST. The
                # suffix rules below target ±inf-default columns (`p_nom_max`,
                # `lifetime`, `e_sum_min`) — but `Transformer.phase_shift_max`
                # ends in `_max` and defaults to 0.0, and clearing it to `inf`
                # made the next solve refuse the value `_bulk` itself wrote.
                _meta = _finite_input_meta(component_class, col)
                if _meta is not None:
                    coerced[col] = _meta[0]
                elif col.endswith("_max") or col == "lifetime":
                    coerced[col] = float("inf")
                elif col == "e_sum_min":
                    coerced[col] = float("-inf")
                elif col in _FINITE_DEFAULT_BOUNDS:
                    # Phase 12f. NaN is not a valid "no bound" sentinel for
                    # these five: PyPSA does not fall back to a default, it
                    # MASKS the constraint row out of the LP, so clearing
                    # `p_max_pu` used to leave a 100 MW unit free to dispatch
                    # 500 MW. Their class default is finite, so "unset" has a
                    # real value — and it is exactly what `n.add(attr=None)`
                    # coerces to, verified for all five across Generator,
                    # Link, StorageUnit, Store, Line and Transformer. Keyed by
                    # (component, column) because `StorageUnit.p_min_pu` is
                    # −1.0 where a Generator's is 0.0.
                    #
                    # `ramp_limit_*` deliberately still lands in the NaN branch
                    # below: there the class default IS NaN and PyPSA masks the
                    # row on purpose, which is the documented way to say "this
                    # unit has no ramp limit".
                    coerced[col] = _finite_bound_default(component_class, col)
                else:
                    coerced[col] = float("nan")  # pandas treats this as missing
                continue
            try:
                coerced[col] = float(value)
            except (TypeError, ValueError):
                raise HTTPException(400,
                    f"Column '{col}' is numeric ({col_dtype}); got non-numeric "
                    f"value {value!r}.")
            # Phase 12f: `json.loads` accepts the bare `NaN` and `Infinity`
            # literals and `float()` accepts the strings "nan" and "inf", so
            # a non-finite value can reach one of the five bounds past the
            # `null` branch above. It masks the LP row exactly as a cleared
            # cell did, so it is refused here — the same answer the time-
            # series routes give — rather than accepted and refused at solve.
            # Whole-branch review S1: the outage rate is a probability-like
            # unavailability — finite and in [0, 1) — and the engines
            # convolve whatever number is here, so the bulk path refuses
            # exactly what the create/update schemas refuse.
            if col == "outage_rate_value" and not (
                    math.isfinite(coerced[col]) and 0.0 <= coerced[col] < 1.0):
                raise HTTPException(
                    422,
                    f"Column 'outage_rate_value' must be a finite number in "
                    f"[0, 1); got {value!r}. It is a probability-like "
                    "unavailability, not a percentage or count. Send null "
                    "to unset it (the per-carrier default then applies).")
            if not math.isfinite(coerced[col]) and (
                    col in _FINITE_DEFAULT_BOUNDS
                    or _finite_input_meta(component_class, col) is not None):
                # Phase 12g: every finite-default input, not only the five.
                raise HTTPException(
                    422,
                    f"Column '{col}' must be a finite number; got {value!r}. "
                    "PyPSA does not default a non-finite value here, it drops "
                    "the term or the constraint that reads it. Send null to "
                    "restore the default.")
            continue
        # Strings / objects pass through. We still cast to str if the user
        # sent a number into a string column so dtype stays clean.
        if pd.api.types.is_string_dtype(col_dtype) or pd.api.types.is_object_dtype(col_dtype):
            coerced[col] = "" if value is None else str(value)
            continue
        coerced[col] = value
    return coerced.get(col, value)


# MERGE NOTE (2026-09-10): master's `bulk_update` is the base, with this
# branch's per-row form (spec D9) grafted on. Master's is the better base:
# it added the Phase 12h single-lock hold — load-bearing, because the flag
# normaliser can CREATE a column that the unknown-column check and the
# dtype dispatch then read — plus the transient-row refusal and the
# finite-default rules. Taking this branch's version whole (the first
# attempt) lost all of that and failed master's own tests.
def apply_bulk_update(body: dict) -> dict:
    """Bulk edit. Two request shapes, one implementation.

    `names` + `updates` applies ONE set of values to many rows; `rows` (spec
    D9) applies a DIFFERENT set per row. They are mutually exclusive, and
    everything after validation treats them as a list of batches so the
    coercion below — the only place that knows a column's dtype rules — has
    exactly one implementation rather than one per shape.
    """
    component_class = body.get("component_class", "")
    names = body.get("names", [])
    updates = body.get("updates", {})
    rows = body.get("rows")

    if component_class not in _COMPONENT_ATTRS:
        raise HTTPException(400, f"Unknown component_class '{component_class}'. "
            f"Expected one of: {', '.join(sorted(_COMPONENT_ATTRS))}.")
    row_form = rows is not None
    if row_form and (names or updates):
        raise HTTPException(400, "Send either names+updates or rows, not both.")

    if row_form:
        if not isinstance(rows, list) or len(rows) == 0:
            raise HTTPException(400, "rows must be a non-empty list")
        pairs: list[tuple[str, dict]] = []
        for i, entry in enumerate(rows):
            if not isinstance(entry, dict):
                raise HTTPException(400, f"rows[{i}] must be an object")
            nm = entry.get("name")
            up = entry.get("updates")
            if not isinstance(nm, str) or not nm:
                raise HTTPException(400, f"rows[{i}] needs a non-empty 'name'")
            if not isinstance(up, dict) or len(up) == 0:
                raise HTTPException(400, f"rows[{i}] needs a non-empty 'updates' object")
            if "name" in up:
                raise HTTPException(400,
                    "Bulk rename not supported. Use PUT /<component>/{name}.")
            pairs.append((nm, up))
        name_strs = [nm for nm, _ in pairs]
        # A duplicate name would make the result order-dependent and the undo
        # step ambiguous. One gesture is one request; a client that targets the
        # same row twice has a bug worth surfacing.
        if len(set(name_strs)) != len(name_strs):
            dupes = sorted({x for x in name_strs if name_strs.count(x) > 1})
            raise HTTPException(400,
                f"Duplicate row name(s) in rows: {', '.join(dupes[:5])}.")
        touched_cols = {c for _, up in pairs for c in up}
    else:
        if not isinstance(names, list) or len(names) == 0:
            raise HTTPException(400, "names must be a non-empty list")
        if not isinstance(updates, dict) or len(updates) == 0:
            raise HTTPException(400, "updates must be a non-empty object")
        if "name" in updates:
            raise HTTPException(400, "Bulk rename not supported. Use PUT /<component>/{name}.")
        name_strs = [str(x) for x in names]
        touched_cols = set(updates)

    # ONE lock hold spans the prologue, the unknown-column check, the dtype
    # dispatch and the write (Phase 12h). The flag normaliser below can CREATE
    # a column, and both the check and the dispatch read the frame's columns
    # and dtypes — a solve adding and removing its slack rows underneath would
    # make the route write against a shape it never inspected. `get_lock()` is
    # an RLock, so a caller already holding it is unaffected.
    #
    # All-or-nothing is preserved INSIDE the hold: every batch is coerced
    # before the first `df.loc` write, so a bad value in row 9 still leaves
    # rows 1-8 untouched. That was the reason the coercion used to sit outside
    # the lock; ordering, not lock scope, is what actually buys it.
    with PyPSAService.get_lock():
        attr = _COMPONENT_ATTRS[component_class]
        n = PyPSAService.get_network()
        df = getattr(n, attr)

        # `p_max_pu_includes_outages` is a custom BOOL column, and this route
        # is the one that has to set it on an import whose frame never carried
        # it — without the create-if-absent the unknown-column check below
        # refuses with `has no column(s)`. Normalising HERE, ahead of that
        # check AND of the dtype dispatch that reads `df[col].dtype`, is also
        # what makes a `_bulk` write land as a real `bool`.
        if attr == "generators":
            try:
                from services.adequacy.occurrence import normalise_flag_column
                normalise_flag_column(n)
            except Exception:                                 # noqa: BLE001
                pass

        # Bulk semantics: refuse the whole batch if any target is missing —
        # partial application would be hard to undo predictably.
        missing = [n_ for n_ in name_strs if n_ not in df.index]
        if missing:
            sample = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            raise HTTPException(404, f"{len(missing)} {component_class}(s) not found: {sample}")

        # Reject any target that is currently a solver-internal transient row
        # (vintage clone, VOLL slack). The /api/network/{component} filter hides
        # these from the UI, so a frontend cannot normally surface their names —
        # but a stale localStorage payload or a CLI hitting this endpoint could.
        # Mutating LP scaffolding mid-solve corrupts the optimisation subtly.
        transient_targets = [n_ for n_ in name_strs
                             if n_ in PyPSAService.get_transient_rows(component_class)]
        if transient_targets:
            sample = ", ".join(transient_targets[:3]) + ("…" if len(transient_targets) > 3 else "")
            raise HTTPException(
                409,
                f"Cannot bulk-edit {len(transient_targets)} {component_class}(s) "
                f"({sample}) — these rows are LP scaffolding generated by the "
                f"current solve (vintage clones or VOLL slacks). Wait for the "
                f"solver to finish and try again on the parent row(s).",
            )

        # Validate every column exists, across EVERY batch. PyPSA defines its
        # schema lazily, so the column may exist on the frame with no row
        # setting it — this catches typos like "p_min_pu " (trailing space).
        unknown_cols = [c for c in sorted(touched_cols) if c not in df.columns]
        if unknown_cols:
            raise HTTPException(400,
                f"{component_class} has no column(s): {', '.join(unknown_cols)}.")

        # The two request shapes reduce to the same thing here: a list of
        # (target rows, values) batches. Flat form is one batch over every
        # name; row form is one batch per row.
        batches = ([([nm], up) for nm, up in pairs] if row_form
                   else [(name_strs, updates)])

        # Coerce EVERY batch before writing ANY of them.
        pending: list[tuple[list[str], dict[str, Any]]] = []
        for _targets, _updates in batches:
            coerced: dict[str, Any] = {}
            for col, value in _updates.items():
                col_dtype = df[col].dtype
                if pd.api.types.is_bool_dtype(col_dtype):
                    if isinstance(value, str):
                        if value.strip().lower() in ("true", "1", "yes"):
                            value = True
                        elif value.strip().lower() in ("false", "0", "no"):
                            value = False
                    if value is None:
                        # Phase 12h: the bulk editor sends `null` for a blank
                        # cell, and `df.loc[...] = None` upcasts the column to
                        # `object` — the one shape netCDF refuses — so the next
                        # project save is a 500. A null clears to the column's
                        # CLASS DEFAULT, read from PyPSA's metadata rather than
                        # assumed False.
                        #
                        # `active` is refused instead. Its default is True, so
                        # clearing it would ACTIVATE every selected asset,
                        # behind a confirm toast that reads "Set active =
                        # (unset) on 200 generator(s)?". 422 is the shape this
                        # route already uses for a value it could write but
                        # refuses on what the write would MEAN (12g's non-finite
                        # refusal); 400 is its wrong-type answer.
                        if col == "active":
                            raise HTTPException(
                                422,
                                "Column 'active' cannot be cleared — send true "
                                "or false. Its PyPSA default is true, so "
                                "clearing it would ACTIVATE every selected "
                                "asset rather than leave it as it is.")
                        coerced[col] = _bool_input_default(component_class, col)
                        continue
                    coerced[col] = bool(value)
                    continue
                if pd.api.types.is_numeric_dtype(col_dtype):
                    if value is None or value == "":
                        # Blank-to-clear a bound should produce PyPSA's "no bound"
                        # sentinel (±inf), matching how the per-row PUT path clears the
                        # capacity/economic bounds via the schema aliases (_NoneToPosInf
                        # on *_max / lifetime, _NoneToNegInf on e_sum_min). The
                        # endswith("_max") predicate is intentionally a superset: it also
                        # covers PyPSA's inf-default voltage bounds (v_mag_pu_max,
                        # v_ang_max) — clearing those to inf is likewise their PyPSA
                        # default, so the resulting network is valid. Everything else
                        # keeps NaN ("missing"), as before.
                        # Phase 12g: the finite-default metadata decides FIRST. The
                        # suffix rules below target ±inf-default columns (`p_nom_max`,
                        # `lifetime`, `e_sum_min`) — but `Transformer.phase_shift_max`
                        # ends in `_max` and defaults to 0.0, and clearing it to `inf`
                        # made the next solve refuse the value `_bulk` itself wrote.
                        _meta = _finite_input_meta(component_class, col)
                        if _meta is not None:
                            coerced[col] = _meta[0]
                        elif col.endswith("_max") or col == "lifetime":
                            coerced[col] = float("inf")
                        elif col == "e_sum_min":
                            coerced[col] = float("-inf")
                        elif col in _FINITE_DEFAULT_BOUNDS:
                            # Phase 12f. NaN is not a valid "no bound" sentinel for
                            # these five: PyPSA does not fall back to a default, it
                            # MASKS the constraint row out of the LP, so clearing
                            # `p_max_pu` used to leave a 100 MW unit free to dispatch
                            # 500 MW. Their class default is finite, so "unset" has a
                            # real value — and it is exactly what `n.add(attr=None)`
                            # coerces to, verified for all five across Generator,
                            # Link, StorageUnit, Store, Line and Transformer. Keyed by
                            # (component, column) because `StorageUnit.p_min_pu` is
                            # −1.0 where a Generator's is 0.0.
                            #
                            # `ramp_limit_*` deliberately still lands in the NaN branch
                            # below: there the class default IS NaN and PyPSA masks the
                            # row on purpose, which is the documented way to say "this
                            # unit has no ramp limit".
                            coerced[col] = _finite_bound_default(component_class, col)
                        else:
                            coerced[col] = float("nan")  # pandas treats this as missing
                        continue
                    try:
                        coerced[col] = float(value)
                    except (TypeError, ValueError):
                        raise HTTPException(400,
                            f"Column '{col}' is numeric ({col_dtype}); got non-numeric "
                            f"value {value!r}.")
                    # Phase 12f: `json.loads` accepts the bare `NaN` and `Infinity`
                    # literals and `float()` accepts the strings "nan" and "inf", so
                    # a non-finite value can reach one of the five bounds past the
                    # `null` branch above. It masks the LP row exactly as a cleared
                    # cell did, so it is refused here — the same answer the time-
                    # series routes give — rather than accepted and refused at solve.
                    # Whole-branch review S1: the outage rate is a probability-like
                    # unavailability — finite and in [0, 1) — and the engines
                    # convolve whatever number is here, so the bulk path refuses
                    # exactly what the create/update schemas refuse.
                    if col == "outage_rate_value" and not (
                            math.isfinite(coerced[col]) and 0.0 <= coerced[col] < 1.0):
                        raise HTTPException(
                            422,
                            f"Column 'outage_rate_value' must be a finite number in "
                            f"[0, 1); got {value!r}. It is a probability-like "
                            "unavailability, not a percentage or count. Send null "
                            "to unset it (the per-carrier default then applies).")
                    if not math.isfinite(coerced[col]) and (
                            col in _FINITE_DEFAULT_BOUNDS
                            or _finite_input_meta(component_class, col) is not None):
                        # Phase 12g: every finite-default input, not only the five.
                        raise HTTPException(
                            422,
                            f"Column '{col}' must be a finite number; got {value!r}. "
                            "PyPSA does not default a non-finite value here, it drops "
                            "the term or the constraint that reads it. Send null to "
                            "restore the default.")
                    continue
                # Strings / objects pass through. We still cast to str if the user
                # sent a number into a string column so dtype stays clean.
                if pd.api.types.is_string_dtype(col_dtype) or pd.api.types.is_object_dtype(col_dtype):
                    coerced[col] = "" if value is None else str(value)
                    continue
                coerced[col] = value

            pending.append((_targets, coerced))

        for _targets, coerced in pending:
            # If the batch sets `carrier`, ensure the carrier row exists with
            # catalog metadata first — same auto-add behaviour as PUT.
            if component_class != "Carrier" and isinstance(coerced.get("carrier"), str):
                ensure_carrier(n, coerced["carrier"])
            for col, value in coerced.items():
                # pandas keeps an int64 column int64 when the written value is
                # integral and upcasts only on NaN, so `build_year` cleared to
                # its default 0 stays int64 with no help (measured, Phase 12g).
                df.loc[_targets, col] = value

    # One audit entry per bulk op (not per component). The row form cannot
    # print every value, so it prints the shape instead.
    if row_form:
        description = f"Bulk: {len(touched_cols)} field(s) across {len(name_strs)} row(s)"
        fields = sorted(touched_cols)
    else:
        description = "Bulk: " + ", ".join(f"{k}={v}" for k, v in updates.items())
        fields = list(updates.keys())
    change_log_service.log(
        "update", component_class, f"({len(name_strs)} items)", description,
    )
    return {"updated": len(name_strs), "fields": fields}


