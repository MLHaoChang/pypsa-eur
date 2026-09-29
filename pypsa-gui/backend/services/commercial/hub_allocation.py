"""
Energy-hub allocation inputs: what each member of a group connection is charged
of the group's bill (Edge Investment Case P3 WP3.3a; spec §7).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.3a.

`period_hub(...)` reads ONE billing period of a group connection — each
member's import, the group's export, the rating arguments the group bill used
and the group's own `RatingResult` — and returns a `participants.HubPeriod`:

  * `metered` — the linear import items (per kWh, measured on import, no tiers:
    energy, levies, certificates) rated per member through `tariff_engine.rate`
    with a one-item tariff and the group bill's own `billing_period` /
    `represents_hours`, read like the ledger's bill (`per_item_sampled`). A
    share key would misallocate a TOU rate; a per-kWh levy is metered for the
    same reason (plan deviation, recorded: the plan names energy items only);
  * `peak` — each demand item's month-and-window amounts from the group bill's
    `demand_lines`, split by the members' import in the group's billed
    interval (the engine's `interval_key` settlement-interval means; ties →
    the first maximal interval). Where a ratchet floor binds, the split is
    the contributions in the month that SET the floor: the argmax over the
    same candidate months the engine reads (`_ratchet_floor_prior`'s three
    modes), earliest on ties, checked against the engine's floor — a floor set
    by meter history (no modelled month) is `allocation_not_established`;
  * `energy_mwh` — each member's Σ w·p0 in the period (the `energy` key; not
    the years-weighted `ic_group.energy_share`).

The engine is P2-gated: this module reads its helpers and never changes them.
Amounts are site view (+ the group pays). Pure: no network, no routers, no
`solver_service`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.commercial import tariff_engine as TE
from services.commercial.participants import HubPeriod

METERED_KINDS = ("energy", "tax_levy", "certificate")
_FREQ = {"15min": "15min", "30min": "30min", "h": "h"}


def is_metered(item) -> bool:
    """A linear import item: rated per member, never keyed."""
    return (item.unit == "per_kwh" and item.measured_on == "import" and not item.tiers
            and item.kind in METERED_KINDS)


def is_peak_item(item) -> bool:
    """A demand item the `peak_contribution` key splits by the billed interval
    of the members' IMPORT. A capacity item on the annual peak is not one, nor
    an export-measured demand item — the members' import says nothing about an
    export peak (WP3.3a review #3): both fall back to energy, disclosed."""
    return item.kind != "capacity" and item.measured_on != "export" and TE._is_demand(item)


def floor_source_month(ratchet, month: str, k: int, charged: dict) -> str | None:
    """The modelled month whose actual peak the ratchet floor of (`month`,
    window `k`) reads — the same candidate months as
    `tariff_engine._ratchet_floor_prior` — the argmax, earliest on ties; None
    when no modelled candidate month has a peak (the floor is meter history)."""
    here = pd.Period(month, freq="M")
    year, mm = int(month[:4]), int(month[5:])
    if ratchet.months is not None:
        wanted = [f"{year}-{d:02d}" for d in ratchet.months]
    elif ratchet.cyclic_year:
        wanted = [f"{year}-{((mm - back - 1) % 12) + 1:02d}"
                  for back in range(1, ratchet.lookback_months + 1)]
    else:
        wanted = [(here - back).strftime("%Y-%m")
                  for back in range(1, ratchet.lookback_months + 1)]
    found = sorted((m, charged[(m, k)]) for m in set(wanted) if (m, k) in charged)
    if not found:
        return None
    best = max(v for _m, v in found)
    return next(m for m, v in found if v == best)


def _interval_means(interval: np.ndarray, kw: np.ndarray, dur: np.ndarray) -> pd.Series:
    g = pd.DataFrame({"g": interval, "qd": kw * dur, "d": dur, "nan": np.isnan(kw)})
    agg = g.groupby("g", sort=True).agg(qd=("qd", "sum"), d=("d", "sum"), nan=("nan", "any"))
    return pd.Series(np.where(agg["nan"].to_numpy(), np.nan,
                              agg["qd"].to_numpy() / agg["d"].to_numpy()), index=agg.index)


def _charged(item, rate_k, frag_rows) -> bool:
    """The engine's `_charged`: a window bills (and sets a ratchet) only if its
    rate — or, tiered, any of its tier rates — is non-zero."""
    if item.tiers:
        rates = (item.periods[int(frag_rows[0])].tier_rates if TE.is_windowed_tiered(item)
                 else [t.rate for t in item.tiers])
        return any(r != 0 for r in rates)
    return rate_k != 0


def _peak_split(item, group, members: dict[str, np.ndarray], export: np.ndarray, idx,
                local, dur, month_key) -> tuple[dict[str, float] | None, str | None]:
    """(participant → amount of the item, None) or (None, reason)."""
    dl = group.demand_lines
    rows = dl[dl["tariff_item"] == item.id] if len(dl) else dl
    if group.per_item_sampled.get(item.id) is None:
        return None, "group_item_not_established"
    imp = np.sum(list(members.values()), axis=0)
    if item.measured_on == "net":
        net = imp - export
        q_kw = (net if item.direction == "cost" else -net) * TE._KWH_PER_MWH
    elif item.measured_on == "peak_import":
        q_kw = imp * TE._KWH_PER_MWH
    else:
        q_kw = TE._quantity_mw(item, imp, export) * TE._KWH_PER_MWH
    interval = TE.interval_key(local, _FREQ[item.settlement])
    q_int = _interval_means(interval, q_kw, dur)
    if item.measured_on == "net":
        q_int = q_int.clip(lower=0.0)
    m_int = {p: _interval_means(interval, v * TE._KWH_PER_MWH, dur) for p, v in members.items()}
    first = pd.Series(np.arange(len(interval))).groupby(interval).first()
    g_month = month_key[first.to_numpy()]
    window, names, frag = TE.demand_windows(item, local)
    g_win = window[first.to_numpy()]
    g_frag = frag[first.to_numpy()]
    keys = q_int.index.to_numpy()
    sels: dict[tuple[str, int], np.ndarray] = {}
    charged: dict[tuple[str, int], float] = {}
    for _, r in rows.iterrows():
        k = names.index(str(r["period"]))
        sel = (g_month == str(r["month"])) & (g_win == k)
        sels[(str(r["month"]), k)] = sel
        rate_k = TE.window_rate(item, g_frag[sel])
        if _charged(item, rate_k, g_frag[sel]) and np.isfinite(r["peak_kw"]):
            charged[(str(r["month"]), k)] = float(r["peak_kw"])
    out = {p: 0.0 for p in members}
    for _, r in rows.iterrows():
        amount, peak, billed = float(r["amount"]), float(r["peak_kw"]), float(r["billed_kw"])
        if not (np.isfinite(amount) and np.isfinite(peak) and np.isfinite(billed)):
            return None, "demand_line_unknown"
        if amount == 0:
            continue
        month, k = str(r["month"]), names.index(str(r["period"]))
        src = month
        if item.ratchet is not None and billed > peak * (1 + 1e-12) + 1e-9:
            src = floor_source_month(item.ratchet, month, k, charged)
            if src is None or abs(item.ratchet.share * charged[(src, k)] - billed) > \
                    1e-9 * max(1.0, billed):
                return None, "ratchet_floor_from_meter_history"
        sel = sels.get((src, k))
        if sel is None or not sel.any():
            return None, "billed_interval_not_found"
        vals = q_int.to_numpy()[sel]
        j = int(np.argmax(vals))                    # the first maximal interval
        expected = peak if src == month else charged[(src, k)]
        if abs(vals[j] - expected) > 1e-6 * max(1.0, abs(expected)):
            return None, "billed_interval_not_found"
        at = keys[sel][j]
        c = {p: float(s.loc[at]) for p, s in m_int.items()}
        if any(not np.isfinite(v) for v in c.values()):
            return None, "member_import_unknown"
        total = sum(c.values())
        if total <= 0:
            return None, "no_member_import_in_billed_interval"
        for p, v in c.items():
            out[p] += amount * v / total
    return out, None


def period_hub(idx: pd.DatetimeIndex, members: dict[str, np.ndarray], export: np.ndarray,
               weights: np.ndarray, tariff, *, step_hours, timezone: str | None,
               billing_period, represents_hours, group, peak: bool = True) -> HubPeriod:
    """One period's allocation inputs (see the module notes). `members`:
    participant → import MW per row (every group member's); `group`: the
    group bill's `RatingResult` for the period (None: not billed); `peak`:
    whether the key reads the peak split (only `peak_contribution` does)."""
    flags: list[str] = []
    energy = {p: (None if np.isnan(v).any() else float((weights * v).sum()))
              for p, v in members.items()}
    metered: dict[str, dict[str, float | None] | None] = {}
    splits: dict[str, dict[str, float] | None] = {}
    reasons: dict[str, str] = {}
    if tariff is None or group is None:
        return HubPeriod(energy_mwh=energy, metered=metered, peak=splits, flags=flags,
                         reason="group_bill_not_established")
    local = idx.tz_convert(timezone) if idx.tz is not None else idx
    dur = TE._durations(idx, step_hours)
    month_key = np.asarray(local.strftime("%Y-%m"))
    linear = [item for item in tariff.items if is_metered(item)]
    if linear:
        # One rating per member with every linear item (review #5): per item
        # sampled, as the group bill is read.
        many = tariff.model_copy(update={"items": linear, "unsupported_fields": []})
        by_member = {}
        for p, v in members.items():
            res = TE.rate(pd.DataFrame({"import_mw": v, "export_mw": np.zeros(len(v))},
                                       index=idx), many, step_hours=step_hours,
                          timezone=timezone, billing_period=billing_period,
                          represents_hours=represents_hours)
            by_member[p] = res.per_item_sampled
        for item in linear:
            metered[item.id] = {p: by_member[p].get(item.id) for p in members}
    if peak:
        for item in tariff.items:
            if not is_peak_item(item):
                continue
            split, reason = _peak_split(item, group, members, export, idx, local, dur,
                                        month_key)
            splits[item.id] = split
            if reason:
                reasons[item.id] = reason
                flags.append(f"allocation_not_established:{item.id}:{reason}")
    return HubPeriod(energy_mwh=energy, metered=metered, peak=splits, flags=flags,
                     peak_reason=reasons)
