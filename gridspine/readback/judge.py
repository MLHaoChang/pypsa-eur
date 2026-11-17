"""
Automatic compliance judgment of a read-back test case (GC-07 part 3, TRGC 06/07).

``judge(frame, case, profile)`` judges one simulated or measured test, held in
a ``ChannelFrame``, against the grid-code profile's dynamic envelopes
(``templates/grid_codes.py``, ``DYNAMIC_ENVELOPES``). Each check is one row:

``{case_id, check, role, status, measured, limit, margin, at_s, unit,
quantity, clause, source, detail}``

``status`` is ``pass``, ``fail`` or ``unknown``; ``margin`` is positive
inside the limit; ``at_s`` is the time the measured value occurred. A check
whose envelope is not in the profile, or whose channel the case or the frame
does not carry, is ``unknown``, never ``pass``.

A row's ``role`` is ``obligation`` (what the plant must do) or ``validity``
(whether the test applied what the envelope covers). The case status reads
the obligation rows only: ``fail`` if one fails, else ``unknown`` if one is
unknown or there is none, else ``pass`` (``case_status``). So a case never
passes on a validity row alone.

The case: ``{id, kind, t_event, t_clear?, pre_window_s?,
channels: {role: channel name}}``. Kinds and their checks:

``lvrt`` / ``hvrt`` (fault ride-through; ``t_event`` the fault inception,
``t_clear`` its clearing)
    ``voltage_within_curve`` (validity): the voltage stays on or above the
    LVRT curve (on or below the HVRT curve) from ``t_event`` on; a trace on
    the curve passes; it fails at the first sample outside.
    ``stays_connected`` (obligation): the ``connected`` status channel is 1 at
    every sample from ``t_event`` while the voltage is within the curve. A
    trip after the voltage first leaves the curve is allowed. No status
    channel: unknown.
    ``reactive_current_rise`` (obligation): the reactive current change
    reaches ``target_pct`` of ``k_gain`` times the voltage deviation beyond
    the deadband (the deadband is subtracted) within ``rise_time_s`` of
    ``t_event`` and before ``t_clear``. The deviation is the pre-fault mean
    voltage less the mean voltage over the fault. ``iq`` is in pu of rated
    current, positive capacitive (over-excited): an undervoltage needs a
    positive change, an overvoltage a negative one, so the right magnitude
    with the wrong sign fails.
    ``active_power_recovery`` (obligation, LVRT): active power is back at
    ``recovery_pct`` of its pre-fault mean within ``time_s`` of ``t_clear``
    and stays there to the end of the record; a dip back below moves the
    recovery time to after the dip. ``active_power_ramp`` (obligation, when
    ``ramp_min_pu_per_s`` is set): the average rate from clearing to that
    recovery.

Channel roles: the voltage is ``v1`` (positive-sequence voltage at the PoC)
or ``v_ll_min`` (minimum phase-to-phase voltage at the PoC), one of them, in
pu; ``iq`` reactive current and ``p`` active power in pu; ``connected`` a 0/1
status. Pre-fault values are means over ``pre_window_s`` (default
``PRE_WINDOW_S``) before ``t_event``.

numpy only.
"""

from __future__ import annotations

import math

import numpy as np

from gridspine.readback.channels import ChannelFrame
from gridspine.schema.contracts import ContractError

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
OBLIGATION, VALIDITY = "obligation", "validity"
#: Default length of the pre-event averaging window, in s.
PRE_WINDOW_S = 0.1
VOLTAGE_ROLES = {
    "v1": "positive-sequence voltage at the PoC",
    "v_ll_min": "minimum phase-to-phase voltage at the PoC",
}
ROLES = frozenset({*VOLTAGE_ROLES, "iq", "p", "connected"})
KINDS = ("lvrt", "hvrt")
#: The unit each role must carry in the frame.
_ROLE_UNITS = {
    "v1": "pu",
    "v_ll_min": "pu",
    "iq": "pu",
    "p": "pu",
    "connected": "status",
}


def case_status(rows) -> str:
    """
    ``fail`` if an obligation row fails, else ``unknown`` if one is unknown
    or there is none, else ``pass``.
    """
    statuses = [r["status"] for r in rows if r["role"] == OBLIGATION]
    if FAIL in statuses:
        return FAIL
    if not statuses or UNKNOWN in statuses:
        return UNKNOWN
    return PASS


def curve_at(points, tau):
    """
    The ride-through curve at times ``tau`` (s from the event): linear
    between distinct times, right-continuous at a repeated time, the first
    value before the first point and the last after the last.
    """
    ts = np.array([pt[0] for pt in points], dtype=float)
    vs = np.array([pt[1] for pt in points], dtype=float)
    tau = np.atleast_1d(np.asarray(tau, dtype=float))
    k = np.searchsorted(ts, tau, side="right") - 1  # the last point at or before tau
    out = np.empty_like(tau)
    for i, (ki, ti) in enumerate(zip(k, tau)):
        if ki < 0:
            out[i] = vs[0]
        elif ki == len(ts) - 1:
            out[i] = vs[-1]
        else:  # ts[ki] <= ti < ts[ki + 1], so the two times differ
            out[i] = np.interp(ti, ts[ki : ki + 2], vs[ki : ki + 2])
    return out


class _Case:
    """A validated case bound to its frame."""

    def __init__(self, frame, case):
        if not isinstance(frame, ChannelFrame):
            raise ContractError(
                f"judge needs a ChannelFrame, got {type(frame).__name__}"
            )
        if not isinstance(case, dict):
            raise ContractError(f"a case must be a mapping, got {type(case).__name__}")
        self.id = case.get("id")
        if not isinstance(self.id, str) or not self.id.strip():
            raise ContractError(f"a case needs an id, got {self.id!r}")
        where = f"case {self.id!r}"
        self.where = where
        self.kind = case.get("kind")
        if self.kind not in KINDS:
            raise ContractError(f"{where}: kind {self.kind!r}; allowed {list(KINDS)}")
        self.frame = frame
        self.t_event = self._time(case, "t_event", required=True)
        if not frame.time[0] < self.t_event < frame.time[-1]:
            raise ContractError(
                f"{where}: t_event {self.t_event!r} s is not inside the record "
                f"{frame.time[0]!r}..{frame.time[-1]!r} s"
            )
        self.t_clear = self._time(
            case, "t_clear", required=self.kind in ("lvrt", "hvrt")
        )
        if (
            self.t_clear is not None
            and not self.t_event < self.t_clear <= frame.time[-1]
        ):
            raise ContractError(
                f"{where}: t_clear {self.t_clear!r} s must be after t_event and inside the record"
            )
        self.pre_window = self._window(case, "pre_window_s", PRE_WINDOW_S)
        channels = case.get("channels", {})
        if not isinstance(channels, dict):
            raise ContractError(
                f"{where}: channels must be a mapping of role to channel name"
            )
        bad = sorted(set(channels) - ROLES)
        if bad:
            raise ContractError(
                f"{where}: unknown channel role(s) {bad}; allowed {sorted(ROLES)}"
            )
        voltage = [r for r in VOLTAGE_ROLES if r in channels]
        if len(voltage) > 1:
            raise ContractError(
                f"{where}: give one voltage role, v1 or v_ll_min, not both"
            )
        self.voltage_role = voltage[0] if voltage else None
        self.channels = channels
        for role, name in channels.items():
            if not isinstance(name, str):
                raise ContractError(
                    f"{where}: channel for role {role!r} must be a name, got {name!r}"
                )
            unit = _ROLE_UNITS.get(role)
            if name in frame and unit is not None and frame.units[name] != unit:
                raise ContractError(
                    f"{where}: role {role!r} needs {unit!r}; channel {name!r} is in "
                    f"{frame.units[name]!r}"
                )

    def _time(self, case, key, required=False):
        v = case.get(key)
        if v is None:
            if required:
                raise ContractError(
                    f"{self.where}: {key} is required for kind {self.kind!r}"
                )
            return None
        if (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v < 0
        ):
            raise ContractError(
                f"{self.where}: {key} must be a finite time from 0, got {v!r}"
            )
        return float(v)

    def _window(self, case, key, default):
        v = self._time(case, key)
        if v == 0:
            raise ContractError(f"{self.where}: {key} must be above zero")
        return default if v is None else v

    def missing(self, *roles):
        """Why a role's channel cannot be read, or None."""
        for role in roles:
            if role not in self.channels:
                return f"the case maps no channel to role {role!r}"
            if self.channels[role] not in self.frame:
                return f"{self.frame.source} has no channel {self.channels[role]!r} ({role})"
        return None

    def values(self, role):
        return self.frame.channels[self.channels[role]]

    def pre_mean(self, role):
        t = self.frame.time
        sel = (t >= self.t_event - self.pre_window) & (t < self.t_event)
        if not sel.any():
            raise ContractError(
                f"{self.where}: no sample in the {self.pre_window!r} s before t_event"
            )
        return float(np.mean(self.values(role)[sel]))


def _row(
    case,
    check,
    role,
    envelope,
    status,
    measured=None,
    limit=None,
    margin=None,
    at_s=None,
    unit="",
    quantity="",
    detail="",
):
    return {
        "case_id": case.id,
        "check": check,
        "role": role,
        "status": status,
        "measured": measured,
        "limit": limit,
        "margin": margin,
        "at_s": at_s,
        "unit": unit,
        "quantity": quantity,
        "clause": (envelope or {}).get("clause", ""),
        "source": (envelope or {}).get("source", ""),
        "detail": detail,
    }


def _judged(
    case, check, env, measured, limit, margin, at_s, unit, detail="", quantity=""
):
    """An obligation row that passes when ``margin`` (positive inside the limit) is from 0."""
    status = PASS if margin >= 0 else FAIL
    return _row(
        case,
        check,
        OBLIGATION,
        env,
        status,
        measured,
        limit,
        margin,
        at_s,
        unit,
        quantity,
        detail,
    )


def _at_most(case, check, env, measured, limit, at_s, unit, detail="", quantity=""):
    """An obligation row that passes when ``measured`` is at most ``limit``."""
    return _judged(
        case,
        check,
        env,
        measured,
        limit,
        limit - measured,
        at_s,
        unit,
        detail,
        quantity,
    )


def _unknown(case, check, role, envelope, detail, key=None, **kw):
    if envelope is None:
        detail = f"{key} not in profile"
    return _row(case, check, role, envelope, UNKNOWN, detail=detail, **kw)


def _ride_through(case, profile):
    key = "lvrt_curve" if case.kind == "lvrt" else "hvrt_curve"
    env = profile.get(key)
    vrole = case.voltage_role or "v1"
    quantity = VOLTAGE_ROLES[vrole]
    why = case.missing(vrole)
    if env is None or why:
        return [
            _unknown(
                case, "voltage_within_curve", VALIDITY, env, why, key, quantity=quantity
            ),
            _unknown(
                case, "stays_connected", OBLIGATION, env, why, key, quantity=quantity
            ),
        ]
    t = case.frame.time
    after = t >= case.t_event
    v = case.values(vrole)[after]
    t_after = t[after]
    curve = curve_at(env["points"], t_after - case.t_event)
    margin = v - curve if case.kind == "lvrt" else curve - v
    outside = np.flatnonzero(margin < 0)
    if outside.size:
        k = int(outside[0])
        t_exit = float(t_after[k])
        vrow = _row(
            case,
            "voltage_within_curve",
            VALIDITY,
            env,
            FAIL,
            float(v[k]),
            float(curve[k]),
            float(margin[k]),
            t_exit,
            "pu",
            quantity,
            f"the voltage leaves the {key.split('_')[0].upper()} curve at {t_exit:g} s",
        )
    else:
        k = int(np.argmin(margin))
        t_exit = None
        vrow = _row(
            case,
            "voltage_within_curve",
            VALIDITY,
            env,
            PASS,
            float(v[k]),
            float(curve[k]),
            float(margin[k]),
            float(t_after[k]),
            "pu",
            quantity,
            "closest approach to the curve",
        )
    ended = (
        f"; the obligation ended at {t_exit:g} s when the voltage left the curve"
        if t_exit is not None
        else ""
    )
    why = case.missing("connected")
    if why:
        return [
            vrow,
            _row(
                case,
                "stays_connected",
                OBLIGATION,
                env,
                UNKNOWN,
                quantity=quantity,
                detail=why + ended,
            ),
        ]
    held = after & (t < t_exit) if t_exit is not None else after
    status = case.values("connected")
    if not np.isin(status, (0.0, 1.0)).all():
        raise ContractError(
            f"{case.where}: the connected channel {case.channels['connected']!r} must be 0 or 1"
        )
    if not held.any():
        return [
            vrow,
            _row(
                case,
                "stays_connected",
                OBLIGATION,
                env,
                UNKNOWN,
                quantity=quantity,
                detail="the voltage is outside the curve from fault inception; no sample to judge",
            ),
        ]
    tripped = np.flatnonzero(status[held] == 0)
    if tripped.size:
        at = float(t[held][tripped[0]])
        crow = _row(
            case,
            "stays_connected",
            OBLIGATION,
            env,
            FAIL,
            0.0,
            1.0,
            None,
            at,
            "status",
            quantity,
            f"disconnected at {at:g} s with the voltage within the curve",
        )
    else:
        crow = _row(
            case,
            "stays_connected",
            OBLIGATION,
            env,
            PASS,
            1.0,
            1.0,
            None,
            None,
            "status",
            quantity,
            "connected throughout" + ended,
        )
    return [vrow, crow]


def _reactive_current(case, profile):
    key = "reactive_current_injection"
    env = profile.get(key)
    vrole = case.voltage_role or "v1"
    quantity = VOLTAGE_ROLES[vrole]
    why = case.missing(vrole, "iq")
    if env is None or why:
        return [
            _unknown(
                case,
                "reactive_current_rise",
                OBLIGATION,
                env,
                why,
                key,
                unit="s",
                quantity=quantity,
            )
        ]
    t = case.frame.time
    v, iq = case.values(vrole), case.values("iq")
    v_pre, iq_pre = case.pre_mean(vrole), case.pre_mean("iq")
    fault = (t >= case.t_event) & (t < case.t_clear)
    dv = v_pre - float(np.mean(v[fault]))  # > 0 for an undervoltage
    beyond = abs(dv) - env["deadband_pu"]
    if beyond <= 0:
        return [
            _row(
                case,
                "reactive_current_rise",
                OBLIGATION,
                env,
                UNKNOWN,
                unit="s",
                quantity=quantity,
                detail=f"voltage deviation {abs(dv):g} pu is inside the {env['deadband_pu']:g} pu deadband; "
                "no reactive current is required",
            )
        ]
    sign = (
        1.0 if dv > 0 else -1.0
    )  # capacitive for an undervoltage, inductive for an overvoltage
    need = env["target_pct"] / 100 * env["k_gain"] * beyond
    reached = np.flatnonzero(fault & (sign * (iq - iq_pre) >= need))
    limit = env["rise_time_s"]
    side = "capacitive" if sign > 0 else "inductive"
    detail = (
        f"{side} change of {need:g} pu needed ({env['target_pct']:g} % of {env['k_gain']:g} x "
        f"{beyond:g} pu beyond the deadband; voltage deviation {dv:g} pu, the pre-fault mean over "
        f"{case.pre_window:g} s less the mean over the fault)"
    )
    if not reached.size:
        return [
            _row(
                case,
                "reactive_current_rise",
                OBLIGATION,
                env,
                FAIL,
                None,
                limit,
                None,
                None,
                "s",
                quantity,
                detail=detail + "; not reached before clearing",
            )
        ]
    at = float(t[reached[0]])
    rise = at - case.t_event
    return [
        _at_most(
            case,
            "reactive_current_rise",
            env,
            rise,
            limit,
            at,
            "s",
            detail=detail,
            quantity=quantity,
        )
    ]


def _p_recovery(case, profile):
    key = "active_power_recovery"
    env = profile.get(key)
    why = case.missing("p")
    if env is None or why:
        return [
            _unknown(case, "active_power_recovery", OBLIGATION, env, why, key, unit="s")
        ]
    t = case.frame.time
    p = case.values("p")
    p_pre = case.pre_mean("p")
    if p_pre <= 0:
        return [
            _row(
                case,
                "active_power_recovery",
                OBLIGATION,
                env,
                UNKNOWN,
                unit="s",
                detail=f"pre-fault active power {p_pre:g} pu is not above 0; nothing to recover to",
            )
        ]
    level = env["recovery_pct"] / 100 * p_pre
    after = np.flatnonzero(t >= case.t_clear)
    below = after[p[after] < level]
    limit = env["time_s"]
    detail = (
        f"{env['recovery_pct']:g} % of the pre-fault {p_pre:g} pu (mean over {case.pre_window:g} s) "
        f"is {level:g} pu"
    )
    if below.size and below[-1] == len(t) - 1:
        rows = [
            _row(
                case,
                "active_power_recovery",
                OBLIGATION,
                env,
                FAIL,
                None,
                limit,
                None,
                None,
                "s",
                detail=detail + "; still below at the end of the record",
            )
        ]
        t_rec = None
    else:
        k = int(below[-1]) + 1 if below.size else int(after[0])
        t_rec = float(t[k])
        took = t_rec - case.t_clear
        rows = [
            _at_most(
                case,
                "active_power_recovery",
                env,
                took,
                limit,
                t_rec,
                "s",
                detail=detail + "; held to the end of the record",
            )
        ]
    if "ramp_min_pu_per_s" in env:
        ramp_min = env["ramp_min_pu_per_s"]
        if t_rec is None:
            rows.append(
                _row(
                    case,
                    "active_power_ramp",
                    OBLIGATION,
                    env,
                    FAIL,
                    None,
                    ramp_min,
                    None,
                    None,
                    "pu/s",
                    detail="active power did not recover",
                )
            )
        elif t_rec <= case.t_clear:
            rows.append(
                _row(
                    case,
                    "active_power_ramp",
                    OBLIGATION,
                    env,
                    UNKNOWN,
                    None,
                    ramp_min,
                    unit="pu/s",
                    detail="already recovered at clearing; no ramp to measure",
                )
            )
        else:
            p_clear = float(np.interp(case.t_clear, t, p))
            ramp = (level - p_clear) / (t_rec - case.t_clear)
            rows.append(
                _judged(
                    case,
                    "active_power_ramp",
                    env,
                    ramp,
                    ramp_min,
                    ramp - ramp_min,
                    t_rec,
                    "pu/s",
                    detail="average rate from clearing to recovery",
                )
            )
    return rows


def judge(frame: ChannelFrame, case: dict, profile: dict) -> dict:
    """``{case_id, kind, status, rows}`` for one case (see the module docstring)."""
    c = _Case(frame, case)
    if not isinstance(profile, dict):
        raise ContractError(
            f"a profile must be a mapping, got {type(profile).__name__}"
        )
    if c.kind in ("lvrt", "hvrt"):
        rows = _ride_through(c, profile) + _reactive_current(c, profile)
        if c.kind == "lvrt":
            rows += _p_recovery(c, profile)
    return {"case_id": c.id, "kind": c.kind, "status": case_status(rows), "rows": rows}
