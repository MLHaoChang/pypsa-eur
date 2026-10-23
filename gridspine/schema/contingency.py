"""Stage-boundary contracts for contingency screening and fault levels.

Three artifacts cross these boundaries, and each gets the same pre-coercion
discipline as ``validate_dispatch``: guards read the values AS SUPPLIED,
because ``astype`` launders a fractional hour into an integer and the string
"False" into truthiness before any later check could see it.

Contingency set
    One row per outage to study: ``contingency_id``, ``kind`` in {branch,
    unit, bus_section, n_m_1, n_1_1}, ``element_ids`` (a list of canonical
    element ids) and ``order`` in {1, 2}. The two compound kinds are order 2
    and their id is the element ids joined on the kind's separator
    (``COMPOUND_SEPARATORS``): ``"{maint}|{fault}"`` and ``"{first}>{second}"``. The set is engine-free data — whether every element id names a
    real element is the CALLER's check against the registry, so this module
    stays importable without pandapower.

Contingency results
    One row per (contingency, hour). A non-convergent contingency is a ROW
    with ``converged=False`` carrying ``NON_CONVERGED_SEVERITY`` — never a
    missing row, and never a row whose severity happens to read benign. An
    absent row and a survived contingency are indistinguishable downstream,
    which is why the validator enforces the sentinel in both directions:
    diverged rows must carry it, converged rows must not. ``islanded`` marks
    the outages that split the grid (11 of case39's 46 branch outages do —
    nine radial generator connections and two that cut off a whole pocket);
    an islanded row is never converged, and the flag is what separates that
    topology fact, identical in every hour, from a numerical divergence.

Fault levels
    One row per (bus, case) from IEC 60909, ``case`` in {max, min}. A fault
    level of zero at an energised bus is a coverage failure (an element with
    no short-circuit data was silently skipped), not a result, so the floor is
    strictly positive.

Contingency ids obey the canonical charset by FULLMATCH (a ``$``-anchored
regex passes ``"BUS_01\\n"``), but NOT the 12-character cap: that cap is the
PSS/E v33 NAME field width and applies to the bus and unit names that reach
the .raw. A pair id like ``BUS_01-BUS_02-1--BUS_02-BUS_03-1`` is longer by
construction and never becomes a NAME.
"""
import re

import numpy as np
import pandas as pd

from .contracts import ContractError
from .network import NAME_CHARSET

#: Severity recorded for a contingency whose load flow did not converge.
#: A large FINITE float rather than ``inf``: it sorts above every real severity
#: and survives a CSV round-trip, which ``inf`` does not reliably do.
NON_CONVERGED_SEVERITY = 1.0e6

#: The compound kinds and the separator their id joins its two element ids on.
#: Both are outside the canonical charset, so a compound id never collides
#: with a plain one and its parts are recovered by one split.
COMPOUND_SEPARATORS = {"n_m_1": "|", "n_1_1": ">"}
CONTINGENCY_KINDS = frozenset({"branch", "unit", "bus_section", *COMPOUND_SEPARATORS})
FAULT_CASES = frozenset({"max", "min"})

CONTINGENCY_SET_COLUMNS = {
    "contingency_id": "object",
    "kind": "object",
    "element_ids": "object",
    "order": "int64",
}

CONTINGENCY_RESULT_COLUMNS = {
    "contingency_id": "object",
    "hour": "int64",
    "converged": "bool",
    "islanded": "bool",
    "max_branch_loading_pct": "float64",
    "min_vm_pu": "float64",
    "max_vm_pu": "float64",
    "n_violations": "int64",
    "severity": "float64",
}
_PHYSICS_COLUMNS = ("max_branch_loading_pct", "min_vm_pu", "max_vm_pu")

FAULT_LEVEL_COLUMNS = {
    "bus": "object",
    "ikss_ka": "float64",
    "sk_mva": "float64",
    "case": "object",
}


def _check_ids(values, what: str) -> None:
    """Canonical charset by fullmatch, no nulls, no empties. No length cap — see module docstring."""
    for v in values:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            raise ContractError(f"{what} contains a null id")
        if not isinstance(v, str):
            raise ContractError(f"{what} contains a non-string id: {v!r}")
        if v == "":
            raise ContractError(f"{what} contains an empty id")
        if not NAME_CHARSET.fullmatch(v):
            raise ContractError(
                f"{what} id contains characters outside [A-Za-z0-9_-]: {v!r}"
            )


def _check_contingency_ids(values, what: str) -> None:
    """``_check_ids``, except that one compound separator may join two canonical parts."""
    for v in values:
        if isinstance(v, str) and any(sep in v for sep in COMPOUND_SEPARATORS.values()):
            parts = re.split(r"[|>]", v)
            if len(parts) != 2:
                raise ContractError(f"{what} id has more than one compound separator: {v!r}")
            _check_ids(parts, what)
        else:
            _check_ids([v], what)


def _integral(series: pd.Series, what: str) -> pd.Series:
    """The pre-coercion integrality guard, shared by hour and count columns."""
    raw = pd.to_numeric(series, errors="coerce")
    bad = raw.isna() | (raw % 1 != 0)
    if bad.any():
        raise ContractError(
            f"{what} must be integral, got {series[bad].unique().tolist()}"
        )
    return raw


def validate_contingency_set(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(CONTINGENCY_SET_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"contingency set missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_contingency_ids(out["contingency_id"], "contingency_id")
    order_raw = out["order"]
    bad_order = (order_raw != 1) & (order_raw != 2)  # NaN != x is True, so NaN is bad
    if bad_order.any():
        raise ContractError(
            f"order must be exactly 1 or 2, got {order_raw[bad_order].unique().tolist()}"
        )
    bad_kind = ~out["kind"].isin(CONTINGENCY_KINDS)
    if bad_kind.any():
        raise ContractError(
            f"kind must be one of {sorted(CONTINGENCY_KINDS)}, got "
            f"{out.loc[bad_kind, 'kind'].unique().tolist()}"
        )
    for cid, elems, order in zip(out["contingency_id"], out["element_ids"], order_raw):
        # A bare string is iterable and would read as one element per character.
        if not isinstance(elems, (list, tuple)):
            raise ContractError(
                f"contingency {cid}: element_ids must be a list, got {type(elems).__name__}"
            )
        if len(elems) == 0:
            raise ContractError(f"contingency {cid}: element_ids is empty")
        if len(elems) != int(order):
            raise ContractError(
                f"contingency {cid}: order {int(order)} but {len(elems)} element_ids"
            )
        _check_ids(elems, f"contingency {cid} element_ids")
        if len(set(elems)) != len(elems):
            raise ContractError(f"contingency {cid}: element_ids repeat an element")
    for cid, kind, elems in zip(out["contingency_id"], out["kind"], out["element_ids"]):
        sep = COMPOUND_SEPARATORS.get(kind)
        if sep is None:
            if any(s in cid for s in COMPOUND_SEPARATORS.values()):
                raise ContractError(f"contingency {cid}: a compound separator on kind {kind!r}")
        elif len(elems) != 2:
            raise ContractError(f"contingency {cid}: a {kind} contingency is order 2, got {len(elems)} element_ids")
        elif cid != sep.join(elems):
            raise ContractError(
                f"contingency {cid}: a {kind} id is its element_ids joined on {sep!r}, "
                f"expected {sep.join(elems)!r}"
            )

    try:
        out = out.astype(CONTINGENCY_SET_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"contingency set dtype coercion failed: {exc}") from exc

    dup = out["contingency_id"].duplicated(keep=False)
    if dup.any():
        # Name the kinds too: a unit and a bus section may share a bare name.
        said = [
            f"{cid} (kinds: {', '.join(sorted(set(out.loc[out['contingency_id'] == cid, 'kind'])))})"
            for cid in sorted(out.loc[dup, "contingency_id"].unique())
        ]
        raise ContractError(f"duplicate contingency_id: {'; '.join(said)}")
    return out


def validate_contingency_results(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(CONTINGENCY_RESULT_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"contingency results missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_ids(out["contingency_id"], "contingency_id")
    bad_conv = [
        v for v in out["converged"] if not isinstance(v, (bool, np.bool_))
    ]
    if bad_conv:
        raise ContractError(
            f"converged must be exactly True or False, got {bad_conv[:5]!r}"
        )
    bad_isl = [v for v in out["islanded"] if not isinstance(v, (bool, np.bool_))]
    if bad_isl:
        raise ContractError(f"islanded must be exactly True or False, got {bad_isl[:5]!r}")
    _integral(out["hour"], "hour")
    n_viol = _integral(out["n_violations"], "n_violations")
    if (n_viol < 0).any():
        raise ContractError(
            f"n_violations must be >= 0, got {n_viol[n_viol < 0].unique().tolist()}"
        )

    try:
        out = out.astype(CONTINGENCY_RESULT_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"contingency results dtype coercion failed: {exc}") from exc

    sev = out["severity"]
    if sev.isna().any() or np.isinf(sev).any():
        raise ContractError("severity must be finite (no NaN, no inf)")
    if (sev < 0).any():
        raise ContractError(f"severity must be >= 0, got {sev[sev < 0].unique().tolist()}")

    converged = out["converged"]
    # Physics columns may be NaN ONLY on a diverged row — there is no flow to
    # report — and must be finite everywhere else.
    phys = out[list(_PHYSICS_COLUMNS)]
    nan_on_converged = phys.isna().any(axis=1) & converged
    if nan_on_converged.any():
        raise ContractError(
            "NaN physics on a converged row: "
            f"{out.loc[nan_on_converged, 'contingency_id'].tolist()}"
        )
    inf_any = np.isinf(phys.fillna(0.0)).any(axis=1)
    if inf_any.any():
        raise ContractError(
            f"non-finite inf in physics columns: {out.loc[inf_any, 'contingency_id'].tolist()}"
        )
    conv_rows = out[converged]
    if (conv_rows["max_branch_loading_pct"] < 0).any():
        raise ContractError("max_branch_loading_pct must be >= 0")
    if (conv_rows["min_vm_pu"] <= 0).any() or (conv_rows["max_vm_pu"] <= 0).any():
        raise ContractError("min_vm_pu and max_vm_pu must be > 0")
    if (conv_rows["min_vm_pu"] > conv_rows["max_vm_pu"]).any():
        raise ContractError("min_vm_pu exceeds max_vm_pu")

    # An islanded outage has no post-contingency flow to report on the whole
    # grid, so it is never "converged" — but it is a TOPOLOGY fact, identical in
    # every hour, and the flag lets a reader (and the ranking) tell it from a
    # numerical divergence.
    islanded_converged = out["islanded"] & converged
    if islanded_converged.any():
        raise ContractError(
            "islanded rows cannot be converged: "
            f"{out.loc[islanded_converged, 'contingency_id'].tolist()}"
        )

    # The cross-field rule, both directions.
    diverged_benign = (~converged) & (sev != NON_CONVERGED_SEVERITY)
    if diverged_benign.any():
        raise ContractError(
            "diverged rows must carry NON_CONVERGED_SEVERITY: "
            f"{out.loc[diverged_benign, 'contingency_id'].tolist()}"
        )
    converged_sentinel = converged & (sev == NON_CONVERGED_SEVERITY)
    if converged_sentinel.any():
        raise ContractError(
            "converged rows may not carry NON_CONVERGED_SEVERITY: "
            f"{out.loc[converged_sentinel, 'contingency_id'].tolist()}"
        )

    dup = out.duplicated(subset=["contingency_id", "hour"])
    if dup.any():
        raise ContractError(
            f"duplicate (contingency_id, hour) rows: {out.loc[dup, 'contingency_id'].tolist()}"
        )
    return out


def validate_fault_levels(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(FAULT_LEVEL_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"fault levels missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_ids(out["bus"], "bus")
    bad_case = ~out["case"].isin(FAULT_CASES)
    if bad_case.any():
        raise ContractError(
            f"case must be one of {sorted(FAULT_CASES)}, got "
            f"{out.loc[bad_case, 'case'].unique().tolist()}"
        )

    try:
        out = out.astype(FAULT_LEVEL_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"fault levels dtype coercion failed: {exc}") from exc

    for col in ("ikss_ka", "sk_mva"):
        vals = out[col]
        bad = vals.isna() | np.isinf(vals) | (vals <= 0)
        if bad.any():
            raise ContractError(
                f"{col} must be finite and > 0, got {vals[bad].unique().tolist()} "
                f"at bus(es) {out.loc[bad, 'bus'].tolist()}"
            )
    dup = out.duplicated(subset=["bus", "case"])
    if dup.any():
        raise ContractError(
            f"duplicate (bus, case) rows: {out.loc[dup, 'bus'].tolist()}"
        )
    return out


#: The verdicts of the planning-criteria check (``static/planning_criteria``).
#: ``not_solved`` is an islanded or non-converged contingency: no flow to judge.
PLANNING_VERDICTS = frozenset({"pass", "fail", "not_solved"})
#: The angle-limit verdicts: ``unknown`` where no limit is set or no pair could
#: be measured, never ``pass``; ``not_solved`` exactly where stage 1 is.
ANGLE_VERDICTS = frozenset({"pass", "fail", "unknown", "not_solved"})

PLANNING_RESULT_COLUMNS = {
    "contingency_id": "object",
    "kind": "object",
    "hour": "int64",
    "converged": "bool",
    "islanded": "bool",
    "load_lost_mw": "float64",
    "gen_lost_mw": "float64",
    "stage1_loading_pct": "float64",
    "stage1_branch": "object",
    "stage1_verdict": "object",
    "stage2_loading_pct": "float64",
    "stage2_branch": "object",
    "stage2_min_vm_pu": "float64",
    "stage2_max_vm_pu": "float64",
    "stage2_verdict": "object",
    "remedial": "object",
    "angle_deg": "float64",
    "angle_pair": "object",
    "angle_verdict": "object",
    "note": "object",
}
_STAGE2_PHYSICS = ("stage2_loading_pct", "stage2_min_vm_pu", "stage2_max_vm_pu")


def validate_planning_results(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (contingency, hour) of the two-stage check. A stage that is
    not solved reads ``not_solved`` with NaN figures, never a benign number;
    stage 2 is never solved when stage 1 is not."""
    missing = set(PLANNING_RESULT_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"planning results missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_contingency_ids(out["contingency_id"], "contingency_id")
    bad_kind = ~out["kind"].isin(CONTINGENCY_KINDS)
    if bad_kind.any():
        raise ContractError(f"kind must be one of {sorted(CONTINGENCY_KINDS)}, got "
                            f"{out.loc[bad_kind, 'kind'].unique().tolist()}")
    for col in ("converged", "islanded"):
        bad = [v for v in out[col] if not isinstance(v, (bool, np.bool_))]
        if bad:
            raise ContractError(f"{col} must be exactly True or False, got {bad[:5]!r}")
    _integral(out["hour"], "hour")
    for col in ("stage1_verdict", "stage2_verdict"):
        bad = ~out[col].isin(PLANNING_VERDICTS)
        if bad.any():
            raise ContractError(f"{col} must be one of {sorted(PLANNING_VERDICTS)}, got "
                                f"{out.loc[bad, col].unique().tolist()}")
    bad = ~out["angle_verdict"].isin(ANGLE_VERDICTS)
    if bad.any():
        raise ContractError(f"angle_verdict must be one of {sorted(ANGLE_VERDICTS)}, got "
                            f"{out.loc[bad, 'angle_verdict'].unique().tolist()}")

    try:
        out = out.astype(PLANNING_RESULT_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"planning results dtype coercion failed: {exc}") from exc

    for col in ("load_lost_mw", "gen_lost_mw"):
        v = out[col]
        if v.isna().any() or np.isinf(v).any() or (v < 0).any():
            raise ContractError(f"{col} must be finite and >= 0")
    ids = out["contingency_id"]
    if (out["islanded"] & out["converged"]).any():
        raise ContractError(f"islanded rows cannot be converged: {ids[out['islanded'] & out['converged']].tolist()}")
    s1_solved = out["stage1_verdict"] != "not_solved"
    if (s1_solved != out["converged"]).any():
        raise ContractError("stage1_verdict is not_solved exactly when the row is not converged: "
                            f"{ids[s1_solved != out['converged']].tolist()}")
    s2_solved = out["stage2_verdict"] != "not_solved"
    if (s2_solved & ~s1_solved).any():
        raise ContractError(f"stage 2 solved where stage 1 is not: {ids[s2_solved & ~s1_solved].tolist()}")
    s1 = out["stage1_loading_pct"]
    if (s1.isna() == s1_solved).any() or np.isinf(s1.fillna(0.0)).any():
        raise ContractError("stage1_loading_pct must be finite where stage 1 is solved and NaN elsewhere")
    s2 = out[list(_STAGE2_PHYSICS)]
    if (s2.isna().any(axis=1) & s2_solved).any() or (s2.notna().any(axis=1) & ~s2_solved).any() \
            or np.isinf(s2.fillna(0.0)).any(axis=None):
        raise ContractError("stage-2 figures must be finite where stage 2 is solved and NaN elsewhere")
    a_solved = out["angle_verdict"] != "not_solved"
    if (a_solved != s1_solved).any():
        raise ContractError("angle_verdict is not_solved exactly where stage 1 is: "
                            f"{ids[a_solved != s1_solved].tolist()}")
    ang = out["angle_deg"]
    judged = out["angle_verdict"].isin(["pass", "fail"])
    if (ang.isna() & judged).any() or (ang.notna() & ~a_solved).any() or np.isinf(ang.fillna(0.0)).any() \
            or (ang < 0).any():
        raise ContractError("angle_deg must be finite and >= 0 where the angle is judged, NaN where not solved")
    dup = out.duplicated(subset=["contingency_id", "hour"])
    if dup.any():
        raise ContractError(f"duplicate (contingency_id, hour) rows: {ids[dup].tolist()}")
    return out
