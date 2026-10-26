"""The current-distortion screen at the point of common coupling (PCC)
(datacenter readiness 12, part 1): IEEE 519-2014 total demand distortion.

A second check on the harmonic screen's spectra and summation
(``static/harmonics.py``), not a second spectrum path. The summed harmonic
current per order comes from ``harmonics._i_h``; this module divides it by
the maximum demand current I_L instead of turning it into a voltage:

    I_h %   = 100 * I_h / I_L                       (MVA at the PCC voltage)
    TDD %   = sqrt(sum_h I_h %^2),  h = 2..50
    Isc/IL  = S_sc / I_L                            (picks the limit band)

The limits are IEEE 519-2014's current-distortion tables, chosen by the PCC
voltage: Table 2 up to and including 69 kV, Table 3 above 69 kV up to and
including 161 kV, Table 4 above 161 kV. Each table has rows by Isc/IL; a
ratio exactly on a boundary takes the stricter (lower-ratio) row. The odd
order groups are half-open: 3 <= h < 11, 11 <= h < 17, 17 <= h < 23,
23 <= h < 35 and 35 <= h <= 50. An even order is limited to 25 % of the odd
limit of its group; order 2 takes the first group's.

Generation equipment: IEEE 519 limits it to the lowest Isc/IL row whatever
the actual ratio. A PCC with any unit flagged ``generation`` is judged on
that row, and its note names the units.

The caller passes S_sc and I_L. The campus study passes the PCC's minimum
fault level, while IEEE 519 uses the maximum short-circuit current, so the
ratio is lower and the band stricter than the standard's. It passes the
rated campus load as I_L, not IEEE 519's average of monthly maximum demands.
Both are said in every judged row.

The table values are from memory of IEEE 519-2014 and are not verified
against the text (assumed).

Pure pandas; no engine import.
"""
from __future__ import annotations

import math

import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static import harmonics as hm
from gridspine.templates.grid_codes import CURRENT_TDD_STANDARDS as STANDARDS

TOLERANCE = hm.TOLERANCE

LABEL = "static screen; binding answer is a harmonic load-flow study with the grid's impedance"

#: The odd order groups, half-open [lo, hi): the last is 35 <= h <= 50.
GROUPS = ((3, 11), (11, 17), (17, 23), (23, 35), (35, 51))
EVEN_SHARE = 0.25

#: The upper Isc/IL bound of each row but the last, inclusive (a ratio on a
#: boundary takes the stricter row): Tables 2 and 3, and Table 4.
ISC_IL_BOUNDS = (20.0, 50.0, 100.0, 1000.0)
ISC_IL_BOUNDS_T4 = (25.0, 50.0)


def _labels(bounds: tuple) -> tuple:
    edges = [f"{b:g}" for b in bounds]
    return (f"<= {edges[0]}", *(f"{lo}-{hi}" for lo, hi in zip(edges, edges[1:])), f"> {edges[-1]}")


def _table(name: str, kv_max: float | None, bounds: tuple, rows: tuple) -> tuple:
    return name, kv_max, bounds, tuple((label, odd, tdd) for label, (odd, tdd) in zip(_labels(bounds), rows))


#: IEEE 519-2014 current-distortion limits, % of I_L, by table: its name, the
#: highest PCC voltage it covers (kV, inclusive; None for no ceiling), its
#: Isc/IL bounds and, per row, (label, odd limits by group, TDD). From memory,
#: not verified against the text (assumed).
TABLES = (
    _table("Table 2", 69.0, ISC_IL_BOUNDS, (
        ((4.0, 2.0, 1.5, 0.6, 0.3), 5.0),
        ((7.0, 3.5, 2.5, 1.0, 0.5), 8.0),
        ((10.0, 4.5, 4.0, 1.5, 0.7), 12.0),
        ((12.0, 5.5, 5.0, 2.0, 1.0), 15.0),
        ((15.0, 7.0, 6.0, 2.5, 1.4), 20.0),
    )),
    _table("Table 3", 161.0, ISC_IL_BOUNDS, (
        ((2.0, 1.0, 0.75, 0.3, 0.15), 2.5),
        ((3.5, 1.75, 1.25, 0.5, 0.25), 4.0),
        ((5.0, 2.25, 2.0, 0.75, 0.35), 6.0),
        ((6.0, 2.75, 2.5, 1.0, 0.5), 7.5),
        ((7.5, 3.5, 3.0, 1.25, 0.7), 10.0),
    )),
    _table("Table 4", None, ISC_IL_BOUNDS_T4, (
        ((1.0, 0.5, 0.38, 0.15, 0.1), 1.5),
        ((2.0, 1.0, 0.75, 0.3, 0.15), 2.5),
        ((3.0, 1.5, 1.15, 0.45, 0.22), 3.75),
    )),
)

LEDGER = (
    "the band uses the minimum short-circuit level, while IEEE 519 uses the maximum short-circuit "
    "current, so the screen is stricter than the standard",
    "I_L is the rated campus load, not IEEE 519's average of monthly maximum demands",
    "the IEEE 519-2014 table values are from memory and not verified against the text (assumed)",
    "a ratio exactly on a band boundary takes the stricter band",
)
STRICTER = ("band from S_sc,min, while IEEE 519 uses the maximum short-circuit current, so the screen is "
            "stricter than the standard")
I_L_NOTE = "I_L: rated campus load, not IEEE 519's average of monthly maximum demands"

POC_COLUMNS = (
    "poc", "units", "no_input_units", "generation_units", "law", "standard", "vn_kv", "sk_mva", "i_l_mva",
    "isc_il", "table", "band", "i_h_pct", "tdd_pct", "tdd_limit_pct", "tdd_margin_pct", "worst_order",
    "worst_i_h_pct", "worst_limit_pct", "failing_orders", "verdict", "note", "label",
)


def table_for(vn_kv: float) -> tuple:
    """The IEEE 519-2014 table entry for a PCC of ``vn_kv`` kV."""
    v = float(vn_kv)
    if not (math.isfinite(v) and v > 0):
        raise ContractError(f"the PCC voltage must be a positive number of kV, got {vn_kv!r}")
    for entry in TABLES:
        if entry[1] is None or v <= entry[1]:
            return entry
    raise AssertionError("unreachable: the last table has no ceiling")  # pragma: no cover


def band_index(ratio: float, bounds: tuple) -> int:
    """The row of an Isc/IL ``ratio``: a ratio on a boundary takes the lower
    (stricter) row."""
    for i, upper in enumerate(bounds):
        if ratio <= upper:
            return i
    return len(bounds)


def order_limit(h: int, odd_limits: tuple) -> float | None:
    """The limit at order ``h`` (% of I_L) from a row's odd limits by group:
    an even order takes 25 % of its group's, order 2 the first group's. None
    outside 2..50."""
    if not hm.H_MIN <= h <= hm.H_MAX:
        return None
    group = next((i for i, (lo, hi) in enumerate(GROUPS) if lo <= h < hi), 0)
    return odd_limits[group] * (EVEN_SHARE if h % 2 == 0 else 1.0)


def _unknown(row: dict, note: str) -> dict:
    return row | dict.fromkeys(("isc_il", "table", "band", "i_h_pct", "tdd_pct", "tdd_limit_pct",
                                "tdd_margin_pct", "worst_order", "worst_i_h_pct", "worst_limit_pct"), None) | {
        "failing_orders": [], "verdict": "unknown", "note": note}


def _judge(i_h_pct: dict, odd_limits: tuple, tdd_limit: float) -> dict:
    limits = {h: order_limit(h, odd_limits) for h in i_h_pct}
    limited = {h: v for h, v in i_h_pct.items() if limits[h] is not None}
    tdd = math.sqrt(sum(v * v for v in limited.values()))
    worst = max(limited, key=lambda h: (limited[h] / limits[h], -h)) if limited else None
    failing = sorted(h for h, v in limited.items() if v > limits[h] + TOLERANCE)
    tdd_fail = tdd > tdd_limit + TOLERANCE
    notes = []
    if worst is not None:
        notes.append(f"worst order {worst}: {limited[worst]:.4g} % of I_L against {limits[worst]:g} %")
    if failing:
        notes.append("above the limit at " + ", ".join(f"order {h}" for h in failing))
    if tdd_fail:
        notes.append(f"TDD {tdd:.4g} % above {tdd_limit:g} %")
    return {"i_h_pct": limited, "tdd_pct": tdd, "tdd_limit_pct": tdd_limit, "tdd_margin_pct": tdd_limit - tdd,
            "worst_order": worst, "worst_i_h_pct": None if worst is None else limited[worst],
            "worst_limit_pct": None if worst is None else limits[worst], "failing_orders": failing,
            "verdict": "fail" if failing or tdd_fail else "pass", "notes": notes}


def tdd_table(units: pd.DataFrame, sk_mva_by_poc: dict, i_l_mva_by_poc: dict, vn_kv_by_poc: dict,
              standard: str | None, law: str = "iec") -> pd.DataFrame:
    """One row per PCC (``POC_COLUMNS``): the total demand distortion of its
    units' summed harmonic currents against IEEE 519-2014.

    ``units``: as ``harmonics.harmonic_table`` takes them (``unit``, ``poc``,
    ``rated_mva``, ``harmonic_source``, ``harmonic_spectrum``), plus an
    optional ``generation`` flag (missing or NaN is False). ``sk_mva_by_poc``
    is S_sc (MVA), ``i_l_mva_by_poc`` I_L as MVA at the PCC voltage and
    ``vn_kv_by_poc`` the PCC voltage. ``standard`` is one of ``STANDARDS`` or
    None; with None, or with no S_sc or I_L at a PCC, that PCC is
    ``unknown``. A unit with no input blocks a pass at its PCC."""
    hm.alpha(5, law)                                    # refuse a bad law up front
    if standard is not None and standard not in STANDARDS:
        raise ContractError(f"current-distortion standard {standard!r} is not one of {', '.join(STANDARDS)}")
    for col in ("unit", "poc", "rated_mva", "harmonic_source", "harmonic_spectrum"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    if units["unit"].duplicated().any():
        raise ContractError(f"duplicate unit(s): {sorted(units.loc[units['unit'].duplicated(), 'unit'])}")

    by_poc = {}
    for rec in units.to_dict("records"):
        entry = by_poc.setdefault(rec["poc"], {"known": [], "missing": [], "names": [], "generation": []})
        entry["names"].append(rec["unit"])
        gen = rec.get("generation")
        if gen is not None and not (isinstance(gen, float) and math.isnan(gen)) and bool(gen):
            entry["generation"].append(rec["unit"])
        spectrum, _ = hm.unit_spectrum(rec["harmonic_source"], rec["harmonic_spectrum"])
        try:
            mva = float(rec["rated_mva"])
        except (TypeError, ValueError):
            mva = math.nan
        if spectrum is None or not (math.isfinite(mva) and mva > 0):
            entry["missing"].append(rec["unit"])
        else:
            entry["known"].append((mva, spectrum))

    rows = []
    for poc, entry in by_poc.items():
        sk, i_l, vn = (hm._sk_of(by, poc) for by in (sk_mva_by_poc, i_l_mva_by_poc, vn_kv_by_poc))
        row = {"poc": poc, "units": entry["names"], "no_input_units": entry["missing"],
               "generation_units": entry["generation"], "law": law, "standard": standard,
               "vn_kv": vn, "sk_mva": sk, "i_l_mva": i_l, "label": LABEL}
        if standard is None:
            rows.append(_unknown(row, "the grid code names no current-distortion standard"))
            continue
        if vn is None:
            rows.append(_unknown(row, "No voltage at the point of connection, so no IEEE 519 table applies"))
            continue
        if sk is None:
            rows.append(_unknown(row, hm.NO_SK))
            continue
        if i_l is None:
            rows.append(_unknown(row, "No load behind the point of connection, so I_L is unknown"))
            continue
        table, _, bounds, bands = table_for(vn)
        ratio = sk / i_l
        band = 0 if entry["generation"] else band_index(ratio, bounds)
        label, odd_limits, tdd_limit = bands[band]
        i_h = hm._i_h(entry["known"], law)
        j = _judge({h: 100.0 * v / i_l for h, v in i_h.items()}, odd_limits, tdd_limit)
        notes = [f"IEEE 519-2014 {table}, Isc/IL {ratio:.4g}, band {label}", *j.pop("notes"), STRICTER, I_L_NOTE]
        if entry["generation"]:
            notes.insert(1, "generation equipment (" + ", ".join(map(str, entry["generation"]))
                         + ") is limited to the lowest Isc/IL row whatever the ratio")
        if entry["missing"]:
            if j["verdict"] != "fail":
                j["verdict"] = "unknown"
            notes.append("no input for " + ", ".join(map(str, entry["missing"])) + ": a missing unit blocks a pass")
        rows.append(row | j | {"isc_il": ratio, "table": table, "band": label, "note": "; ".join(notes)})
    return pd.DataFrame(rows, columns=list(POC_COLUMNS))


def standard_from_profile(profile: dict | None) -> tuple[str | None, list]:
    """The current-distortion standard a grid-code profile names and where it
    comes from: ``(standard or None, [{"key", "clause", "source"}])``."""
    lim = (profile or {}).get("current_tdd_standard")
    if not isinstance(lim, dict):
        return None, []
    return str(lim["value"]), [{"key": "current_tdd_standard", "clause": lim.get("clause"),
                                "source": lim.get("source")}]
