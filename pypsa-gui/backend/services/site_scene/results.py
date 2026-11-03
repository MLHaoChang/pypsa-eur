"""
SC-03: native results projected onto scene buildings and process groups,
under the accepted D07 registry (`.scratch/site-scene-generation/d07/plan.md`,
owner decision D07-ACCEPT-1; GK A conditions in `issues/04-result-projection.md`).

Read only. Nothing here solves, simulates, writes, mints or marks the network
dirty, and nothing is interpolated: a value is the native one at an exact
native snapshot label, or it is unavailable with a reason.

  * **Gate.** `gate(ctx, n, run)` asks `harness.calculations.solve_matches_inputs`
    (the Results tab's and Check answers' own guard). It alone decides whether
    any number is shown; `_label` only names why not. When it passes, the live
    network holds the LP solve (the guard requires no AC PF or kept LOPF
    snapshot), so frames are read from the network itself. The run id is
    `services.dispatch_status.dispatch_digest` (str).
  * **Ports and signs.** Native signs are kept and named: Load and Link ports
    withdrawal positive; Generator, Store and StorageUnit injection positive. A
    Link lists only the ports whose `bus{i}` is set (PyPSA reports 0.0 for an
    unused one). A port's role comes from its parameters (port 0 and a negative
    `efficiency{i}` are inputs) and is nominal: `reversed` marks a value whose
    sign disagrees with it.
  * **Units.** A bus's declared `unit` is read raw: "t" counts in t/h and t;
    "", "None", NaN, "MW" and "MWh" count in MW and MWh unless the carrier
    catalogue says t (`process_products.bus_unit`); anything else is
    `unit_unknown`. Tonnes from MWh only through `carrier_catalog.bus_tonne_factor`.
  * **Time.** `rate` is instantaneous and never weighted. `window_energy` is
    Σ rate × snapshot_weightings.generators × period years
    (`period_utils.snapshot_weights`), the basis of `process_view`. Levels
    (Store `e`, StorageUnit `state_of_charge`) are states and never summed.
  * **Groups.** A bus that only member targets touch is internal and excluded;
    on any other bus `net_import` is the sum of member withdrawals, so transfers
    between members cancel. A total exists only for a single (carrier, unit).
    A member whose features are not all inside the group makes the group's
    numbers `group_partial_allocation`.

Every numerical leaf is `contract.quantity(value, unit, reason)`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from services import component_identity
from services.carrier_catalog import bus_tonne_factor
from services.process_products import bus_unit, is_product_bus
from services.site_scene.contract import SceneError, quantity

REASONS: tuple[str, ...] = (
    "project_not_open", "run_not_solved", "run_failed", "run_cancelled", "run_running", "results_not_current",
    "run_changed", "snapshot_not_in_run", "window_out_of_range", "unbound_target", "series_missing",
    "non_finite_value", "unit_unknown", "conversion_factor_missing", "vintage_rows_unsupported", "mixed_units",
    "group_partial_allocation", "member_unavailable")

# (static frame, time accessor, rate attr, level attr, sign) per bindable class (associations.ROLES).
_SINGLE = {
    "Generator": ("generators", "generators_t", "p", None, "injection_positive"),
    "Load": ("loads", "loads_t", "p", None, "withdrawal_positive"),
    "Store": ("stores", "stores_t", "p", "e", "injection_positive"),
    "StorageUnit": ("storage_units", "storage_units_t", "p", "state_of_charge", "injection_positive"),
}
_ROLE = {"Generator": "output", "Load": "input", "Store": "storage", "StorageUnit": "storage"}
_MAX_LINK_PORTS = 5
_MW_WORDS = {"", "none", "nan", "mw", "mwh"}
_EPS = 1e-9


# ── gate ────────────────────────────────────────────────────────────────────

def _label(state: dict) -> str:
    """Why the gate is closed, in the closed list's words. Never decides availability."""
    status = str(state.get("status") or "").lower()
    if status in ("running", "queued", "started", "abort_requested"):
        return "run_running"
    if status in ("aborted", "interrupted", "cancelled"):
        return "run_cancelled"
    if status in ("failed", "error"):
        return "run_failed"
    if status == "completed":
        return "results_not_current" if state.get("condition") == "optimal" else "run_failed"
    return "run_not_solved"


def gate(ctx, n, run: str | None) -> dict[str, Any]:
    """{current, reason, digest}. Caller holds the network lock; `n` is None when the project is not open."""
    if n is None:
        return {"current": False, "reason": "project_not_open", "digest": None}
    from harness import calculations
    from services.dispatch_status import dispatch_digest

    if not calculations.solve_matches_inputs(ctx):
        return {"current": False, "reason": _label(ctx.solver_state), "digest": None}
    digest = dispatch_digest(n)
    if run is not None and run != digest:
        return {"current": False, "reason": "run_changed", "digest": None}
    return {"current": True, "reason": None, "digest": digest}


# ── query ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Query:
    key: Any                       # the exact native snapshot label, or None (not in the run)
    label: Any                     # the asked timestep, as the response echoes it
    period: int | None
    window: tuple[Any, Any] | None  # (from, to) timestep labels within `period`


def _bad(message: str) -> SceneError:
    return SceneError("invalid_input", message)


def _timesteps(n) -> pd.Index:
    sns = n.snapshots
    return sns.get_level_values(-1) if isinstance(sns, pd.MultiIndex) else sns


def _parse_step(n, text: Any, what: str):
    if not isinstance(text, str) or not text.strip():
        raise _bad(f"{what}: give a snapshot label")
    steps = _timesteps(n)
    if pd.api.types.is_integer_dtype(steps.dtype):
        if not re.fullmatch(r"-?\d+", text.strip()):
            raise _bad(f"{what}: this model's snapshots are numbered; give a whole number")
        return int(text)
    try:
        stamp = pd.Timestamp(text)
    except (TypeError, ValueError):
        raise _bad(f"{what}: {text!r} is not a time") from None
    if stamp is pd.NaT:
        raise _bad(f"{what}: {text!r} is not a time")
    aware_index = getattr(steps, "tz", None) is not None
    if (stamp.tzinfo is not None) != aware_index:
        raise _bad(f"{what}: give the time {'with' if aware_index else 'without'} a time zone, as the model stores it")
    return stamp


def parse_query(n, *, snapshot: Any = None, period: Any = None, window_from: Any = None,
                window_to: Any = None) -> Query:
    """The request's snapshot and window as native labels. Malformed input raises `invalid_input` (422)."""
    multi = isinstance(n.snapshots, pd.MultiIndex)
    per = None
    if multi:
        if period is None or not re.fullmatch(r"\d+", str(period).strip()):
            raise _bad("This model has investment periods: give the period of the snapshot")
        per = int(period)
    elif period not in (None, ""):
        raise _bad("This model has no investment periods: leave the period out")
    step = _parse_step(n, snapshot, "Snapshot")
    if (window_from is None) != (window_to is None):
        raise _bad("A window needs both a start and an end")
    window = None
    if window_from is not None:
        window = (_parse_step(n, window_from, "Window start"), _parse_step(n, window_to, "Window end"))
        if window[0] > window[1]:
            raise _bad("The window starts after it ends")
    key = (per, step) if multi else step
    return Query(key=key if key in n.snapshots else None, label=step, period=per, window=window)


def _echo(label: Any) -> Any:
    return label.isoformat() if isinstance(label, pd.Timestamp) else label


# ── native reads ────────────────────────────────────────────────────────────

def _finite(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return 0.0 if v == 0 else v


def _units(n, bus: str) -> tuple[str | None, str | None]:
    """(rate unit, energy unit) of a bus; (None, None) when its declared unit is unknown."""
    raw = n.buses.at[bus, "unit"] if "unit" in n.buses.columns and bus in n.buses.index else ""
    text = "" if raw is None or (isinstance(raw, float) and math.isnan(raw)) else str(raw).strip()
    if text == "t" or (text.lower() in _MW_WORDS and bus_unit(n, bus) == "t"):
        return "t/h", "t"
    if text.lower() in _MW_WORDS:
        return "MW", "MWh"
    return None, None


def _carrier(n, bus: str) -> str | None:
    c = n.buses.at[bus, "carrier"] if "carrier" in n.buses.columns and bus in n.buses.index else None
    return str(c) if isinstance(c, str) and c else None


def _text(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


@dataclass
class _Port:
    port: Any
    bus: str
    role: str
    sign: str
    series: pd.Series | None       # native values over the snapshots; None when there is no column
    withdrawal: int                # +1 when the native sign is withdrawal positive, −1 when injection positive


def _frame_col(frame, name: str) -> pd.Series | None:
    if frame is None or getattr(frame, "empty", True) or name not in frame.columns:
        return None
    return frame[name]


def _ports(n, cls: str, name: str) -> list[_Port]:
    if cls == "Link":
        row = n.links.loc[name]
        out = []
        for i in range(_MAX_LINK_PORTS):
            bus = row.get(f"bus{i}")
            if not _text(bus):
                continue
            eff = 1.0 if i == 0 else row.get("efficiency" if i == 1 else f"efficiency{i}", 1.0)
            role = "input" if i == 0 or (_finite(eff) or 0.0) < 0 else "output"
            out.append(_Port(i, str(bus), role, "withdrawal_positive",
                             _frame_col(getattr(n.links_t, f"p{i}", None), name), +1))
        return out
    static, acc, attr, _level, sign = _SINGLE[cls]
    bus = str(getattr(n, static).at[name, "bus"])
    return [_Port("bus", bus, _ROLE[cls], sign, _frame_col(getattr(getattr(n, acc), attr, None), name),
                  +1 if sign == "withdrawal_positive" else -1)]


def _vintaged(n, cls: str, name: str) -> bool:
    """`<name>@<year>` rows beside the bound one: their sum is not served in SC-03 (owner choice B)."""
    index = n.components[cls].static.index
    return any(re.fullmatch(re.escape(name) + r"@\d{4}", str(i)) for i in index)


def _weights(n) -> pd.Series:
    from services.period_utils import snapshot_weights
    return snapshot_weights(n, "generators").reindex(n.snapshots).astype(float)


def _window_mask(n, q: Query) -> pd.Series | None:
    if q.window is None:
        return None
    steps = pd.Series(_timesteps(n), index=n.snapshots)
    mask = (steps >= q.window[0]) & (steps <= q.window[1])
    if q.period is not None:
        mask &= pd.Series(n.snapshots.get_level_values(0) == q.period, index=n.snapshots)
    return mask


# ── projection ──────────────────────────────────────────────────────────────

@dataclass
class _Ctx:
    n: Any
    gate: dict
    q: Query
    weights: pd.Series | None
    mask: pd.Series | None

    @property
    def closed(self) -> str | None:
        if not self.gate["current"]:
            return self.gate["reason"]
        if self.q.key is None:
            return "snapshot_not_in_run"
        return None

    def at(self, series: pd.Series | None, unit: str | None) -> dict:
        if self.closed:
            return quantity(None, unit, self.closed)
        if series is None:
            return quantity(None, unit, "series_missing")
        if unit is None:
            return quantity(None, None, "unit_unknown")
        v = _finite(series.get(self.q.key))
        return quantity(v, unit) if v is not None else quantity(None, unit, "non_finite_value")

    def window(self, series: pd.Series | None, unit: str | None) -> dict:
        if self.gate["current"] is False:
            return quantity(None, unit, self.gate["reason"])
        if series is None:
            return quantity(None, unit, "series_missing")
        if unit is None:
            return quantity(None, None, "unit_unknown")
        if not self.mask.any():
            return quantity(None, unit, "window_out_of_range")
        part = series.reindex(self.n.snapshots)[self.mask] * self.weights[self.mask]
        if not all(math.isfinite(x) for x in part):
            return quantity(None, unit, "non_finite_value")
        return quantity(_finite(part.sum()), unit)


def _reversed(role: str, leaf: dict) -> bool:
    if not leaf["available"] or role not in ("input", "output"):
        return False
    return leaf["value"] < -_EPS if role == "input" else leaf["value"] > _EPS


def _port_view(c: _Ctx, p: _Port, cls: str, weight: float | None) -> dict:
    n = c.n
    rate_unit, energy_unit = _units(n, p.bus)
    rate = c.at(p.series, rate_unit)
    out = {"port": p.port, "bus": p.bus, "carrier": _carrier(n, p.bus), "role": p.role,
           "reversed": cls == "Link" and _reversed(p.role, rate), "sign": p.sign, "rate": rate}
    if energy_unit == "MWh" and is_product_bus(n, p.bus):
        factor = bus_tonne_factor(n, p.bus)
        if not rate["available"]:
            out["rate_t"] = quantity(None, "t/h", rate["reason"])
        elif factor is None:
            out["rate_t"] = quantity(None, "t/h", "conversion_factor_missing")
        else:
            out["rate_t"] = quantity(_finite(rate["value"] * factor), "t/h")
    if weight is not None:
        out["allocated"] = (quantity(_finite(rate["value"] * weight), rate["unit"]) if rate["available"]
                            else quantity(None, rate["unit"], rate["reason"]))
    if c.mask is not None:
        out["window_energy"] = c.window(p.series, energy_unit)
    return out


def _target_view(c: _Ctx, assoc: dict, rows: list[dict]) -> dict:
    cls, uid = assoc["class"], assoc.get("uid")
    weights = [r.get("weight") for r in rows]
    if assoc.get("weight") is not None:
        allocation = {"basis": "explicit", "weight": assoc["weight"]}
    elif len({r["feature"] for r in rows}) > 1 and all(w is None for w in weights):
        allocation = {"basis": "shared", "features": sorted({r["feature"] for r in rows})}
    else:
        allocation = None
    out = {"class": cls, "uid": uid, "name": None, "allocation": allocation, "ports": []}
    if c.n is None:
        return out
    name = component_identity.name_of(c.n, cls, uid) if uid else None
    if name is None or cls not in _SINGLE and cls != "Link":
        out["reason"] = "unbound_target"
        return out
    out["name"] = name
    if _vintaged(c.n, cls, name):
        out["reason"] = "vintage_rows_unsupported"
        return out
    out["ports"] = [_port_view(c, p, cls, assoc.get("weight")) for p in _ports(c.n, cls, name)]
    if cls in ("Store", "StorageUnit"):
        static, acc, _attr, level, _sign = _SINGLE[cls]
        _rate_unit, energy_unit = _units(c.n, str(getattr(c.n, static).at[name, "bus"]))
        out["level"] = c.at(_frame_col(getattr(getattr(c.n, acc), level, None), name), energy_unit)
    return out


def _members(groups: dict[str, dict], gid: str) -> set[str]:
    """The non-group features under `gid`, recursively (groups are acyclic, `associations._check_groups`)."""
    out, stack, seen = set(), [gid], set()
    while stack:
        g = stack.pop()
        if g in seen:
            continue
        seen.add(g)
        for m in groups[g]["members"]:
            (stack.append(m) if m in groups else out.add(m))
    return out


def _bus_users(n) -> dict[str, set[tuple[str, str]]]:
    """Every native component with a port on each bus, over every class with a bus attribute (condition 9)."""
    users: dict[str, set[tuple[str, str]]] = {}
    for comp in n.components:
        static = comp.static
        if static is None or static.empty:
            continue
        cols = [c for c in static.columns if re.fullmatch(r"bus\d*", str(c))]
        for col in cols:
            for name, bus in static[col].items():
                if _text(bus):
                    users.setdefault(str(bus), set()).add((comp.name, str(name)))
    return users


def _unavailable_group(reason: str, member: str | None = None) -> dict:
    return {"boundary": [], "internal_buses": [], "total": quantity(None, None, reason), "unavailable_member": member}


def _group_view(c: _Ctx, gid: str, groups: dict, assocs: list[dict], by_target: dict) -> dict:
    if c.closed:
        return _unavailable_group(c.closed)
    features = _members(groups, gid)
    members: dict[tuple[str, str], str] = {}           # (class, name) → "Class:name"
    for a in assocs:
        if a["feature"] not in features:
            continue
        key = (a["class"], a.get("uid"))
        label = f"{a['class']}:{a.get('name_at_bind') or a.get('uid')}"
        name = component_identity.name_of(c.n, a["class"], a["uid"]) if a.get("uid") else None
        if name is None:
            return _unavailable_group("member_unavailable", label)
        label = f"{a['class']}:{name}"
        if not {r["feature"] for r in by_target[key]} <= features:
            return _unavailable_group("group_partial_allocation", label)
        if _vintaged(c.n, a["class"], name):
            return _unavailable_group("member_unavailable", label)
        members[(a["class"], name)] = label
    flows: dict[str, list[pd.Series]] = {}
    for (cls, name), label in members.items():
        for p in _ports(c.n, cls, name):
            if p.series is None:
                return _unavailable_group("member_unavailable", label)
            flows.setdefault(p.bus, []).append(p.series * p.withdrawal)
    users, member_keys = _bus_users(c.n), set(members)
    internal = sorted(b for b in flows if users.get(b, set()) <= member_keys)
    rows = []
    for bus in sorted(set(flows) - set(internal)):
        series = sum(flows[bus][1:], flows[bus][0])
        rate_unit, energy_unit = _units(c.n, bus)
        row = {"bus": bus, "carrier": _carrier(c.n, bus), "net_import": c.at(series, rate_unit)}
        if c.mask is not None:
            row["window_energy"] = c.window(series, energy_unit)
        rows.append(row)
    return {"boundary": rows, "internal_buses": internal, "total": _total(rows), "unavailable_member": None}


def _total(rows: list[dict]) -> dict:
    if not rows:
        return quantity(None, None, "mixed_units")
    bad = next((r["net_import"] for r in rows if not r["net_import"]["available"]), None)
    if bad is not None:
        return quantity(None, None, bad["reason"])
    if len({(r["carrier"], r["net_import"]["unit"]) for r in rows}) > 1:
        return quantity(None, None, "mixed_units")
    return quantity(_finite(sum(r["net_import"]["value"] for r in rows)), rows[0]["net_import"]["unit"])


def project(scene: dict[str, Any], n, *, gate: dict[str, Any], query: Query | None) -> dict[str, Any]:
    """The scene's results at one snapshot (and a window): per feature its targets' ports, per group its boundary."""
    q = query or Query(key=None, label=None, period=None, window=None)
    current = gate["current"] and n is not None
    c = _Ctx(n=n, gate=gate, q=q, weights=_weights(n) if current else None,
             mask=_window_mask(n, q) if current and q.window is not None else None)
    if q.window is not None and c.mask is None:
        c.mask = pd.Series(dtype=bool)                  # the gate is closed: window leaves name its reason
    assocs = list(scene.get("associations") or [])
    by_target: dict[tuple, list[dict]] = {}
    for a in assocs:
        by_target.setdefault((a["class"], a.get("uid")), []).append(a)
    features: dict[str, dict] = {}
    for a in assocs:
        features.setdefault(a["feature"], {"targets": []})["targets"].append(
            _target_view(c, a, by_target[(a["class"], a.get("uid"))]))
    groups = {g["id"]: g for g in scene.get("groups") or []}
    snap = None
    if current and q.key is not None:
        w = n.snapshot_weightings["generators"] if "generators" in n.snapshot_weightings else None
        years = 1.0
        if q.period is not None:
            years = float(n.investment_period_weightings["years"].get(q.period, float("nan")))
        snap = {"index": int(n.snapshots.get_loc(q.key)), "label": _echo(q.label), "period": q.period,
                "weight_generators_h": _finite(w.get(q.key)) if w is not None else None,
                "period_years": _finite(years)}
    window = None
    if q.window is not None:
        count = int(c.mask.sum()) if current else 0
        window = {"from": _echo(q.window[0]), "to": _echo(q.window[1]), "snapshots": count, "basis": "generators"}
    return {"run": {"digest": gate["digest"], "current": gate["current"], "reason": gate["reason"]},
            "snapshot": snap, "window": window, "features": features,
            "groups": {gid: _group_view(c, gid, groups, assocs, by_target) for gid in groups}}
