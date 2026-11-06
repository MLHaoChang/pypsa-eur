"""Derived claims over checked numbers (AA-03 S2, owner freeze AA03-FREEZE-1 item 2).

A draft may name a derived entry: a registered formula over blocks the answer
publishes as checked numbers, or over earlier derived entries. The software
recomputes it; the model's value is only compared, never shown. Every failure
raises ValueError("derived_<reason>"), which limits the whole draft.

Operands share one scope: project, run, window and cost basis (GK B S2 Q=(a)).
A cross-run difference stays with the checked cost-delta profile.
"""

import math

# The model's value must match the software's to 1e-9 relative; a rounded
# claim fails (GK B D-6: no wider tolerance without an owner decision).
RELATIVE_TOLERANCE = 1e-9
# A denominator this close to zero gives no percentage (owned by the formulas).
NEAR_ZERO = 1e-9
MAX_ENTRIES = 8
# Unit -> (dimension, factor to the dimension's base unit). MVA is apparent
# power, not active power: it converts to nothing (GK B D-4).
UNITS = {
    "kW": ("power", 1e-3),
    "MW": ("power", 1.0),
    "GW": ("power", 1e3),
    "kWh": ("energy", 1e-3),
    "MWh": ("energy", 1.0),
}
PERCENT, POINTS = "%", "pp"


def _refuse(reason):
    raise ValueError("derived_" + reason)


def _same_unit(units):
    """Every operand in one unit; power against energy has its own reason."""
    if len(set(units)) == 1:
        return units[0]
    dimensions = {UNITS[u][0] for u in units if u in UNITS}
    if dimensions == {"power", "energy"}:
        _refuse("power_energy_refused")
    _refuse("unit_mismatch")


def _not_percent(unit):
    # Percent on percent is percentage_point_delta's alone (GK B D-4).
    if unit in (PERCENT, POINTS):
        _refuse("unit_mismatch")
    return unit


def _difference(values, units, entry):
    return values[0] - values[1], _not_percent(_same_unit(units))


def _sum(values, units, entry):
    return math.fsum(values), _not_percent(_same_unit(units))


def _percent_change(values, units, entry):
    if entry.denominator is None:
        _refuse("missing_denominator")
    if entry.denominator != entry.operands[1]:
        # The baseline is the second operand and the named denominator (D-3).
        _refuse("denominator_not_operand")
    _not_percent(_same_unit(units))
    new, base = values
    if abs(base) <= NEAR_ZERO:
        _refuse("near_zero_denominator")
    return (new - base) / abs(base) * 100, PERCENT


def _percentage_point_delta(values, units, entry):
    if set(units) != {PERCENT}:
        _refuse("unit_mismatch")
    return values[0] - values[1], POINTS


def _share_percent(values, units, entry):
    part, basis = values
    _not_percent(_same_unit(units))
    if abs(basis) <= NEAR_ZERO:
        _refuse("near_zero_denominator")
    return part / basis * 100, PERCENT


def _unit_convert(values, units, entry):
    source, target = units[0], entry.to_unit
    if target is None:
        _refuse("missing_target_unit")
    if source not in UNITS or target not in UNITS:
        _refuse("unit_mismatch")
    if UNITS[source][0] != UNITS[target][0]:
        _refuse("power_energy_refused")
    return values[0] * UNITS[source][1] / UNITS[target][1], target


# formula_id -> (operand count range, compute, the optional field it takes).
FORMULAS = {
    "difference/1": ((2, 2), _difference, None),
    "sum/1": ((2, MAX_ENTRIES), _sum, None),
    "percent_change/1": ((2, 2), _percent_change, "denominator"),
    "percentage_point_delta/1": ((2, 2), _percentage_point_delta, None),
    "share_percent/1": ((1, 1), _share_percent, "basis"),
    "unit_convert/1": ((1, 1), _unit_convert, "to_unit"),
}
OPTIONAL = ("denominator", "basis", "to_unit")


def scope_key(record):
    """What two operands must share: project, run, window and cost basis."""
    scope = record["scope"]
    request = record.get("request_scope") or {}
    kind = scope.get("kind")
    run = (
        scope.get("entity_id")
        if kind in ("run", "run_section", "run_comparison")
        else scope.get("run_id")
    )
    return {
        "run": (record["project_id"], run, scope.get("baseline_run_id")),
        "window": (
            scope.get("start", request.get("start")),
            scope.get("end", request.get("end")),
            scope.get("window"),
        ),
        "basis": request.get("cost_basis"),
    }


def _shared_scope(scopes):
    for part in ("run", "window", "basis"):
        if len({repr(s[part]) for s in scopes}) != 1:
            _refuse("mixed_scope")
    return scopes[0]


def check(entries, published):
    """Recompute each entry over `published` ({block id: (value, unit, scope)}).

    Returns the public entries with the software's value and unit.
    """
    if len(entries) > MAX_ENTRIES:
        _refuse("too_many")
    known = dict(published)
    public = []
    for entry in entries:
        if entry.id in known:
            _refuse("repeated_id")
        if entry.formula_id not in FORMULAS:
            _refuse("unknown_formula")
        (low, high), compute, optional = FORMULAS[entry.formula_id]
        if not low <= len(entry.operands) <= high or len(set(entry.operands)) != len(
            entry.operands
        ):
            _refuse("wrong_arity")
        if any(
            getattr(entry, name) is not None for name in OPTIONAL if name != optional
        ):
            _refuse("wrong_operands")
        ids = list(entry.operands)
        if optional == "basis":
            if entry.basis is None:
                _refuse("missing_basis")
            if entry.basis in ids:
                _refuse("wrong_operands")
            ids.append(entry.basis)
        missing = [i for i in ids if i not in known]
        if missing:
            # Earlier entries are known; the entry itself or a later one is not.
            later = {e.id for e in entries}
            _refuse(
                "forward_or_self_reference"
                if set(missing) <= later
                else "operand_not_published"
            )
        operands = [known[i] for i in ids]
        scope = _shared_scope([o[2] for o in operands])
        value, unit = compute([o[0] for o in operands], [o[1] for o in operands], entry)
        if not math.isfinite(value):
            _refuse("near_zero_denominator")
        if entry.unit != unit:
            _refuse("unit_mismatch")
        if abs(entry.value - value) > RELATIVE_TOLERANCE * abs(value):
            _refuse("value_mismatch")
        known[entry.id] = (value, unit, scope)
        public.append(
            {
                "id": entry.id,
                "kind": "derived",
                "formula_id": entry.formula_id,
                "operands": ids,
                "value": value,
                "unit": unit,
            }
        )
    return public


def line(entry):
    value = repr(entry["value"]).removesuffix(".0")
    return f"Derived {entry['id']} ({entry['formula_id']} of {', '.join(entry['operands'])}): {value} {entry['unit']}."
