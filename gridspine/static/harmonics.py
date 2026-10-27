"""The harmonic-distortion screen at the point of connection (PoC)
(grid-to-process issue 17, harmonics part).

Large drives and thyristor heaters inject harmonic currents. This module
turns each unit's current spectrum into the harmonic voltage it causes at the
PoC, per order and as THD, and judges it against the grid code's limits:

    I_h,unit = pct_h / 100 * rated_mva                (MVA at nominal voltage)
    I_h      = (sum_i I_h,i ** alpha) ** (1 / alpha)  (IEC 61000-3-6 general summation law)
    U_h %    = 100 * h * I_h / S_k                    (Z_sc = U^2 / S_k, at order h)
    THD %    = sqrt(sum_h U_h %^2),  h = 2..50

``alpha`` is 1 below order 5, 1.4 from 5 to 10 and 2 above 10 (``law="iec"``);
``law="arithmetic"`` is the same formula with alpha 1 at every order.

S_k is a caller input: the module reads no fault level itself (the scorecard
passes ``_poc_sk_mva``, the campus study its PCC's minimum case).

Every unit has a spectrum from one of two places, and its row says which:
the vendor's (``harmonic_spectrum``, order:% of rated current) or the demo
default for its topology (``DEFAULT_SPECTRA``). A unit with no rated MVA, or
with neither a topology nor a spectrum, is "no input": its row is kept with
verdict ``unknown`` and it blocks a pass at its PoC. ``linear`` declares a
unit that emits none.

Each row: verdict ``pass``/``fail``/``unknown``. Limits are THD and/or per
order; a row is judged on whichever is set, and is ``unknown`` only when
neither is.

Pure pandas; no engine import.
"""
from __future__ import annotations

import dataclasses
import math

import pandas as pd

from gridspine.schema.contracts import ContractError

H_MIN, H_MAX = 2, 50
TOLERANCE = 1e-9
LAWS = ("iec", "arithmetic")

#: Drive topologies and heater controls. ``linear`` is a declared non-emitter.
TOPOLOGIES = ("linear", "6-pulse", "12-pulse", "18-pulse", "active-front-end",
              "phase-angle", "burst", "thyristor-stack")

#: Default current spectra, % of rated current by order. DEMO values: typical
#: generic-engineering shapes (6-pulse with a DC-link choke; 12/18-pulse with a
#: small residual of the cancelled orders; an active front end; a three-phase
#: AC controller at its worst firing angle), not from any standard or vendor.
#: Burst (integral-cycle) control emits no harmonics; it is a flicker case.
DEFAULT_SPECTRA = {
    "linear": {},
    "6-pulse": {5: 30.0, 7: 12.0, 11: 7.0, 13: 5.0, 17: 3.0, 19: 2.5, 23: 2.5, 25: 2.0},
    "12-pulse": {5: 3.0, 7: 1.5, 11: 7.0, 13: 5.0, 23: 2.5, 25: 2.0},
    "18-pulse": {5: 1.5, 7: 1.0, 11: 1.0, 13: 0.8, 17: 3.0, 19: 2.5, 23: 0.5, 25: 0.5,
                 35: 1.2, 37: 1.0},
    "active-front-end": {5: 1.5, 7: 1.0, 11: 0.8, 13: 0.6, 17: 0.4, 19: 0.3, 23: 0.3, 25: 0.3,
                         29: 0.2, 31: 0.2},
    "phase-angle": {5: 25.0, 7: 12.0, 11: 8.0, 13: 6.0, 17: 4.0, 19: 3.0, 23: 2.5, 25: 2.0},
    "burst": {},
    "thyristor-stack": {5: 25.0, 7: 12.0, 11: 8.0, 13: 6.0, 17: 4.0, 19: 3.0, 23: 2.5, 25: 2.0},
}

LABEL = "static screen; binding answer is a harmonic load-flow study with the grid's impedance"

LEDGER = (
    "U_h = h * I_h * Z_sc with Z_sc from the PoC fault level alone: the grid's X/R, its "
    "frequency response and any resonance with capacitor banks or cables are ignored; every "
    "unit is lumped at the PoC, so the phase shift of its transformer (which cancels 5th and "
    "7th between 6-pulse units on Dy and Yy transformers) is not modelled; all of these are "
    "conservative or screening-only (assumed scope)",
    "default spectra per topology are demo values, not a standard's or a vendor's; a vendor "
    "spectrum per unit overrides them",
    "filters are not modelled; a vendor spectrum may already be post-filter, and the row says so",
    "an installation is compared with the whole limit given, not an allocated share of a "
    "planning level, which is lenient where the limit is a planning level",
)

UNIT_COLUMNS = (
    "poc", "unit", "rated_mva", "harmonic_source", "spectrum_source", "post_filter", "sk_mva",
    "u_h_pct", "thd_pct", "thd_limit_pct", "thd_margin_pct", "worst_order", "worst_u_h_pct",
    "worst_limit_pct", "failing_orders", "verdict", "note", "label",
)
POC_COLUMNS = (
    "poc", "units", "no_input_units", "law", "sk_mva", "u_h_pct", "thd_pct", "thd_limit_pct",
    "thd_margin_pct", "worst_order", "worst_u_h_pct", "worst_limit_pct", "failing_orders",
    "verdict", "note", "label",
)


@dataclasses.dataclass(frozen=True)
class Limits:
    """THD and per-order limits, % of nominal voltage; None for not set."""
    thd_pct: float | None = None
    orders_pct: dict | None = None


@dataclasses.dataclass(frozen=True)
class HarmonicStudy:
    """``units``: one row per unit, its own contribution alone
    (``UNIT_COLUMNS``); ``pocs``: one row per PoC, summed (``POC_COLUMNS``)."""
    units: pd.DataFrame
    pocs: pd.DataFrame


def alpha(h: int, law: str = "iec") -> float:
    """The summation exponent at order ``h``."""
    if law not in LAWS:
        raise ContractError(f"summation law must be one of {list(LAWS)}, got {law!r}")
    if law == "arithmetic" or h < 5:
        return 1.0
    return 1.4 if h <= 10 else 2.0


def parse_spectrum(text) -> dict:
    """``{order: % of rated current}`` from ``"5: 20, 7: 14"``; ``{}`` when empty.
    Orders are integers 2..50, each once; percentages finite and >= 0."""
    if isinstance(text, dict):
        items = [(str(k), str(v)) for k, v in text.items()]
    elif text is None or (isinstance(text, float) and math.isnan(text)) or not str(text).strip():
        return {}
    else:
        items = []
        for part in str(text).split(","):
            if ":" not in part:
                raise ContractError(f"Harmonic spectrum: expected order:% pairs such as 5:20, got {part.strip()!r}.")
            k, v = part.split(":", 1)
            items.append((k.strip(), v.strip()))
    out = {}
    for k, v in items:
        try:
            h = int(k)
        except ValueError:
            raise ContractError(f"Harmonic spectrum: order {k!r} is not a whole number.") from None
        if h < H_MIN:
            raise ContractError(f"Harmonic spectrum: order {h} is below {H_MIN}.")
        if h > H_MAX:
            raise ContractError(f"Harmonic spectrum: order {h} is above {H_MAX}.")
        if h in out:
            raise ContractError(f"Harmonic spectrum: order {h} appears twice.")
        try:
            pct = float(v)
        except ValueError:
            pct = math.nan
        if not math.isfinite(pct) or pct < 0:
            raise ContractError(f"Harmonic spectrum: order {h} needs a percentage of 0 or more, got {v!r}.")
        out[h] = pct
    return out


def unit_spectrum(topology, vendor) -> tuple[dict | None, str]:
    """(spectrum, its source): the vendor's when given, else the topology's
    demo default (none for a declared ``linear`` unit); (None, reason) when
    neither is."""
    spectrum = parse_spectrum(vendor)
    topology = "" if topology is None or (isinstance(topology, float) and math.isnan(topology)) \
        else str(topology).strip()
    if topology and topology not in TOPOLOGIES:
        raise ContractError(f"Harmonic source: {topology!r} is not one of {', '.join(TOPOLOGIES)}.")
    if spectrum:
        return spectrum, "vendor"
    if topology == "linear":
        return {}, "declared linear"
    if topology:
        return dict(DEFAULT_SPECTRA[topology]), f"demo default ({topology})"
    return None, "no harmonic_source and no spectrum"


def _i_h(contribs: list, law: str) -> dict:
    """The summed harmonic current per order, in MVA at the PoC's voltage, from
    [(rated_mva, spectrum)]: each unit's I_h = rated_mva · % / 100, summed with
    the exponent ``alpha(h, law)``."""
    orders = sorted({h for _, s in contribs for h in s})
    out = {}
    for h in orders:
        a = alpha(h, law)
        out[h] = sum((mva * s.get(h, 0.0) / 100.0) ** a for mva, s in contribs) ** (1.0 / a)
    return out


def _u_h(contribs: list, sk: float, law: str) -> dict:
    """U_h % per order from [(rated_mva, spectrum)] at a PoC of S_k MVA."""
    return {h: 100.0 * h * i_h / sk for h, i_h in _i_h(contribs, law).items()}


def _judge(u: dict, limits: Limits) -> dict:
    thd = math.sqrt(sum(v * v for v in u.values()))
    orders = limits.orders_pct or {}
    limited = {h: v for h, v in u.items() if h in orders}
    if limited:
        worst = max(limited, key=lambda h: (limited[h] / orders[h] if orders[h] > 0 else math.inf, -h))
    elif u:
        worst = max(u, key=lambda h: (u[h], -h))
    else:
        worst = None
    failing = sorted(h for h, v in limited.items() if v > orders[h] + TOLERANCE)
    thd_fail = limits.thd_pct is not None and thd > limits.thd_pct + TOLERANCE
    if limits.thd_pct is None and not orders:
        verdict = "unknown"
    else:
        verdict = "fail" if failing or thd_fail else "pass"
    notes = []
    if worst is not None:
        notes.append(f"worst order {worst}: {u[worst]:.4g} %")
    if failing:
        notes.append("above the limit at " + ", ".join(f"order {h}" for h in failing))
    if thd_fail:
        notes.append(f"THD {thd:.4g} % above {limits.thd_pct:g} %")
    if verdict == "unknown":
        notes.append("the grid code sets no harmonic limit")
    return {
        "u_h_pct": u, "thd_pct": thd, "thd_limit_pct": limits.thd_pct,
        "thd_margin_pct": None if limits.thd_pct is None else limits.thd_pct - thd,
        "worst_order": worst, "worst_u_h_pct": None if worst is None else u[worst],
        "worst_limit_pct": orders.get(worst) if worst is not None else None,
        "failing_orders": failing, "verdict": verdict, "notes": notes,
    }


def _empty_judgement() -> dict:
    return dict.fromkeys(("u_h_pct", "thd_pct", "thd_limit_pct", "thd_margin_pct", "worst_order",
                          "worst_u_h_pct", "worst_limit_pct"), None) | {"failing_orders": [],
                                                                         "verdict": "unknown"}


def _sk_of(sk_mva_by_poc, poc):
    sk = sk_mva_by_poc.get(poc) if sk_mva_by_poc is not None else None
    try:
        sk = float(sk)
    except (TypeError, ValueError):
        return None
    return sk if math.isfinite(sk) and sk > 0 else None


NO_SK = "No short-circuit level at the point of connection"


def check_units(units: pd.DataFrame, law: str) -> None:
    """Refuse a bad summation law, a units frame without the screen's
    columns, or a unit named twice."""
    alpha(5, law)                                   # refuse a bad law up front
    for col in ("unit", "poc", "rated_mva", "harmonic_source", "harmonic_spectrum"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    if units["unit"].duplicated().any():
        raise ContractError(f"duplicate unit(s): {sorted(units.loc[units['unit'].duplicated(), 'unit'])}")


def unit_inputs(rec: dict) -> tuple:
    """A unit record's ``(rated_mva or NaN, spectrum or None, spectrum
    source, missing inputs)``; ``missing`` empty means the unit can be
    judged."""
    spectrum, source = unit_spectrum(rec["harmonic_source"], rec["harmonic_spectrum"])
    try:
        mva = float(rec["rated_mva"])
    except (TypeError, ValueError):
        mva = math.nan
    missing = []
    if not (math.isfinite(mva) and mva > 0):
        missing.append("rated_mva")
    if spectrum is None:
        missing.append(source)
    return mva, spectrum, source, missing


def harmonic_table(units: pd.DataFrame, sk_mva_by_poc: dict, limits: Limits,
                   law: str = "iec") -> HarmonicStudy:
    """Per-unit and per-PoC harmonic distortion.

    ``units``: ``unit``, ``poc``, ``rated_mva`` (NaN/None for not given),
    ``harmonic_source`` (one of ``TOPOLOGIES`` or empty), ``harmonic_spectrum``
    (``"5: 20, 7: 14"``, a dict, or empty) and ``post_filter`` (whether the
    spectrum already includes the unit's filter). ``sk_mva_by_poc``: S_k (MVA)
    by PoC; a PoC with none is ``unknown``."""
    check_units(units, law)
    limits = limits or Limits()

    unit_rows, by_poc = [], {}
    for rec in units.to_dict("records"):
        poc, name = rec["poc"], rec["unit"]
        post = bool(rec.get("post_filter")) if pd.notna(rec.get("post_filter")) else False
        mva, spectrum, source, missing = unit_inputs(rec)
        sk = _sk_of(sk_mva_by_poc, poc)
        row = {"poc": poc, "unit": name, "rated_mva": mva if math.isfinite(mva) else None,
               "harmonic_source": rec["harmonic_source"] or "", "spectrum_source": source,
               "post_filter": post, "sk_mva": sk, "label": LABEL}
        entry = by_poc.setdefault(poc, {"known": [], "missing": [], "names": []})
        entry["names"].append(name)
        if missing:
            row.update(_empty_judgement(), note="no input: " + "; ".join(missing))
            entry["missing"].append(name)
        else:
            entry["known"].append((mva, spectrum))
            notes = [f"spectrum: {source}" + (", post-filter" if post else ", not post-filter")]
            if sk is None:
                row.update(_empty_judgement())
                notes.append(NO_SK)
            else:
                j = _judge(_u_h([(mva, spectrum)], sk, law), limits)
                notes += j.pop("notes")
                row.update(j)
            row["note"] = "; ".join(notes)
        unit_rows.append(row)

    poc_rows = []
    for poc, entry in by_poc.items():
        sk = _sk_of(sk_mva_by_poc, poc)
        row = {"poc": poc, "units": entry["names"], "no_input_units": entry["missing"], "law": law,
               "sk_mva": sk, "label": LABEL}
        if sk is None:
            row.update(_empty_judgement(), note=NO_SK)
            poc_rows.append(row)
            continue
        j = _judge(_u_h(entry["known"], sk, law), limits)
        notes = j.pop("notes")
        if entry["missing"]:
            if j["verdict"] != "fail":
                j["verdict"] = "unknown"
            notes.append("no input for " + ", ".join(map(str, entry["missing"]))
                         + ": a missing unit blocks a pass")
        row.update(j, note="; ".join(notes))
        poc_rows.append(row)

    return HarmonicStudy(units=pd.DataFrame(unit_rows, columns=list(UNIT_COLUMNS)),
                         pocs=pd.DataFrame(poc_rows, columns=list(POC_COLUMNS)))


def limits_from_profile(profile: dict | None) -> tuple[Limits, list]:
    """The harmonic limits of a grid-code profile and where they come from:
    ``(Limits, [{"key", "clause", "source"}])``. Either key may be absent;
    a profile with neither sets no limit (every verdict ``unknown``)."""
    profile = profile or {}
    thd, orders, cited = None, None, []
    lim = profile.get("thd_limit_pct")
    if isinstance(lim, dict):
        thd = float(lim["value"])
        cited.append({"key": "thd_limit_pct", "clause": lim.get("clause"), "source": lim.get("source")})
    lim = profile.get("harmonic_limits_pct")
    if isinstance(lim, dict):
        orders = {int(h): float(v) for h, v in lim["orders"].items()}
        cited.append({"key": "harmonic_limits_pct", "clause": lim.get("clause"), "source": lim.get("source")})
    return Limits(thd_pct=thd, orders_pct=orders), cited
