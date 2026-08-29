"""
Pre-run consistency checks for the three simulation modes.

Single source of truth: validate_for_run(n, solver_config) -> list[Issue].
Used in two places:
  1. /api/simulation/preflight — manual "Validate" button before Run.
  2. solver_service.run_simulation — refuses to start if any error-severity
     issues are present, streams the messages into the live log so the user
     sees why their click did nothing.

Severity policy: error = PyPSA will fail on this; warning = solve will run but
the result is probably nonsense (e.g. all-zero costs, zero s_nom). Errors
abort the run; warnings only inform.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

import pandas as pd

from services.carrier_catalog import CARRIER_CATALOG

# Severity literal kept narrow on purpose — anything beyond error/warning
# pushes UX complexity onto every callsite for marginal value.
Severity = Literal["error", "warning"]


@dataclass
class Issue:
    severity: Severity
    code: str               # short stable identifier; the UI may filter on this
    component_class: str    # "Bus" / "Line" / ... or "" for network-level
    name: str               # component name; "" for network-level
    message: str            # human-readable, complete sentence

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── tiny helpers ─────────────────────────────────────────────────────────────

def _is_finite_pos(x: Any) -> bool:
    try:
        v = float(x)
        return math.isfinite(v) and v > 0
    except Exception:
        return False


def _is_finite_nonneg(x: Any) -> bool:
    try:
        v = float(x)
        return math.isfinite(v) and v >= 0
    except Exception:
        return False


def _is_finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _pretty(x: Any) -> str:
    """
    Numpy scalars repr as 'np.float64(0.0)'; coerce to plain Python so user
    messages aren't littered with the type wrapper. Falls back to repr() for
    anything we can't coerce.
    """
    if x is None:
        return "None"
    try:
        # numpy / pandas scalars expose .item(); plain types pass through
        v = x.item() if hasattr(x, "item") and not isinstance(x, (str, bytes)) else x
    except Exception:
        v = x
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "+inf" if v > 0 else "-inf"
        return f"{v:g}"
    return repr(v)


def _err(code: str, comp: str, name: str, msg: str) -> Issue:
    return Issue("error", code, comp, name, msg)


def _warn(code: str, comp: str, name: str, msg: str) -> Issue:
    return Issue("warning", code, comp, name, msg)


# Carrier names that imply combustion of fossil carbon. Word-boundary matched
# (see `_contains_word` below), lower-cased, because carrier naming is
# free-form.
#
# The exclusions below are NOT "carriers that happen to look clean" — they are
# carriers whose carbon is biogenic (never fossil to begin with) or already
# accounted for upstream of combustion, so a co2_emissions=0 on the carrier
# itself is correct, not a gap:
#   - `biogas` / `biomethane`: biogenic CO2, conventionally counted as zero.
#   - `methanol`: in this repo's PyPSA-Eur sector-coupled networks, methanol
#     is SYNTHETIC (`methanolisation` — built from H2 + captured CO2), so its
#     carbon is accounted for at capture, not at the burn. `CCGT methanol` /
#     `OCGT methanol` / `allam methanol` would otherwise trip on the `ccgt`/
#     `ocgt` substrings despite not being fossil gas plants.
# A warning that cries wolf is one users learn to dismiss — that's the same
# cost whether the false positive comes from `gas` matching `biogas` or from
# `ccgt` matching `CCGT methanol`.
#
# 2026-07-31 review (Finding 2): bare substring matching (`x in c`) ALSO
# produced false positives that have nothing to do with biogas/methanol —
# `oil` inside `boiler`, `gas` inside `gasification`, `coal` inside
# `charcoal`, `methanol` itself was matched as a bare substring in the
# exclusion list. Demonstrated false positives (all must be False now):
# 'biomass boiler', 'electric boiler', 'hydrogen boiler', 'residential rural
# biomass boiler', 'biomass gasification', 'syngas', 'charcoal', 'waste
# heat'. Fixed by requiring every keyword/exclusion to match a whole word —
# see `_contains_word`. Three of the eight needed a deliberate call beyond
# "just add boundaries":
#   - `charcoal`: no special-case needed. Word-boundary matching alone stops
#     `coal` from matching inside it (the `c` before `coal` in `charcoal` has
#     no boundary), which is also the physically correct answer — charcoal is
#     biogenic, not fossil.
#   - `syngas`: NOT added as a keyword, deliberately left unmatched when
#     bare. Syngas from coal gasification is fossil; syngas from biomass
#     gasification is not — the bare name alone doesn't say which, and this
#     module doesn't invent an emission factor (C1). A carrier that DOES
#     qualify its origin, e.g. `coal syngas` / `lignite syngas`, is still
#     caught via that qualifying keyword's own word-boundary match. This is
#     the same reasoning as the biogas/methanol exclusions: an unqualified
#     guess would either false-negative on real fossil syngas or
#     false-positive on biomass syngas, and the latter is exactly the
#     "cry wolf" cost this module exists to avoid.
#   - `waste heat`: added to `_FOSSIL_PHRASE_EXCLUDE`. Word-boundary matching
#     does NOT fix this one — `waste` genuinely is its own word here, it's
#     just describing a byproduct heat stream (e.g. CHP heat recovery /
#     district-heating offtake), not a fuel. `waste` alone (municipal solid
#     waste as a combusted fuel) must keep matching.
_FOSSIL_KEYWORDS = ("gas", "coal", "lignite", "oil", "diesel", "peat", "waste",
                    "ccgt", "ocgt", "methane")
_FOSSIL_EXCLUDE = ("biogas", "biomethane", "methanol")
_FOSSIL_PHRASE_EXCLUDE = ("waste heat",)

_FOSSIL_KEYWORD_RE = [re.compile(rf"\b{re.escape(k)}\b") for k in _FOSSIL_KEYWORDS]
_FOSSIL_EXCLUDE_RE = [re.compile(rf"\b{re.escape(k)}\b") for k in _FOSSIL_EXCLUDE]


def _contains_word(carrier_lower: str, patterns: list[re.Pattern[str]]) -> bool:
    """Whole-word match against any of `patterns` — see the module comment above."""
    return any(p.search(carrier_lower) for p in patterns)


def _looks_fossil(carrier: str) -> bool:
    c = (carrier or "").lower()
    if any(phrase in c for phrase in _FOSSIL_PHRASE_EXCLUDE):
        return False
    if _contains_word(c, _FOSSIL_EXCLUDE_RE):
        return False
    return _contains_word(c, _FOSSIL_KEYWORD_RE)


# ── network-level checks (run for every mode) ────────────────────────────────

def _check_network_level(n) -> list[Issue]:
    out: list[Issue] = []
    if len(n.snapshots) == 0:
        out.append(_err("snapshots_empty", "", "",
            "Network has no snapshots — set Model Horizon before running."))
    if len(n.buses) == 0:
        out.append(_err("buses_empty", "", "",
            "Network has no buses — add at least one bus before running."))
    # snapshot_weightings: PyPSA defaults to 1; we only flag NaN/inf, which the
    # solver chokes on but the UI lets users introduce via spreadsheet import.
    try:
        sw = n.snapshot_weightings
        if not sw.empty:
            for col in sw.columns:
                bad = sw[col][~sw[col].apply(_is_finite)]
                if len(bad) > 0:
                    out.append(_err("snapshot_weighting_nan", "", col,
                        f"snapshot_weightings['{col}'] has {len(bad)} NaN/inf value(s)."))
    except Exception:
        pass  # weights are optional — not having them is fine
    return out


def _check_carrier_emissions(n) -> list[Issue]:
    """
    A fossil-looking carrier with no CO2 intensity makes every emissions figure
    zero, silently. Ungated on purpose: the two pre-existing guards fire only
    when co2_price > 0 or a global constraint exists, and a real project had
    neither — which is exactly how a 300 MW gas plant reported 0 tCO2.
    """
    out: list[Issue] = []
    if n.generators.empty:
        return out
    # NOTE: do NOT also short-circuit on `n.carriers.empty`. A network
    # imported via n.import_from_netcdf / import_from_csv_folder
    # (routers/io.py) gets no ensure_carrier pass over its generators, so an
    # imported fossil-carrier generator with zero Carrier rows is exactly
    # the least-known-data case this warning exists for. The per-carrier
    # fallback below (`c in n.carriers.index else 0.0`) already degrades
    # correctly when a row is missing OR the whole table is empty — an
    # empty n.carriers has an empty .index, so `c in n.carriers.index` is
    # simply always False and every used carrier falls back to 0.0.
    if n.carriers.empty or "co2_emissions" not in n.carriers.columns:
        used = sorted({str(c) for c in n.generators["carrier"].unique() if _looks_fossil(str(c))})
        intensities = {c: 0.0 for c in used}
    else:
        used = sorted({str(c) for c in n.generators["carrier"].unique() if _looks_fossil(str(c))})
        intensities = {
            c: float(n.carriers.at[c, "co2_emissions"]) if c in n.carriers.index else 0.0
            for c in used
        }
    for carrier, value in intensities.items():
        if value > 0:
            continue
        suggested = CARRIER_CATALOG.get(carrier, {}).get("co2_emissions", 0.0)
        hint = (f" The catalog value for '{carrier}' is {suggested} tCO2/MWh."
                if suggested else "")
        out.append(_warn("carrier_zero_co2", "Carrier", carrier,
            f"Carrier '{carrier}' looks like a fossil fuel but has co2_emissions = 0, "
            f"so every emissions figure for it is zero.{hint}"))
    return out


def _check_bus_v_nom(n) -> list[Issue]:
    out: list[Issue] = []
    for name in n.buses.index:
        v = n.buses.at[name, "v_nom"] if "v_nom" in n.buses.columns else None
        if not _is_finite_pos(v):
            out.append(_err("bus_v_nom_invalid", "Bus", str(name),
                f"v_nom must be > 0 (got {_pretty(v)})."))
    return out


def _check_bus_references(n) -> list[Issue]:
    """Every component bus reference must point at an existing bus."""
    out: list[Issue] = []
    bus_set = set(n.buses.index.astype(str))

    def _check(df: pd.DataFrame, comp: str, cols: list[str]) -> None:
        for col in cols:
            if col not in df.columns:
                continue
            for name in df.index:
                ref = str(df.at[name, col])
                if ref == "" or ref == "nan":
                    out.append(_err("bus_ref_missing", comp, str(name),
                        f"{col} is empty."))
                elif ref not in bus_set:
                    out.append(_err("bus_ref_unknown", comp, str(name),
                        f"{col}='{ref}' does not match any bus."))

    _check(n.generators,    "Generator",    ["bus"])
    _check(n.loads,         "Load",         ["bus"])
    _check(n.storage_units, "StorageUnit",  ["bus"])
    _check(n.stores,        "Store",        ["bus"])
    _check(n.lines,         "Line",         ["bus0", "bus1"])
    _check(n.transformers,  "Transformer",  ["bus0", "bus1"])
    # Links may be multi-bus (bus2, bus3, …) — only flag the ones where the
    # corresponding efficiency is non-zero, otherwise a blank bus2 is normal.
    if not n.links.empty:
        for name in n.links.index:
            for col in ("bus0", "bus1"):
                ref = str(n.links.at[name, col])
                if ref == "" or ref == "nan":
                    out.append(_err("bus_ref_missing", "Link", str(name),
                        f"{col} is empty."))
                elif ref not in bus_set:
                    out.append(_err("bus_ref_unknown", "Link", str(name),
                        f"{col}='{ref}' does not match any bus."))
            # Optional secondary buses (bus2 / bus3 / …). When the slot is
            # empty PyPSA simply ignores the corresponding efficiency_n —
            # so we don't flag the default efficiency_n=1.0 on an empty
            # bus_n as a misconfiguration (it isn't). Only flag a
            # populated bus_n that doesn't match any known bus.
            for col in ("bus2", "bus3", "bus4"):
                if col not in n.links.columns:
                    continue
                ref = str(n.links.at[name, col])
                if ref and ref != "nan" and ref not in bus_set:
                    out.append(_err("bus_ref_unknown", "Link", str(name),
                        f"{col}='{ref}' does not match any bus."))
    return out


def _line_x_check(df: pd.DataFrame, comp: str) -> list[Issue]:
    out: list[Issue] = []
    if df.empty or "x" not in df.columns:
        return out
    for name in df.index:
        x = df.at[name, "x"]
        if not _is_finite_pos(x):
            out.append(_err(f"{comp.lower()}_x_invalid", comp, str(name),
                f"reactance x must be > 0 (got {_pretty(x)}). Required for power-flow solves."))
    return out


# ── load p_set coverage (pf / lopf all need finite values) ──────────────────

def _check_loads_p_set(n) -> list[Issue]:
    """
    A load contributes p_set either statically (n.loads.p_set) or via a
    time-varying column in n.loads_t.p_set. Check both: the value must exist
    and be finite. PyPSA defaults p_set to 0 so the common failure mode is
    NaN introduced by spreadsheet import.
    """
    out: list[Issue] = []
    if n.loads.empty:
        return out
    static = n.loads["p_set"] if "p_set" in n.loads.columns else None
    ts = n.loads_t.p_set if hasattr(n.loads_t, "p_set") else pd.DataFrame()
    for name in n.loads.index:
        if name in ts.columns:
            col = ts[name]
            bad = col[~col.apply(_is_finite)]
            if len(bad) > 0:
                out.append(_err("load_p_set_nan", "Load", str(name),
                    f"time-varying p_set has {len(bad)} NaN/inf value(s)."))
        else:
            v = static.loc[name] if static is not None else None
            if v is None or not _is_finite(v):
                out.append(_err("load_p_set_invalid", "Load", str(name),
                    f"static p_set must be finite (got {_pretty(v)})."))
    return out


# ── PF mode ──────────────────────────────────────────────────────────────────

def _check_pf(n) -> list[Issue]:
    out: list[Issue] = []
    out += _line_x_check(n.lines, "Line")
    out += _line_x_check(n.transformers, "Transformer")
    out += _check_loads_p_set(n)
    # Slack: every connected sub-network needs at least one slack-controlled
    # bus. Without running n.determine_network_topology() we approximate with
    # the cheapest signal: at least one bus marked Slack overall. If users
    # have multiple disconnected components it'll surface as a PyPSA error,
    # but the most common bug is "zero slacks anywhere" which we catch.
    if "control" in n.buses.columns:
        slacks = n.buses[n.buses["control"].astype(str).str.lower() == "slack"]
        if len(slacks) == 0 and len(n.buses) > 0:
            out.append(_err("pf_no_slack", "Bus", "",
                "Non-linear PF needs at least one bus with control='Slack'."))
    # Generators feeding PF: must have control set (PQ/PV/Slack).
    if not n.generators.empty and "control" in n.generators.columns:
        for name in n.generators.index:
            ctrl = str(n.generators.at[name, "control"]).strip()
            if ctrl not in ("PQ", "PV", "Slack"):
                out.append(_err("gen_control_invalid", "Generator", str(name),
                    f"control must be PQ, PV, or Slack (got '{ctrl}') for non-linear PF."))
    return out


# ── LOPF mode ────────────────────────────────────────────────────────────────

def _check_extendable_bounds(
    df: pd.DataFrame, comp: str, prefix: str, capital_cost_required: bool,
) -> list[Issue]:
    """
    *_extendable assets need finite *_nom_min / *_nom_max with min < max,
    and a positive capital_cost (otherwise the solver picks max_nom for free).
    `prefix` is 'p_nom' / 's_nom' / 'e_nom'.
    """
    out: list[Issue] = []
    ext_col = f"{prefix}_extendable"
    if df.empty or ext_col not in df.columns:
        return out
    min_col, max_col = f"{prefix}_min", f"{prefix}_max"
    for name in df.index:
        if not bool(df.at[name, ext_col]):
            v = df.at[name, prefix] if prefix in df.columns else None
            if not _is_finite_pos(v):
                out.append(_err(f"{comp.lower()}_{prefix}_invalid", comp, str(name),
                    f"{prefix} must be > 0 when {ext_col}=False (got {_pretty(v)})."))
            continue
        # Extendable: bounds must be finite and ordered
        lo = df.at[name, min_col] if min_col in df.columns else 0
        hi = df.at[name, max_col] if max_col in df.columns else float("inf")
        if not (_is_finite_nonneg(lo) and _is_finite(hi) and float(hi) > float(lo)):
            out.append(_err(f"{comp.lower()}_{prefix}_bounds", comp, str(name),
                f"extendable: need finite {min_col} < {max_col} (got {_pretty(lo)} / {_pretty(hi)})."))
        if capital_cost_required and "capital_cost" in df.columns:
            cc = df.at[name, "capital_cost"]
            # Accept overnight_cost > 0 as a substitute: solver_service's
            # _apply_modelling_assumptions recomputes capital_cost from
            # overnight_cost × annuity(discount_rate, lifetime) at solve time,
            # so either field carries the economic input the LP needs.
            oc = df.at[name, "overnight_cost"] if "overnight_cost" in df.columns else None
            if not _is_finite_pos(cc) and not _is_finite_pos(oc):
                out.append(_err(f"{comp.lower()}_no_capital_cost", comp, str(name),
                    f"extendable assets need capital_cost > 0 OR overnight_cost > 0 "
                    f"(got capital_cost={_pretty(cc)}, overnight_cost={_pretty(oc)}); "
                    "otherwise the solver builds free up to the max."))
    return out


def _check_efficiency(
    df: pd.DataFrame, comp: str, col: str,
    *, lower: float | None = 0.0, upper: float | None = 1.0,
) -> list[Issue]:
    """
    Validate an efficiency column.

    Three independently-overridable bounds:
      * `lower`  — exclusive lower bound (default 0). Pass None to allow
                   negative values. Used for Link.efficiency2/3/4 on
                   multi-output Links where eff_n < 0 is the canonical
                   PyPSA idiom for "this link EXTRACTS from bus_n" —
                   heat pumps with a low-grade heat source, CHPs with a
                   cooling sink, regen drives reclaiming braking energy.
      * `upper`  — inclusive upper bound (default 1). Pass None to allow
                   COP > 1 on heat-pump Links.
      * The value must always be finite (NaN / inf rejected).

    For Generators and StorageUnits the physical interpretation is
    fuel→electricity (or store↔dispatch round-trip), bounded by
    thermodynamics at ≤ 1. For Links the "efficiency" is a generic
    conversion coefficient — primary efficiency stays > 0 (don't reverse
    the LP's natural direction), but multi-output efficiency2/3/4 can be
    any non-zero finite value.
    """
    out: list[Issue] = []
    if df.empty or col not in df.columns:
        return out
    for name in df.index:
        v = df.at[name, col]
        if not _is_finite(v):
            out.append(_err(f"{comp.lower()}_efficiency_invalid", comp, str(name),
                f"{col} must be finite (got {_pretty(v)})."))
            continue
        fv = float(v)
        if lower is not None and fv <= lower:
            bound_msg = f"{col} must be > {lower:g}"
            out.append(_err(f"{comp.lower()}_efficiency_invalid", comp, str(name),
                f"{bound_msg} (got {_pretty(v)})."))
            continue
        if upper is not None and fv > upper:
            range_lower = "0" if lower is None else f"{lower:g}"
            out.append(_err(f"{comp.lower()}_efficiency_invalid", comp, str(name),
                f"{col} must satisfy {range_lower} < {col} ≤ {upper:g} (got {_pretty(v)})."))
    return out


def _check_transformer_types(n) -> list[Issue]:
    """
    Transformer.type is a foreign key into n.transformer_types. Any value
    that isn't registered there crashes the solver with
    `The type(s) X do(es) not exist in n.transformer_types`.

    solver_service auto-strips unrecognised types at solve time (so the run
    succeeds), but we still surface a warning here so the user knows the
    transformer's r/x/s_nom will be taken from the explicit columns rather
    than from any type-template they thought they'd selected.
    """
    out: list[Issue] = []
    if n.transformers.empty or "type" not in n.transformers.columns:
        return out
    try:
        valid = set(n.transformer_types.index)
    except Exception:
        valid = set()
    types = n.transformers["type"].fillna("")
    for name in types.index:
        t = str(types.loc[name])
        if t and t not in valid:
            out.append(_warn("transformer_type_unregistered", "Transformer", str(name),
                f"type='{t}' is not in n.transformer_types — it will be stripped "
                f"at solve time and the explicit s_nom/x values used instead."))
    return out


def _check_pmin_pmax(n) -> list[Issue]:
    """
    p_min_pu > p_max_pu makes the dispatch interval empty → infeasible.
    Checks the STATIC columns only; time-varying profiles where this swap
    happens at one snapshot are caught by PyPSA itself at solve time.
    """
    out: list[Issue] = []
    for comp, df, lo, hi in (
        ("Generator", n.generators, "p_min_pu", "p_max_pu"),
        ("Link",      n.links,      "p_min_pu", "p_max_pu"),
        ("Store",     n.stores,     "e_min_pu", "e_max_pu"),
    ):
        if df.empty or lo not in df.columns or hi not in df.columns:
            continue
        for name in df.index:
            l, h = df.at[name, lo], df.at[name, hi]
            if _is_finite(l) and _is_finite(h) and float(l) > float(h):
                out.append(_err(f"{comp.lower()}_min_gt_max", comp, str(name),
                    f"{lo}={_pretty(l)} > {hi}={_pretty(h)} — empty dispatch interval, LP infeasible."))
    return out


def _check_unbounded_costs(n) -> list[Issue]:
    """
    Negative cost + extendable + unbounded *_nom_max = LP unbounded.
    Solver returns infeasible/unbounded; user sees a cryptic exception. Catch
    here. The negative-cost case usually shows up via the curtailment-cost
    discount trick we apply at solve time, but a raw user-entered negative
    marginal_cost on an extendable generator hits the same trap.
    """
    out: list[Issue] = []
    for comp, df, prefix in (
        ("Generator",   n.generators,    "p_nom"),
        ("Link",        n.links,         "p_nom"),
        ("StorageUnit", n.storage_units, "p_nom"),
        ("Store",       n.stores,        "e_nom"),
    ):
        if df.empty:
            continue
        ext_col, max_col = f"{prefix}_extendable", f"{prefix}_max"
        if ext_col not in df.columns:
            continue
        for name in df.index:
            if not bool(df.at[name, ext_col]):
                continue
            hi = df.at[name, max_col] if max_col in df.columns else float("inf")
            if _is_finite(hi):
                continue  # bounded — safe even if cost is negative
            # Capacity is unbounded; check both cost knobs for negative values.
            for col in ("capital_cost", "marginal_cost"):
                if col in df.columns:
                    v = df.at[name, col]
                    if _is_finite(v) and float(v) < 0:
                        out.append(_err(f"{comp.lower()}_unbounded_neg_cost", comp, str(name),
                            f"{col}={_pretty(v)} (negative) AND extendable AND {max_col} is +inf → "
                            "LP is unbounded. Set a finite {max_col} or non-negative {col}."))
    return out


def _check_modelling_assumptions(n, solver_config) -> list[Issue]:
    """
    Sanity-checks for the five solve-time modelling knobs we added:
    discount_rate, default_lifetime, co2_price, voll, investment_periods.

    Each issue here is a warning — the LP solves fine, but the knob the user
    twiddled has no effect on the current network. Helps avoid the "I set
    discount rate to 10 % and nothing changed" debug spiral.
    """
    out: list[Issue] = []
    cfg = solver_config

    # CO2 price > 0 but no carrier has co2_emissions > 0 → no fossil to charge.
    if getattr(cfg, "co2_price", 0.0) > 0.0:
        co2 = n.carriers.get("co2_emissions", pd.Series(dtype=float)) if not n.carriers.empty else pd.Series(dtype=float)
        if co2.empty or not (co2.fillna(0.0) > 0).any():
            out.append(_warn("co2_price_no_fossil", "", "",
                f"co2_price={cfg.co2_price} €/tCO2 set, but no carrier has co2_emissions > 0. "
                "Surcharge applies to nothing."))

    # VOLL > 0 but no loads → slack generators are pointless overhead.
    if getattr(cfg, "voll", 0.0) > 0.0:
        if n.loads.empty:
            out.append(_warn("voll_no_loads", "", "",
                f"voll={cfg.voll} €/MWh set, but the network has no loads. "
                "Slack generators will be added but never dispatched."))

    # investment_periods configured but no extendable asset → multi-period LP
    # has no decision variables differentiating across periods.
    if getattr(cfg, "multi_investment_periods", False) and getattr(cfg, "investment_periods", None):
        any_ext = False
        for df, col in (
            (n.generators,    "p_nom_extendable"),
            (n.links,         "p_nom_extendable"),
            (n.storage_units, "p_nom_extendable"),
            (n.stores,        "e_nom_extendable"),
            (n.lines,         "s_nom_extendable"),
            (n.transformers,  "s_nom_extendable"),
        ):
            if not df.empty and col in df.columns and bool(df[col].any()):
                any_ext = True
                break
        if not any_ext:
            out.append(_warn("multi_period_no_extendable", "", "",
                "investment_periods configured but no asset is extendable. "
                "Multi-period LP has no investment decision to make."))

    # Discount-rate recompute relies on overnight_cost being set on assets.
    # If no asset has it, the annuity calc touches nothing (the user-typed
    # capital_cost values stay).
    if abs(getattr(cfg, "discount_rate", 0.07) - 0.07) > 1e-6:
        has_overnight = False
        for df in (n.generators, n.links, n.storage_units, n.stores, n.lines, n.transformers):
            if not df.empty and "overnight_cost" in df.columns:
                if df["overnight_cost"].fillna(0).abs().sum() > 0:
                    has_overnight = True
                    break
        if not has_overnight:
            out.append(_warn("discount_rate_no_effect", "", "",
                f"discount_rate={cfg.discount_rate} set, but no asset has overnight_cost. "
                "The annuity recompute has no effect — capital_cost values stay as typed."))
    return out


def _check_sclopf(n, cfg) -> list[Issue]:
    """
    SCLOPF-specific checks. Fires only when the user enabled sclopf.

    - Hard error if no branch was selected (the resolver returned an empty
      set) — would just be a plain LOPF, so the user almost certainly
      mis-configured the panel.
    - Warn when the contingency set is very large (PyPSA's LODF + LP
      grows linearly with it). 200 is a soft threshold based on the LP
      cost of (K branches × N snapshots) extra rows.
    - Warn if every passive branch is in the set on a network that looks
      radial — at least one bridge will trigger islanding and the LP will
      be infeasible at that contingency.
    """
    out: list[Issue] = []
    if not getattr(cfg, "sclopf", False):
        return out
    # PyPSA's `optimize_security_constrained` doesn't accept the
    # `transmission_losses` kwarg — the secant loss formulation lives on
    # the plain optimize path. Combining the two would silently drop the
    # loss model. Block at preflight so the user picks one or the other.
    if getattr(cfg, "transmission_losses", False):
        out.append(_err("sclopf_with_transmission_losses", "", "",
            "SCLOPF doesn't support `transmission_losses=True` — PyPSA's "
            "secant-loss formulation isn't wired into the BODF-based "
            "contingency LP. Disable one of the two for this run."))
    # Lazy import to avoid a circular dependency at validation-service load.
    from services.solver_service import resolve_branch_outages
    outages = resolve_branch_outages(n, cfg)
    if not outages:
        out.append(_err("sclopf_no_branches", "", "",
            "SCLOPF is enabled but no branches resolved from the selection "
            "(all-lines / all-transformers / voltage threshold / extras). "
            "Enable at least one source — otherwise this is just a plain LOPF."))
        return out
    if len(outages) > 200:
        out.append(_warn("sclopf_large_contingency_set", "", "",
            f"SCLOPF will consider {len(outages)} contingencies. Each adds "
            "constraints per snapshot — solve time grows roughly linearly. "
            "Consider raising the voltage threshold or unchecking 'include all' "
            "and listing critical branches explicitly."))
    # Heuristic radial check: a radial subnetwork has #branches == #buses − 1.
    # If the user picked ALL passive branches, any radial subnet will have at
    # least one bridge whose outage islands part of the system → infeasible.
    try:
        n.determine_network_topology()
        for sn in n.sub_networks.obj:
            n_buses = len(sn.buses())
            n_branches = len(sn.branches())
            if n_branches > 0 and n_branches <= n_buses - 1:
                out.append(_warn("sclopf_radial_subnet", "", "",
                    f"Sub-network with {n_buses} bus(es) and {n_branches} branch(es) "
                    "looks radial — N-1 will island the network at some contingency "
                    "and the LP will be infeasible there. Consider excluding radial "
                    "branches from the outage set or adding redundancy."))
                break
    except Exception:
        pass  # topology computation isn't critical; just skip the hint
    return out


def _check_curtailment_cost(n) -> list[Issue]:
    """
    The custom `curtailment_cost` attribute we surface on Generator should
    only be set on renewables (where curtailment is a meaningful concept).
    Setting it on a thermal generator just adds a dispatch incentive that
    distorts merit order without representing any physical effect.

    Also surfaces a one-shot warning when ANY generator has curtailment_cost
    set: the LP solves with `-cost × p` added to the objective, which
    flips the bus marginal price negative whenever that renewable is the
    marginal unit. Users see this on the Load Flow tab as negative
    hourly prices. The Load Flow chart has a "Merit order" toggle that
    reverses the subsidy at report time — this validator just points
    users at it before they spend time confused.
    """
    out: list[Issue] = []
    if n.generators.empty or "curtailment_cost" not in n.generators.columns:
        return out
    # Mirror frontend's renewable detection (utils/carriers.ts::isRenewableCarrier).
    # `hydro` is checked separately with a word boundary, not folded into the
    # bare-substring `rkw` scan below — `"hydro" in "hydrogen"` is also True,
    # which would exempt hydrogen (H2 storage/generation, not hydropower) from
    # this warning. Same "hydro ⊂ hydrogen" defect folded in from the
    # 2026-07-31 review (Finding 2), fixed the same way in isRenewableCarrier.
    rkw = ("wind", "solar", "pv", "ror", "geothermal",
           "offwind", "onwind", "wave", "tidal", "rooftop")
    active_count = 0
    for name in n.generators.index:
        cost = n.generators.at[name, "curtailment_cost"] or 0
        if not _is_finite(cost) or float(cost) <= 0:
            continue
        active_count += 1
        carrier = str(n.generators.at[name, "carrier"]).lower()
        is_renewable = re.search(r"\bhydro\b", carrier) is not None or any(k in carrier for k in rkw)
        if not is_renewable:
            out.append(_warn("curtailment_cost_on_thermal", "Generator", str(name),
                f"curtailment_cost={cost} but carrier='{carrier}' is not a renewable. "
                "The incentive will still apply, but it's not physically meaningful."))
    if active_count > 0:
        out.append(_warn("curtailment_cost_negative_prices", "", "",
            f"{active_count} generator(s) carry curtailment_cost > 0. By LP "
            "duality this can produce NEGATIVE marginal prices at hours when a "
            "subsidised renewable is the marginal dispatch unit — the LP "
            "interprets `-cost × p` as a payment to dispatch, which flips the "
            "bus price by that amount. Use the 'Merit order' toggle on the "
            "Load Flow → Hourly marginal price chart to see prices with the "
            "subsidy reversed."))
    return out


def _check_snapshot_weights_nyears(n) -> list[Issue]:
    """
    Warn when the effective modelled horizon (``n.nyears``) is far from a
    plausible full year. PyPSA computes ``nyears = Σ snapshot_w.objective /
    8760`` (per period in multi-period mode), then ``periodized_cost`` uses
    this to scale annuitised overnight costs. A nyears value of e.g. 0.019
    (= 168 hours × default weight 1.0) under-prices CAPEX by ~50×, which
    makes the LP over-build extendable assets (especially renewables).

    Common trigger: representative-week setups whose ``snapshot_weightings``
    got silently reset to 1.0 by an earlier ``set_snapshots(MultiIndex)``
    call. Should not fire on the canonical 8760-hourly path with weight=1
    (nyears == 1.0).

    Thresholds: warn if any period's nyears < 0.5 OR > 1.5 (i.e. the
    represented horizon differs from 1 year by more than 50 %). This
    tolerates 168-week × 52.14 scaling (1.0), full hourly year (1.0),
    half-hour data (1.0 with weight 0.5), and a few legitimate edge cases —
    but catches the silent-reset regression.
    """
    out: list[Issue] = []
    try:
        nyears = n.nyears
    except Exception:
        return out
    # `n.nyears` is a Series per period in multi-period mode, a float otherwise.
    if hasattr(nyears, "items"):
        items = list(nyears.items())
    else:
        items = [(None, float(nyears))]
    suspect: list[tuple[object, float]] = []
    for p, val in items:
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if not _is_finite(v):
            continue
        if v < 0.5 or v > 1.5:
            suspect.append((p, v))
    if not suspect:
        return out
    # Multi-period: list a couple of periods; single-period: just the value.
    if suspect[0][0] is None:
        msg = (
            f"Modelled horizon nyears={suspect[0][1]:.3f} is far from 1.0 — "
            f"this scales CAPEX in the LP via periodized_cost. If the snapshots "
            f"are meant to represent one full year, set snapshot_weightings so "
            f"Σ weight × hours ≈ 8760 (e.g. weight 52.14 per snapshot for a "
            f"168-hour representative week)."
        )
    else:
        details = ", ".join(f"period {p}: {v:.3f}" for p, v in suspect[:3])
        msg = (
            f"Some periods have nyears far from 1.0 ({details}). PyPSA's LP "
            f"scales CAPEX by nyears (see periodized_cost), so a nyears of "
            f"0.019 under-prices CAPEX by ~50× and over-builds renewables. "
            f"Likely cause: snapshot_weightings was reset to 1.0 by a "
            f"previous set_snapshots(MultiIndex) — re-apply your weights "
            f"(e.g. 52.14 per snapshot for representative weeks)."
        )
    out.append(_warn("snapshot_weights_nyears_off", "", "", msg))
    return out


def _check_loss_magnitude(n, cfg) -> list[Issue]:
    """
    When transmission_losses is on, warn for any passive branch whose loss
    at rated flow would exceed 5 % of s_nom.

    PyPSA models losses as `loss(p) = r_pu_eff · p²` and ties them into the LP
    via `|dispatch| ≤ s_nom − loss`. A line whose loss-at-s_nom is comparable
    to s_nom is essentially useless to the optimiser — the LP routes flow
    elsewhere and the user sees "transmission losses crushed my flows".

    Fraction at s_nom:
      Line:        (r / v_nom²) · s_nom²   /   s_nom  =  r · s_nom / v_nom²
      Transformer: (r · tap_ratio / s_nom) · s_nom²   /   s_nom  =  r · tap_ratio
        (PyPSA stores transformer r as per-unit on its own s_nom base.)

    Also a hard error if Line.r > 0 but the bus v_nom looks unset (≤ 1 kV) —
    division by ~0 makes losses explode and the LP behaves nonsensically.
    """
    out: list[Issue] = []
    if not bool(getattr(cfg, "transmission_losses", False)):
        return out
    if bool(getattr(cfg, "sclopf", False)):
        return out  # SCLOPF ignores transmission_losses anyway

    threshold = 0.05  # 5 % of s_nom

    # Lines
    if not n.lines.empty:
        bus_vnom = n.buses["v_nom"] if "v_nom" in n.buses.columns else None
        for name in n.lines.index:
            r = n.lines.at[name, "r"] if "r" in n.lines.columns else 0.0
            s_nom = n.lines.at[name, "s_nom"] if "s_nom" in n.lines.columns else 0.0
            bus0 = str(n.lines.at[name, "bus0"]) if "bus0" in n.lines.columns else ""
            if not _is_finite_pos(r) or not _is_finite_pos(s_nom):
                continue
            v_nom = bus_vnom.get(bus0) if bus_vnom is not None else None
            if not _is_finite(v_nom):
                continue  # already flagged by _check_bus_v_nom
            v = float(v_nom)
            if v < 1.0:  # < 1 kV is almost certainly wrong for AC transmission
                out.append(_err("line_loss_vnom_too_small", "Line", str(name),
                    f"transmission_losses is on and r={float(r):g} Ω but bus0='{bus0}' "
                    f"has v_nom={_pretty(v_nom)} kV. PyPSA computes r_pu = r / v_nom², "
                    "so a tiny v_nom inflates losses and the LP will route around the line. "
                    "Set the bus v_nom to a realistic AC voltage (e.g. 220 or 380 kV)."))
                continue
            frac = float(r) * float(s_nom) / (v * v)
            if frac > threshold:
                out.append(_warn("line_loss_high", "Line", str(name),
                    f"At s_nom={float(s_nom):g} MVA, loss ≈ {frac*100:.1f} % of capacity "
                    f"(r={float(r):g} Ω, bus0 v_nom={v:g} kV). The LP will reduce "
                    "flow on this line to free up capacity, possibly to ~0. "
                    "Check the resistance value — expected ~0.03 Ω/km on 380 kV overhead lines."))

    # Transformers — r is per-unit on the transformer's own s_nom base.
    if not n.transformers.empty:
        for name in n.transformers.index:
            r_pu = n.transformers.at[name, "r"] if "r" in n.transformers.columns else 0.0
            tap = n.transformers.at[name, "tap_ratio"] \
                if "tap_ratio" in n.transformers.columns else 1.0
            if not _is_finite_pos(r_pu):
                continue
            if not _is_finite(tap) or float(tap) == 0:
                tap = 1.0
            frac = float(r_pu) * float(tap)
            if frac > threshold:
                out.append(_warn("transformer_loss_high", "Transformer", str(name),
                    f"At s_nom, loss ≈ {frac*100:.1f} % of capacity (r={float(r_pu):g} pu, "
                    f"tap_ratio={float(tap):g}). Typical transformer r is 0.001–0.01 pu. "
                    "The LP will reduce flow through this transformer when transmission_losses "
                    "is on."))
    return out


def _check_rolling_horizon(n, cfg) -> list[Issue]:
    """
    Pre-flight checks for the rolling-horizon LOPF strategy.

    Issues raised:
      • Hard error if SCLOPF is also on — LODF contingency constraints are
        defined against the full snapshot index; the per-window LP can't
        express N-1 coverage that spans windows.
      • Hard error if run_ac_pf_after_lopf is on — the AC PF dispatch fix
        would need a per-window story which the current code doesn't have.
      • Hard error if horizon <= 0 or overlap < 0 (config sanitisation).
      • Hard error if overlap >= horizon (PyPSA rejects this internally).
      • Warning if horizon > total snapshots — the rolling solve degenerates
        to a single window (no harm, but the user probably meant "full").
      • Warning if multi_investment_periods is on — investment-period LPs
        need a defined snapshot index per period; per-window solves do not
        compose cleanly.
    """
    out: list[Issue] = []
    H = int(getattr(cfg, "rolling_horizon", 168) or 0)
    O = int(getattr(cfg, "rolling_overlap", 24) or 0)
    if H <= 0:
        out.append(_err("rolling_horizon_invalid", "", "",
            f"rolling_horizon must be > 0 (got {H})."))
    if O < 0:
        out.append(_err("rolling_overlap_invalid", "", "",
            f"rolling_overlap must be >= 0 (got {O})."))
    if H > 0 and O >= H:
        out.append(_err("rolling_overlap_exceeds_horizon", "", "",
            f"rolling_overlap ({O}) must be strictly less than rolling_horizon ({H})."))
    if getattr(cfg, "sclopf", False):
        out.append(_err("rolling_with_sclopf", "", "",
            "SCLOPF is not compatible with the rolling-horizon strategy — "
            "LODF contingency constraints are defined against the full "
            "snapshot index. Disable one of the two."))
    if getattr(cfg, "run_ac_pf_after_lopf", False):
        out.append(_err("rolling_with_ac_pf", "", "",
            "Auto-chained AC PF is not yet supported with the rolling-horizon "
            "strategy — the dispatch fix needs a per-window slack story. "
            "Disable `run_ac_pf_after_lopf` or use the standalone Stage 2 "
            "trigger after the rolling solve completes."))
    total = len(n.snapshots) if hasattr(n, "snapshots") else 0
    if H > total and total > 0:
        out.append(_warn("rolling_horizon_exceeds_snapshots", "", "",
            f"rolling_horizon ({H}) > total snapshots ({total}). The solve "
            f"will run as a single window — same result as solve_strategy=full."))
    if getattr(cfg, "multi_investment_periods", False):
        # Hard error, not a warning: PyPSA's optimize_with_rolling_horizon
        # doesn't accept multi_investment_periods, doesn't apply
        # investment_period_weightings.years across windows, and each window
        # builds its own independent set of capacity decisions. In practice
        # the later-period windows end up under-built and the solver sheds
        # load instead of expanding — a result that diverges drastically
        # from a single-shot multi-period LP. Force the user to pick one
        # mode. Solver-side auto-fallback (to full-horizon) is a safety net
        # for any flow that bypasses preflight.
        out.append(_err("rolling_with_multi_period", "", "",
            "Rolling-horizon is incompatible with multi-investment periods. "
            "PyPSA solves each window independently without period weightings, "
            "so capacity decisions don't coordinate across periods — later "
            "periods shed load instead of expanding. Switch solve_strategy "
            "back to 'full' for multi-period capacity expansion, or disable "
            "multi_investment_periods if you really need the rolling solver."))
    return out


def _check_myopic_foresight(n, cfg) -> list[Issue]:
    """
    Pre-flight checks for the myopic-foresight solve strategy (Phase 1 of
    the limited-foresight rolling planner).

    Issues raised:
      • Hard error if multi_investment_periods is off — the whole point of
        myopic is per-period sequencing; a flat-snapshot network has nothing
        to roll over.
      • Hard error if the user configured zero investment periods — same
        reason as above but caught explicitly so the message is clearer.
      • Hard error if SCLOPF is also on — N-1 contingency dispatch would
        need its own per-period LODF treatment which the driver doesn't
        have.
      • Hard error if auto-chained AC PF is on — Stage 2 inherits the
        LP-stage dispatch, but myopic produces results PER PERIOD, so
        there's no single dispatch state to fix for the PF step.
    """
    import pandas as pd
    out: list[Issue] = []
    if not getattr(cfg, "multi_investment_periods", False):
        out.append(_err("myopic_requires_multi_period", "", "",
            "Myopic foresight is only meaningful with multi-investment-period "
            "planning. Enable multi_investment_periods or switch "
            "solve_strategy back to 'full'."))
    # The myopic driver reads periods from `n.investment_periods` (set via
    # Snapshots → multi-period promotion), NOT from `cfg.investment_periods`.
    # The latter is an optional override applied inside
    # _apply_modelling_assumptions step 4 and is typically empty. Fall back
    # to cfg.investment_periods only when the network's list is empty, so
    # the message is helpful regardless of which surface the user used.
    network_periods = []
    if hasattr(n, "investment_periods"):
        try:
            network_periods = list(n.investment_periods)
        except Exception:
            network_periods = []
    cfg_periods = list(getattr(cfg, "investment_periods", None) or [])
    if not network_periods and not cfg_periods:
        out.append(_err("myopic_no_periods", "", "",
            "Myopic foresight needs at least one investment period. The "
            "network's snapshot MultiIndex is empty at level 0 — promote "
            "snapshots to multi-period under Snapshots → Multi-period first, "
            "or switch solve_strategy back to 'full'."))
    # Flat snapshots on a multi-period config are auto-promoted by
    # _apply_modelling_assumptions step 4 when cfg.investment_periods is
    # non-empty (it calls n.set_investment_periods(periods=...) which
    # reshapes the index). Only error here when cfg won't perform that
    # promotion — i.e. the network is flat AND cfg.investment_periods is
    # empty. Without this carve-out, every user who configures periods
    # purely via the solver config gets a false-positive validation block.
    if (
        hasattr(n, "snapshots")
        and not isinstance(n.snapshots, pd.MultiIndex)
        and not cfg_periods
    ):
        out.append(_err("myopic_flat_snapshots", "", "",
            "Myopic foresight needs the snapshots index to be a (period, "
            "timestep) MultiIndex. The network currently has a flat index "
            "and cfg.investment_periods is empty — either promote snapshots "
            "via Snapshots → multi-period, set cfg.investment_periods so "
            "the solver auto-promotes, or switch solve_strategy back to "
            "'full'."))
    # SCLOPF + myopic is supported. The contingency set is resolved once at
    # the start of `_run_myopic_foresight` and filtered per iteration so
    # future-dated outage candidates (build_year > current_period) don't
    # appear before they're built. Just warn about the cost so the user
    # knows large contingency sets multiply per iteration.
    if getattr(cfg, "sclopf", False):
        scope = getattr(cfg, "sclopf_scope", "horizon")
        if scope not in ("horizon", "current_period"):
            out.append(_err("sclopf_scope_invalid", "", "",
                f"`sclopf_scope` must be 'horizon' or 'current_period', got '{scope}'."))
        else:
            out.append(_warn("myopic_sclopf_cost", "", "",
                f"SCLOPF + myopic enabled (scope={scope}). The LP grows roughly "
                f"as snapshots × contingencies × affected branches per "
                f"iteration — keep contingency lists small for solve-time "
                f"tractability."))
            # current_period scope drops future-period representative
            # snapshots from each iteration's LP — so limited foresight,
            # which exists specifically to inject those representatives,
            # becomes a no-op for SCLOPF iterations. Flag the combination
            # so the user doesn't silently lose forward visibility.
            if scope == "current_period" and bool(getattr(cfg, "lf_aggregate_future", False)):
                out.append(_warn("sclopf_scope_current_with_lf", "", "",
                    "`sclopf_scope='current_period'` drops future-period "
                    "representative snapshots from each iteration's contingency "
                    "LP, so `lf_aggregate_future=True` has no effect during "
                    "SCLOPF iterations. Use `sclopf_scope='horizon'` to keep "
                    "the lookahead, or accept that the SCLOPF iterations are "
                    "pure single-period."))
    if getattr(cfg, "run_ac_pf_after_lopf", False):
        out.append(_err("myopic_with_ac_pf", "", "",
            "Auto-chained AC PF is not yet supported with the myopic-foresight "
            "solve strategy — the dispatch fix needs a per-period story. "
            "Disable `run_ac_pf_after_lopf` or use the standalone Stage 2 "
            "trigger after the myopic solve completes."))
    out.extend(_check_myopic_capacity_lock(n, network_periods or cfg_periods))
    return out


# Component class → (static attribute, nominal-capacity field). Mirrors
# `solver_service._NOM_TRIPLES` / `vintage_service.SUPPORTED_COMPONENTS`; kept
# local so validation has no import edge into the solver.
_MYOPIC_NOM_PAIRS = (
    ("Generator",   "generators",    "p_nom"),
    ("StorageUnit", "storage_units", "p_nom"),
    ("Store",       "stores",        "e_nom"),
    ("Link",        "links",         "p_nom"),
    ("Line",        "lines",         "s_nom"),
    ("Transformer", "transformers",  "s_nom"),
)


def _check_myopic_capacity_lock(n, periods) -> list[Issue]:
    """
    Warn when a myopic run cannot expand capacity after its FIRST period.

    After each iteration, `_freeze_period_capacities` pins every extendable
    asset ACTIVE in that period to `p_nom = p_nom_opt, extendable = False` so
    later periods treat it as existing plant. An asset left at the default
    `build_year = 0` is active in every period, so the very first iteration
    freezes it and no later period can ever add to it. The run still reports
    `optimal`; the capacity is simply pinned at the first period's optimum
    while demand keeps growing, and the gap is absorbed by unserved energy.

    Measured on a 3-period system with +44% demand growth: gas froze at 977 MW
    and unserved energy ran 47 → 1 756 → 5 183 MWh. With per-period vintage
    bounds on the same asset it built 977 / +195 / +234 MW and unserved energy
    stayed at 47 → 56 → 68 MWh.

    Per-period vintage bounds (Assets → per-period bounds, stored in
    `n.meta["vintage_bounds"]`) are the supported way to let an asset expand in
    more than one period: `vintage_service` expands it into one extendable row
    per period with `build_year = period`, and the myopic driver defers each
    vintage to its own iteration. This is a WARNING, not an error — freezing is
    legitimate when the user really does mean "decide the fleet once, then
    operate it".
    """
    out: list[Issue] = []
    try:
        period_list = sorted({int(p) for p in (periods or [])})
    except (TypeError, ValueError):
        return out
    # One period cannot lock anything — there is no "later period" to starve.
    if len(period_list) < 2:
        return out

    try:
        bounds = (n.meta or {}).get("vintage_bounds") or {}
    except Exception:
        bounds = {}
    first_period = period_list[0]
    locked: list[str] = []
    covered = 0
    for comp_class, attr, pnom in _MYOPIC_NOM_PAIRS:
        df = getattr(n, attr, None)
        if df is None or getattr(df, "empty", True):
            continue
        ext_col = f"{pnom}_extendable"
        if ext_col not in df.columns:
            continue
        ext = df[df[ext_col].astype(bool)]
        if ext.empty:
            continue
        class_bounds = bounds.get(comp_class) or {}
        # build_year defaults to 0 → active from the first period onward, so the
        # first iteration freezes it. An asset dated INTO a later period is
        # decided by that period's iteration and is not locked out.
        by = ext["build_year"] if "build_year" in ext.columns else None
        for name in ext.index:
            if class_bounds.get(str(name)):
                covered += 1
                continue
            build_year = 0
            if by is not None:
                try:
                    build_year = int(by.at[name])
                except (TypeError, ValueError):
                    build_year = 0
            if build_year <= first_period:
                locked.append(f"{comp_class}:{name}")

    if not locked:
        return out
    shown = ", ".join(locked[:5]) + (f" (+{len(locked) - 5} more)" if len(locked) > 5 else "")
    covered_note = (
        f" {covered} asset(s) DO have per-period bounds and will expand normally."
        if covered else ""
    )
    out.append(_warn("myopic_capacity_locked_after_first_period", "", "",
        f"Myopic foresight will freeze {len(locked)} extendable asset(s) after "
        f"period {first_period}, so they cannot grow in the remaining "
        f"{len(period_list) - 1} period(s): {shown}. If demand rises later, the "
        f"shortfall becomes unserved energy and the solve still reports optimal. "
        f"Set per-period vintage bounds on the assets that should be able to "
        f"expand in more than one period.{covered_note}"))
    return out


def _check_stage2_ac_pf(n, cfg) -> list[Issue]:
    """
    Pre-flight checks for the Stage 2 (post-LOPF AC Power Flow) workflow.

    Runs only when `run_ac_pf_after_lopf=True` on the LOPF path. Catches the
    common failure modes BEFORE the LP completes — saves the user a 5-minute
    optimisation only for `n.pf()` to error on the back end.

    Issues raised here:
      • Hard error if `multi_investment_periods=True` AND Stage 2 is on —
        PyPSA's `n.pf()` doesn't accept a `pd.MultiIndex` snapshot index.
      • Hard error if any Line has both `r=0` AND `x=0` — produces a
        singular admittance matrix; Newton-Raphson cannot start.
      • Warn if no generator has `control='Slack'` AND the user didn't set
        an explicit `ac_pf_slack_bus` — `_pick_ac_pf_slack_bus` will pick
        one, but flag it so users understand the auto-pick is happening.
      • Warn if more than one Slack-controlled generator exists in the same
        sub-network — PyPSA needs exactly one slack per island.
    """
    out: list[Issue] = []

    # Multi-investment-period + AC PF: incompatible
    if getattr(cfg, "multi_investment_periods", False):
        out.append(_err("stage2_multi_period", "", "",
            "Stage 2 (AC Power Flow) does not support multi-investment-period "
            "snapshots — PyPSA's `n.pf()` accepts a flat snapshot index only. "
            "Disable `run_ac_pf_after_lopf` or `multi_investment_periods`."))

    # Zero-impedance lines: singular Y-bus
    if not n.lines.empty:
        if "r" in n.lines.columns and "x" in n.lines.columns:
            for name in n.lines.index:
                r = n.lines.at[name, "r"]
                x = n.lines.at[name, "x"]
                if _is_finite(r) and _is_finite(x) and float(r) == 0 and float(x) == 0:
                    out.append(_err("stage2_zero_impedance", "Line", str(name),
                        "Line has both r=0 and x=0 — singular admittance. "
                        "Newton-Raphson cannot start. Set a non-zero reactance."))

    # Slack-controlled generator OR explicit slack-bus override OR auto-pick
    # is acceptable. Only warn when none of those are in place AND the user
    # might be expecting their network to "just work".
    user_bus = (getattr(cfg, "ac_pf_slack_bus", "") or "").strip()
    if not user_bus:
        gen_slack_count = 0
        bus_slack_count = 0
        if not n.generators.empty and "control" in n.generators.columns:
            gen_slack_count = int((n.generators["control"].astype(str).str.lower() == "slack").sum())
        if "control" in n.buses.columns:
            bus_slack_count = int((n.buses["control"].astype(str).str.lower() == "slack").sum())
        if gen_slack_count == 0 and bus_slack_count == 0:
            out.append(_warn("stage2_no_explicit_slack", "", "",
                "No generator or bus has control='Slack' and ac_pf_slack_bus is "
                "blank. Stage 2 will auto-pick the bus with the largest "
                "generation. Set a Slack generator (right panel → Control) "
                "to take control of the choice."))

        # Multi-slack within one sub-network — only a warning since PyPSA's
        # pf() will demote duplicates, but the choice is non-deterministic.
        if gen_slack_count > 1:
            out.append(_warn("stage2_multi_slack", "", "",
                f"{gen_slack_count} generators are Slack-controlled. PyPSA "
                "expects at most one Slack per electrical island; multiple "
                "Slacks may produce non-deterministic dispatch. Pick one "
                "Slack generator or set ac_pf_slack_bus explicitly."))

    return out


def _check_storage_cyclic_initial(n) -> list[Issue]:
    """
    When cyclic_state_of_charge=True, PyPSA ignores state_of_charge_initial
    and instead enforces SoC(0) = SoC(end). A non-zero initial SoC then has
    no effect — common silent gotcha after toggling cyclic on/off.
    """
    out: list[Issue] = []
    if n.storage_units.empty:
        return out
    if "cyclic_state_of_charge" not in n.storage_units.columns or \
            "state_of_charge_initial" not in n.storage_units.columns:
        return out
    for name in n.storage_units.index:
        if not bool(n.storage_units.at[name, "cyclic_state_of_charge"]):
            continue
        soc = n.storage_units.at[name, "state_of_charge_initial"]
        if _is_finite(soc) and float(soc) > 0:
            out.append(_warn("storage_initial_ignored", "StorageUnit", str(name),
                f"cyclic_state_of_charge=True so state_of_charge_initial={soc} is ignored. "
                "PyPSA enforces SoC(start)=SoC(end) instead."))
    return out


def _check_p_max_pu_bounds(n) -> list[Issue]:
    """
    p_max_pu values should lie in [0, 1] — values above 1 mean "dispatch
    above nameplate" which is physically impossible (and possibly a unit
    confusion: user thinking p_max_pu is in MW).
    """
    out: list[Issue] = []
    if not hasattr(n.generators_t, "p_max_pu") or n.generators_t.p_max_pu.empty:
        return out
    df = n.generators_t.p_max_pu
    for col in df.columns:
        v = df[col]
        if _is_finite(v.max()) and float(v.max()) > 1.0 + 1e-9:
            out.append(_warn("p_max_pu_above_one", "Generator", col,
                f"time-varying p_max_pu max={v.max():.3f} > 1. "
                "Values are fractions of p_nom; >1 means dispatching above nameplate."))
    return out


def _check_lopf(n, solver_config) -> list[Issue]:
    out: list[Issue] = []

    # Lines / Transformers: x must be > 0 (the LP power-flow constraint uses
    # 1/x as the susceptance), s_nom rules per extendable.
    out += _line_x_check(n.lines, "Line")
    out += _line_x_check(n.transformers, "Transformer")
    out += _check_extendable_bounds(n.lines,        "Line",        "s_nom", True)
    out += _check_extendable_bounds(n.transformers, "Transformer", "s_nom", True)
    out += _check_transformer_types(n)
    out += _check_pmin_pmax(n)
    out += _check_unbounded_costs(n)
    out += _check_modelling_assumptions(n, solver_config)
    out += _check_sclopf(n, solver_config)
    out += _check_curtailment_cost(n)
    out += _check_snapshot_weights_nyears(n)
    out += _check_loss_magnitude(n, solver_config)
    out += _check_storage_cyclic_initial(n)
    out += _check_p_max_pu_bounds(n)

    # Generators
    out += _check_extendable_bounds(n.generators, "Generator", "p_nom", True)
    out += _check_efficiency(n.generators, "Generator", "efficiency")
    if not n.generators.empty:
        cmt = n.generators["committable"] if "committable" in n.generators.columns else None
        ext = n.generators["p_nom_extendable"] if "p_nom_extendable" in n.generators.columns else None
        for name in n.generators.index:
            committable = bool(cmt.loc[name]) if cmt is not None else False
            extendable = bool(ext.loc[name]) if ext is not None else False
            if committable and extendable:
                out.append(_err("gen_committable_extendable", "Generator", str(name),
                    "PyPSA cannot solve unit-commitment AND capacity-expansion "
                    "in the same generator. Disable one."))
            if committable:
                # UC requires these to be defined; PyPSA defaults are sane (≥0)
                # so we mainly check for NaN / negative values.
                for col in ("min_up_time", "min_down_time",
                            "start_up_cost", "shut_down_cost"):
                    if col in n.generators.columns:
                        v = n.generators.at[name, col]
                        if not _is_finite_nonneg(v):
                            out.append(_err("gen_uc_param_invalid", "Generator", str(name),
                                f"committable=True but {col}={_pretty(v)} (must be finite ≥ 0)."))
        # Cost sanity: zero capital + zero marginal cost in LOPF means the
        # solver has no preference between gens — solvable but probably not
        # what the user meant. Warning only.
        if "capital_cost" in n.generators.columns and "marginal_cost" in n.generators.columns:
            # overnight_cost > 0 also feeds into capital_cost at solve time via
            # the annuity recompute, so a generator with overnight_cost set
            # isn't actually cost-less — only flag rows where all three are 0.
            oc = n.generators["overnight_cost"].fillna(0) \
                if "overnight_cost" in n.generators.columns \
                else pd.Series(0.0, index=n.generators.index)
            zero_cost = n.generators[
                (n.generators["capital_cost"].fillna(0) == 0)
                & (n.generators["marginal_cost"].fillna(0) == 0)
                & (oc == 0)
            ]
            if len(zero_cost) > 0:
                out.append(_warn("gen_zero_costs", "Generator",
                    f"{len(zero_cost)} item(s)",
                    f"{len(zero_cost)} generator(s) have capital_cost, "
                    "overnight_cost, and marginal_cost all == 0. Result will be indeterminate."))

    # Loads
    out += _check_loads_p_set(n)

    # StorageUnits
    out += _check_extendable_bounds(n.storage_units, "StorageUnit", "p_nom", True)
    out += _check_efficiency(n.storage_units, "StorageUnit", "efficiency_store")
    out += _check_efficiency(n.storage_units, "StorageUnit", "efficiency_dispatch")
    if not n.storage_units.empty:
        for name in n.storage_units.index:
            mh = n.storage_units.at[name, "max_hours"] if "max_hours" in n.storage_units.columns else None
            if not _is_finite_pos(mh):
                out.append(_err("storage_max_hours_invalid", "StorageUnit", str(name),
                    f"max_hours must be > 0 (got {_pretty(mh)})."))
            # state_of_charge_initial within [0, p_nom * max_hours]
            soc = n.storage_units.at[name, "state_of_charge_initial"] \
                if "state_of_charge_initial" in n.storage_units.columns else 0
            p_nom = n.storage_units.at[name, "p_nom"] if "p_nom" in n.storage_units.columns else 0
            if _is_finite(soc) and _is_finite(p_nom) and _is_finite(mh) and float(p_nom) > 0:
                cap = float(p_nom) * float(mh)
                if float(soc) < 0 or float(soc) > cap:
                    out.append(_err("storage_soc_oob", "StorageUnit", str(name),
                        f"state_of_charge_initial={soc} outside [0, {cap}] (=p_nom·max_hours)."))

    # Stores
    out += _check_extendable_bounds(n.stores, "Store", "e_nom", True)

    # Links — Link.efficiency is a generic conversion coefficient (not
    # bounded by thermodynamics like Generator.efficiency). Heat pumps
    # routinely sit at COP 3-5; electric trains, regenerative drives, and
    # H2-from-electrolysis-then-back-to-power chains can also push beyond
    # 1 on some legs. Allow any positive value on the primary efficiency.
    out += _check_extendable_bounds(n.links, "Link", "p_nom", True)
    out += _check_efficiency(n.links, "Link", "efficiency", upper=None)
    # Multi-link efficiency2 / efficiency3 / efficiency4 are validated only
    # on rows where the target bus_n is populated — PyPSA ignores eff_n
    # otherwise (see _check_buses). For multi-output Links, a NEGATIVE
    # eff_n is the PyPSA idiom for "this link extracts from bus_n":
    # heat pumps drawing low-grade heat (eff2 = -(COP-1)), CHPs with a
    # cooling sink, regen drives. So we relax BOTH the lower bound (allow
    # negative) AND the upper bound (allow COP > 1). The only thing
    # rejected is NaN / inf and exact zero on a populated bus (which
    # would mean "this output is connected but contributes nothing" — a
    # misconfiguration the user almost certainly didn't intend).
    for eff_col in ("efficiency2", "efficiency3", "efficiency4"):
        if eff_col not in n.links.columns:
            continue
        bus_col = "bus" + eff_col.removeprefix("efficiency")
        if bus_col not in n.links.columns:
            continue
        connected_mask = n.links[bus_col].astype(str).map(
            lambda s: s != "" and s != "nan"
        )
        if not connected_mask.any():
            continue
        sub = n.links.loc[connected_mask]
        # Reject exact zero on a populated bus (likely a configuration
        # mistake), then validate the magnitude is finite — no upper or
        # lower bound otherwise.
        for name in sub.index:
            v = sub.at[name, eff_col]
            if not _is_finite(v):
                out.append(_err("link_efficiency_invalid", "Link", str(name),
                    f"{eff_col} must be finite (got {_pretty(v)})."))
                continue
            if float(v) == 0.0:
                out.append(_err("link_efficiency_invalid", "Link", str(name),
                    f"{eff_col}=0 but {bus_col} is connected. Either set "
                    f"{eff_col} to a non-zero value (positive = produces at "
                    f"{bus_col}, negative = extracts from {bus_col}) or "
                    f"clear {bus_col}."))

    # Carriers — only meaningful when a CO2 global constraint references them.
    # We flag NaN co2_emissions whenever any global constraint exists, since
    # PyPSA's default is 0.0 (finite) and only spreadsheet imports usually
    # produce NaN here.
    if not n.global_constraints.empty and not n.carriers.empty \
            and "co2_emissions" in n.carriers.columns:
        for name in n.carriers.index:
            v = n.carriers.at[name, "co2_emissions"]
            if not _is_finite(v):
                out.append(_err("carrier_co2_nan", "Carrier", str(name),
                    f"co2_emissions={_pretty(v)}; with a global CO2 constraint set, "
                    "every carrier must have a finite emission factor."))

    # Multi-investment-period horizon
    if getattr(solver_config, "multi_investment_periods", False):
        if n.investment_periods.empty:
            out.append(_err("multi_period_no_periods", "", "",
                "multi_investment_periods=True but n.investment_periods is empty. "
                "Configure periods in Model Horizon."))
        else:
            periods = list(n.investment_periods)
            min_p, max_p = min(periods), max(periods)
            for comp_name, df in [
                ("Generator",   n.generators),
                ("StorageUnit", n.storage_units),
                ("Store",       n.stores),
                ("Line",        n.lines),
                ("Link",        n.links),
                ("Transformer", n.transformers),
            ]:
                if df.empty:
                    continue
                if "build_year" not in df.columns or "lifetime" not in df.columns:
                    continue
                for name in df.index:
                    by = df.at[name, "build_year"]
                    lt = df.at[name, "lifetime"]
                    if not _is_finite(by):
                        continue  # treated as 0 by PyPSA — fine
                    by_v = float(by)
                    lt_v = float(lt) if _is_finite(lt) else float("inf")
                    # Asset is buildable in some period iff
                    # by ≤ max_period AND by + lifetime > min_period.
                    if by_v > max_p:
                        out.append(_warn("asset_unbuildable_period", comp_name, str(name),
                            f"build_year={by_v:.0f} > max investment_period={max_p}. "
                            "Asset will never be built."))
                    elif by_v + lt_v <= min_p:
                        out.append(_warn("asset_retired_before_periods", comp_name, str(name),
                            f"build_year+lifetime={by_v+lt_v:.0f} ≤ min investment_period={min_p}. "
                            "Asset is already retired in every period."))

    # Solver availability — only matters for lopf.
    try:
        from linopy.solvers import available_solvers as _avail
        avail = list(_avail)
    except Exception:
        avail = []
    if solver_config.solver_name not in avail:
        out.append(_err("solver_unavailable", "", "",
            f"Solver '{solver_config.solver_name}' is not available. "
            f"Installed: {', '.join(avail) or '(none)'}."))

    return out


# ── public entry point ───────────────────────────────────────────────────────

def _check_dsr_coherence(n, solver_config) -> list[Issue]:
    """
    Demand-response tier coherence (spec §4.4). Warnings only.
    """
    issues: list[Issue] = []
    price = float(getattr(solver_config, "dsr_price_eur_per_mwh", 0.0) or 0.0)
    if price <= 0:
        return issues
    share = float(getattr(solver_config, "dsr_share_of_load", 0.0) or 0.0)
    buses = [str(b) for b in (getattr(solver_config, "dsr_buses", None) or [])]
    if not buses:
        issues.append(_warn(
            "dsr_enabled_without_buses", "", "",
            "A demand-response price is set but no buses are opted in — the "
            "tier is OFF. DSR is deliberately never applied globally: on a "
            "network that already models flexibility as a real asset it "
            "would count the same response twice. Pick the buses under "
            "Reliability settings.",
        ))
        return issues
    if share <= 0:
        issues.append(_warn(
            "dsr_zero_volume", "", "",
            "Demand response is enabled but its volume share is 0 — the "
            "tier can never dispatch. Set 'dsr_share_of_load' > 0.",
        ))
    su_buses: set[str] = set()
    if not n.storage_units.empty and "bus" in n.storage_units.columns:
        su_buses = set(n.storage_units["bus"].astype(str))
    link_buses: set[str] = set()
    if not n.links.empty:
        for col in ("bus0", "bus1"):
            if col in n.links.columns:
                link_buses |= set(n.links[col].astype(str))
    for bus in buses:
        if bus not in n.buses.index:
            issues.append(_warn(
                "dsr_unknown_bus", "Bus", bus,
                f"DSR opt-in bus '{bus}' does not exist on the network.",
            ))
            continue
        if bus in su_buses or bus in link_buses:
            issues.append(_warn(
                "dsr_double_count_risk", "Bus", bus,
                f"Bus '{bus}' is opted into demand response but already "
                "hosts modelled flexibility (a storage unit or link). The "
                "DSR slack would count the same flexibility twice — either "
                "remove the opt-in or accept the deliberate double count.",
            ))
    return issues


def _check_ens_cap_coherence(solver_config) -> list[Issue]:
    """
    Reliability-target coherence (adequacy spec §5.1 / plan Phase 1 Task 1).
    Pure config checks — no network needed.
    """
    issues: list[Issue] = []
    cap = getattr(solver_config, "ens_cap_permyriad", None)
    try:
        cap = float(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap = None
    zone_mult = getattr(solver_config, "ens_zone_cap_multiple", None)
    if cap is None or cap <= 0:
        if zone_mult is not None:
            issues.append(_warn(
                "ens_zone_multiple_without_cap", "", "",
                "A per-zone ENS ceiling multiple is set but no system ENS "
                "target is — zone ceilings are defined relative to the "
                "target, so nothing is enforced. Set 'ens_cap_permyriad'.",
            ))
        return issues
    voll = float(getattr(solver_config, "voll", 0.0) or 0.0)
    if voll <= 0:
        issues.append(_warn(
            "ens_cap_without_voll", "", "",
            f"An ENS target ({cap:g}‱) is set but VOLL is 0, so no load-"
            "shedding slack generators exist: the LP either serves all "
            "demand or is infeasible, and the cap constrains nothing. Set a "
            "VOLL (typical 3 000–10 000 €/MWh) to make the target meaningful.",
        ))
    if cap > 100.0:
        issues.append(_warn(
            "ens_cap_generous", "", "",
            f"The ENS target is {cap:g}‱ = {cap / 100.0:g}% of demand — "
            "planning NOT to serve that share. Real reliability standards "
            "are 2–3 orders of magnitude tighter (adequate systems run "
            "around 0.1–1‱ of energy; GB's standard is 3 loss-of-load "
            "hours/yr). A generous cap yields a cheap-looking, badly "
            "under-built plan.",
        ))
    strategy = str(getattr(solver_config, "solve_strategy", "full") or "full")
    if strategy in ("rolling", "myopic"):
        issues.append(_err(
            "ens_cap_unsupported_strategy", "", "",
            f"The ENS target is not supported with the '{strategy}' solve "
            "strategy yet: each LP window would need its own demand "
            "denominator, and a per-window cap is not the per-period "
            "standard the target promises. Use the full strategy, or unset "
            "the target.",
        ))
    return issues


def _check_reserve_margin(n, solver_config) -> list[Issue]:
    """
    Firm-capacity (reserve-margin) coherence — Phase 8 spec §3.

    Three findings, all of which have to be made BEFORE the solve because
    after it they are either unsayable or too late:

    * **unpriceable assets (ERROR).** A generator with no outage data AND no
      availability profile is EXCLUDED from the standard's left-hand side
      (§2.2 — crediting it would mean defaulting its derate to 1.0, giving a
      unit the tool knows nothing about MORE firm credit than a gas unit on a
      class average). The wrapper logs the exclusion, but a log line is not a
      decision point: the user would be committing to a plan built against a
      fleet the tool silently shrank.
    * **an unreachable margin (ERROR).** This is the answer that REPLACES
      "let the LP go infeasible", which is not implementable: linopy raises
      ``TypeError`` on a constant constraint and ``Generator-p_nom`` does not
      exist when nothing extendable is active. Every term of
      ``max_achievable < required`` is a constant before the solve, so the
      question is fully decidable here — and the answer here is the more
      useful one ("no plan built from your candidate set can reach this
      margin", with both numbers) than an infeasible LP.
    * **carrier-default derating (WARNING).** Publishing ``source`` in the
      post-solve table is necessary but not sufficient: a class average the
      user never entered changes what gets BUILT, and by the time the table
      exists the plan is already built around it.

    Every number comes from ``solver_service.reserve_margin_facts`` — the
    SAME function the wrapper builds its constraint from. A second
    implementation of the derating chain here would be a second standard: the
    one this blocks on and the one the LP enforces.
    """
    from services.solver_service import _prm_margin, reserve_margin_facts

    margin = _prm_margin(solver_config)
    if margin is None:
        return []
    issues: list[Issue] = []

    # ── the rolling/myopic question, adjudicated (spec §3, last bullet).
    #
    # `_check_ens_cap_coherence` refuses BOTH strategies for the energy cap.
    # The margin mirrors that for ROLLING and diverges for MYOPIC, and the
    # reason is the denominator. `optimize_with_rolling_horizon` calls
    # `extra_functionality` once per WINDOW with that window's snapshots, so
    # §2.5's `peak_P` silently becomes the window's peak: a weaker standard
    # than the one asked for, enforced under its name, and re-stashed by every
    # window so the report describes only the last one. A myopic iteration's
    # snapshots, by contrast, ARE one investment period — exactly the
    # denominator the standard is defined against — so the constraint it
    # installs is the right one. What breaks under myopic is only the REPORT:
    # each iteration overwrites `_reserve_margin_targets`, so the published
    # block covers the final period alone. A correct standard with an
    # incomplete report is a warning; a silently different standard is not.
    strategy = str(getattr(solver_config, "solve_strategy", "full") or "full")
    if strategy == "rolling":
        issues.append(_err(
            "reserve_margin_unsupported_strategy", "", "",
            "The reserve margin is not supported with the 'rolling' solve "
            "strategy: PyPSA solves each window independently and the "
            "constraint would be built against that WINDOW's peak demand, "
            "not the period's — a weaker standard than the one you set, "
            "enforced under its name. Use the full strategy, or unset the "
            "margin.",
        ))
    elif strategy == "myopic":
        issues.append(_warn(
            "reserve_margin_myopic_report_is_partial", "", "",
            "With myopic foresight the reserve margin IS enforced in every "
            "investment period (each iteration's snapshots are exactly one "
            "period, which is the peak the standard is defined against), but "
            "each iteration overwrites the solve-time record: the adequacy "
            "report and /results/reserve_margin will describe only the LAST "
            "period solved. Read the [PRM] log lines for the earlier ones.",
        ))

    try:
        facts = reserve_margin_facts(n, solver_config)
    except Exception:
        # A diagnosis that crashed must never block a run it cannot judge.
        return issues
    if facts is None:
        return issues

    unpriceable = list(facts.get("unpriceable") or [])
    if unpriceable:
        names = ", ".join(sorted(unpriceable)[:20])
        more = " …" if len(unpriceable) > 20 else ""
        issues.append(_err(
            "reserve_margin_unpriceable_assets", "", "",
            f"The reserve margin cannot price {len(unpriceable)} asset(s) "
            f"(no outage data, no availability profile): {names}{more}. They "
            "are excluded from the firm-capacity total — never credited at "
            "1.0 — so the margin would be enforced against a fleet smaller "
            "than the one you built. Enter an outage rate and basis, or an "
            "availability profile, or unset the margin.",
        ))

    for P, per in (facts["stash"].get("periods") or {}).items():
        required = float(per.get("required_mw", 0.0))
        reachable = float(per.get("max_achievable_mw", 0.0))
        if required <= 0 or not math.isfinite(required):
            continue
        if reachable < required:
            where = "" if P == "ALL" else f" in period {P}"
            issues.append(_err(
                "reserve_margin_unreachable", "", "",
                f"No plan built from your candidate set can reach the "
                f"{margin:.1%} reserve margin{where}: it requires "
                f"{required:,.1f} MW of derated firm capacity against a "
                f"{float(per.get('peak_mw', 0.0)):,.1f} MW peak, and the "
                f"whole fleet — every extendable at its p_nom_max, derated — "
                f"tops out at {reachable:,.1f} MW. Raise a p_nom_max, add "
                "candidate capacity, or lower the margin. (This is a "
                "preflight error rather than an infeasible LP because every "
                "term of it is a constant before the solve.)",
            ))

    defaults = list(facts.get("carrier_default") or [])
    if defaults:
        names = ", ".join(sorted(defaults)[:20])
        more = " …" if len(defaults) > 20 else ""
        issues.append(_warn(
            "reserve_margin_carrier_default_derating", "", "",
            f"The reserve margin derates {len(defaults)} asset(s) using "
            f"carrier class averages you did not enter: {names}{more}. Those "
            "numbers change what gets built — a unit credited at 0.95 buys "
            "5 % less firm capacity than one credited at 1.0. Enter "
            "asset-level outage rates for anything the plan turns on.",
        ))

    return issues


def _check_outage_params(n) -> list[Issue]:
    """
    Adequacy occurrence attributes (design spec §5.4): warn on implausible
    (rate, MTTR) pairs and malformed values. Warnings only, never blocking —
    a solve without outage data is still a valid solve; the data only feeds
    the adequacy/FMEA analysis. Asset names are embedded in the message
    (the shared validator speaks in whole sentences); component_class is set
    so the UI can group them.
    """
    from services.adequacy.occurrence import (
        resolve_outage_params,
        validate_outage_params,
    )

    issues: list[Issue] = []
    for component, cls in (
        ("generators", "Generator"), ("storage_units", "StorageUnit"),
        ("stores", "Store"), ("links", "Link"), ("lines", "Line"),
    ):
        df = getattr(n, component, None)
        if df is None or df.empty or "outage_rate_value" not in df.columns:
            # Only networks where someone actually entered outage data get
            # validated — carrier defaults alone are library values and
            # already plausible by construction.
            continue
        try:
            params = resolve_outage_params(n, component)
            params = params[params["source"] == "asset"]
            for msg in validate_outage_params(params):
                issues.append(_warn("outage_params_implausible", cls, "", msg))
        except Exception:
            continue
    return issues


def validate_for_run(n, solver_config) -> list[Issue]:
    """Return all issues. Empty list = ready to run."""
    issues: list[Issue] = []
    issues += _check_network_level(n)
    # Stop early if the network is degenerate — the rest assumes buses exist.
    if any(i.code in ("snapshots_empty", "buses_empty") for i in issues):
        return issues
    issues += _check_bus_v_nom(n)
    issues += _check_bus_references(n)
    # Ungated on purpose — see _check_carrier_emissions docstring. Not
    # conditioned on co2_price or a global constraint, unlike the
    # carrier_co2_nan check further down in _check_lopf.
    issues += _check_carrier_emissions(n)
    # Adequacy occurrence data — warnings only, any mode.
    issues += _check_outage_params(n)
    # Reliability-target coherence — pure config checks.
    issues += _check_ens_cap_coherence(solver_config)
    # Demand-response tier coherence (spec §4.4).
    issues += _check_dsr_coherence(n, solver_config)

    mode = solver_config.mode
    if mode == "pf":
        issues += _check_pf(n)
    elif mode == "lopf":
        issues += _check_lopf(n, solver_config)
        # When Stage 2 auto-chains, the AC PF inherits the LP's dispatch and
        # then needs a slack + finite line/trafo impedances. Surface the same
        # checks that `_check_pf` runs so users see them at preflight time.
        if getattr(solver_config, "run_ac_pf_after_lopf", False):
            issues += _check_stage2_ac_pf(n, solver_config)
        # Rolling-horizon: validate the window/overlap make sense, and reject
        # combinations the dispatch path can't handle (SCLOPF, auto-chained
        # AC PF). Surfaced as errors so the run is blocked before wasting
        # solver time, not as warnings.
        if getattr(solver_config, "solve_strategy", "full") == "rolling":
            issues += _check_rolling_horizon(n, solver_config)
        # Myopic foresight: Phase 1 of limited-foresight rolling. Requires
        # multi_investment_periods (the whole point is solving one period at
        # a time), incompatible with SCLOPF (each period would need its own
        # LODF/contingency story) and with auto-chained AC PF (per-period
        # dispatch fix doesn't compose). Surfaced as errors so users can't
        # accidentally request the strategy when it doesn't apply.
        if getattr(solver_config, "solve_strategy", "full") == "myopic":
            issues += _check_myopic_foresight(n, solver_config)
        # Firm-capacity standard (Phase 8 §3). LOPF-only on purpose: the
        # margin is an LP constraint, and a `pf` run enforces nothing — so a
        # margin left in the config cannot make an AC power flow wrong, and
        # blocking one on it would be a refusal with no standard behind it.
        issues += _check_reserve_margin(n, solver_config)
    else:
        issues.append(_err("unknown_mode", "", "",
            f"Solver mode '{mode}' not recognised (expected lopf/pf)."))

    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)
