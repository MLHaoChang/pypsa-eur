"""A transformer's vector group and tap changer, as a campus file and the
asset library write them (plan C13; the vector group since island plan I1).

``vector_group``
    ``Dyn11``: the HV winding (``D``, ``Y``, ``YN``), the LV winding (``d``,
    ``y``, ``yn``) and the clock number 0-11. Zig-zag windings are not
    written: the campus's fault model (``static/island_sc``) has none.

``tap``
    ``{changer, side, neutral, min, max, step_percent}``: ``changer`` is
    ``on_load`` or ``off_circuit``, ``side`` the winding the taps are on
    (``hv`` or ``lv``), the positions whole numbers with ``min <= neutral <=
    max``, and ``step_percent`` the voltage change per step, tagged and
    positive. A campus transformer also has its position ``pos`` (default
    ``neutral``, within ``min..max``); a library type has none.

The ratio of a unit at a tap position ``pos`` is, with ``t = pos -
neutral`` and ``s`` the step as a fraction, ``vn_hv (1 + t s) / vn_lv``
with the taps on the HV side and ``vn_hv / (vn_lv (1 + t s))`` on the LV
side: pandapower's ``tap_changer_type = "Ratio"``.

Plain Python only, like every other contract in ``schema/``.
"""
import re

from gridspine.schema.contracts import ContractError

#: The vector-group notation (module docstring).
VECTOR_GROUP = re.compile(r"(YN|Y|D)(yn|y|d)(1[01]|[0-9])")
TAP_CHANGERS = ("on_load", "off_circuit")
TAP_SIDES = ("hv", "lv")
_TAP_INTS = ("neutral", "min", "max")
_TAP_FIELDS = ("changer", "side", *_TAP_INTS, "step_percent")


def check_vector_group(where, group) -> int:
    """The clock number of ``group``; refused unless it is in the notation."""
    m = VECTOR_GROUP.fullmatch(group) if isinstance(group, str) else None
    if m is None:
        raise ContractError(f"{where} {group!r} is not a vector group such as 'Dyn11' or 'YNd5' "
                            "(HV D/Y/YN, LV d/y/yn, clock 0-11)")
    return int(m.group(3))


def _whole(where, v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ContractError(f"{where} must be a whole number, got {v!r}")
    return v


def check_tap(where, tap, tagged, with_pos: bool) -> dict:
    """The tap block ``tap`` checked (module docstring), as ``{changer, side,
    neutral, min, max, step_percent (float), pos}`` (``pos`` only with
    ``with_pos``). ``tagged(where, field, spec)`` reads and checks the tagged
    ``step_percent`` (the caller's tag rules); it must also be positive."""
    if not isinstance(tap, dict):
        raise ContractError(f"{where} must be a mapping, got {type(tap).__name__}")
    allowed = (*_TAP_FIELDS, "pos") if with_pos else _TAP_FIELDS
    unknown = sorted(set(tap) - set(allowed))
    if unknown:
        raise ContractError(f"{where}: unknown field(s) {unknown}; allowed {sorted(allowed)}")
    for f in _TAP_FIELDS:
        if f not in tap:
            raise ContractError(f"{where}: missing {f}")
    if tap["changer"] not in TAP_CHANGERS:
        raise ContractError(f"{where}.changer is {tap['changer']!r}; allowed {list(TAP_CHANGERS)}")
    if tap["side"] not in TAP_SIDES:
        raise ContractError(f"{where}.side is {tap['side']!r}; allowed {list(TAP_SIDES)}")
    out = {"changer": tap["changer"], "side": tap["side"]}
    for f in _TAP_INTS:
        out[f] = _whole(f"{where}.{f}", tap[f])
    if not out["min"] <= out["neutral"] <= out["max"]:
        raise ContractError(f"{where}: needs min <= neutral <= max, got {out['min']}, {out['neutral']}, {out['max']}")
    step = tagged(where, "step_percent", tap)
    if not step > 0:
        raise ContractError(f"{where}.step_percent must be positive, got {step}")
    out["step_percent"] = float(step)
    if with_pos:
        out["pos"] = _whole(f"{where}.pos", tap["pos"]) if "pos" in tap else out["neutral"]
        if not out["min"] <= out["pos"] <= out["max"]:
            raise ContractError(f"{where}.pos {out['pos']} is outside min..max ({out['min']}..{out['max']})")
    return out


def ratio(vn_hv_kv, vn_lv_kv, tap=None, offset=0) -> float:
    """The no-load ratio at ``offset`` steps from neutral (module docstring);
    ``tap`` is a checked block or None (a unit without taps)."""
    if tap is None or offset == 0:
        return vn_hv_kv / vn_lv_kv
    k = 1.0 + offset * tap["step_percent"] / 100.0
    return vn_hv_kv * k / vn_lv_kv if tap["side"] == "hv" else vn_hv_kv / (vn_lv_kv * k)
