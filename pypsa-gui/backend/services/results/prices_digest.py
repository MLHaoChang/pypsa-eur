"""LC-03: a bounded digest of a solved network's prices.

The Prices tab's read (`prices`) is a per-snapshot, per-bus table, far over a
run-evidence section at a year of hours. A past run keeps this digest
instead, with the tab's other read (`price_drivers`, the route's own payload)
beside it: overall and per bus carrier the weighted mean, median, p90, max
and min (Compare's rule, `compare.prices.weighted_stats`), per bus the
weighted mean, max with its snapshot, and min; and the load-weighted mean.

Prices are the merit-order-corrected duals every other price surface reads
(`corrected_marginal_prices`). Every weight is `snapshot_weightings.objective`;
the load-weighted mean weights each price by that bus's load (`loads_t.p`,
negative load counted as none) times it. A bus without load is left out of
the load weighting but kept in the plain statistics; no load at all gives a
null load-weighted mean with its reason. inf is null; a NaN dual is 0, as
on every price surface (the shared merit-order correction fills it).

Buses are ordered by max price, then name. Until the section fits
`BUDGET_BYTES`, buses and price_drivers rows (by |price|, as the route sorts
them) are trimmed from the tail, the longer list first, then carriers; what
was trimmed is counted (`buses_omitted`, `rows_omitted`, `carriers_omitted`). Multi-period networks are not digested.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from services.compare.prices import weighted_stats
from services.period_utils import is_multi_period, snapshot_weights
from services.results.dispatch_digest import BUDGET_BYTES, _num, _size
from services.results.load_frames import corrected_marginal_prices

_SLACK = 64   # the omitted counters' digits grow while trimming


def _stats(values: np.ndarray, weights: np.ndarray) -> dict:
    """Weighted mean, median, p90 and the max/min of the finite values."""
    keep = np.isfinite(values)
    values, weights = values[keep], weights[keep]
    if values.size == 0 or weights.sum() <= 0:
        return {'mean_price': None, 'median_price': None, 'p90_price': None,
                'max_price': None, 'min_price': None}
    mean, median, p90 = weighted_stats(values, weights)
    return {'mean_price': _num(mean), 'median_price': _num(median), 'p90_price': _num(p90),
            'max_price': _num(values.max()), 'min_price': _num(values.min())}


def _load_weighted(prices: np.ndarray, load: np.ndarray, w: np.ndarray) -> float | None:
    """Σ price·load·w / Σ load·w over the finite cells; None without load."""
    lw = load * w.reshape(-1, 1)
    keep = np.isfinite(prices) & np.isfinite(lw) & (lw > 0)
    total = lw[keep].sum()
    return _num((prices[keep] * lw[keep]).sum() / total) if total > 0 else None


def _bus_load(n, prices: pd.DataFrame, result_df) -> pd.DataFrame:
    """Load per bus and snapshot (MW, >= 0), on the price frame's axes."""
    loads = n.loads
    p = result_df(n, 'loads_t', 'p', 'lopf') if result_df is not None else None
    if p is None or p.empty:
        p = getattr(n.loads_t, 'p', None)
    if p is None or p.empty:
        p = n.get_switchable_as_dense('Load', 'p_set') if not loads.empty else None
    if p is None or p.empty or 'bus' not in loads.columns:
        return pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    p = p[[c for c in p.columns if c in loads.index]].astype(float).clip(lower=0.0)
    by_bus = p.T.groupby(loads['bus'].reindex(p.columns)).sum().T
    return by_bus.reindex(index=prices.index, columns=prices.columns).fillna(0.0)


def _trim(digest: dict) -> dict:
    """Drop the last buses and price_drivers rows, then carriers, until it fits.

    Each row is measured once and dropped against a running total, then the
    whole digest is measured once more. Of buses and price_drivers rows the
    longer list loses its last row; on a tie the buses do.
    """
    drivers = digest['price_drivers'] if isinstance(digest['price_drivers'], dict) else {}
    stages = (((digest['prices'], 'buses', 'buses_omitted'), (drivers, 'rows', 'rows_omitted')),
              ((digest['prices'], 'by_carrier', 'carriers_omitted'),))
    while (excess := _size(digest) + _SLACK - BUDGET_BYTES) > 0:
        dropped = False
        for stage in stages:
            lists = [(h[f], h, c) for h, f, c in stage if isinstance(h.get(f), list)]
            cost = [[_size(r) + 2 for r in rows] for rows, _, _ in lists]   # + ", "
            while excess > 0 and lists:
                i = max(range(len(lists)), key=lambda j: (len(lists[j][0]), -j))
                rows, holder, counter = lists[i]
                if not rows:
                    break
                rows.pop()
                excess -= cost[i].pop()
                holder[counter] = holder.get(counter, 0) + 1
                dropped = True
        if not dropped:
            break
    return digest


def prices_digest(n, *, result_df=None, price_drivers: Any = None) -> dict:
    """The digest, or {'status': 'unavailable', 'reason': ...}.

    `result_df` reads the LOPF-stage frames of the live network (the router's
    `_result_df`); without it the network's own frames are read. `price_drivers`
    is the route's payload for that read, kept as it is (trimmed rows counted).
    """
    if is_multi_period(n):
        return {'status': 'unavailable', 'reason': 'multi_period_not_digested'}
    sns = n.snapshots
    prices = corrected_marginal_prices(n, from_state=result_df is not None, result_df=result_df)
    if prices is None or prices.empty:
        return {'status': 'unavailable', 'reason': 'no_prices'}
    prices = prices.reindex(sns).astype(float)
    w = snapshot_weights(n, 'objective').reindex(sns).fillna(0.0).astype(float)
    wv, pv = w.to_numpy(), prices.to_numpy()
    load = _bus_load(n, prices, result_df).to_numpy()
    full_w = np.broadcast_to(wv.reshape(-1, 1), pv.shape)

    buses = n.buses
    carrier = (buses['carrier'].reindex(prices.columns).fillna('').astype(str).str.lower()
               .replace('', 'unspecified') if 'carrier' in buses.columns
               else pd.Series('unspecified', index=prices.columns))

    lwm = _load_weighted(pv, load, wv)
    overall = {**_stats(pv.reshape(-1), full_w.reshape(-1)), 'load_weighted_mean': lwm}
    if lwm is None:
        overall['load_weighted_mean_reason'] = 'no_load'

    by_carrier = []
    for c in sorted(set(carrier)):
        cols = np.flatnonzero((carrier == c).to_numpy())
        sub = pv[:, cols]
        by_carrier.append({'name': c, 'bus_count': int(cols.size),
                           **_stats(sub.reshape(-1), full_w[:, cols].reshape(-1)),
                           'load_weighted_mean': _load_weighted(sub, load[:, cols], wv)})
    by_carrier.sort(key=lambda r: (-r['bus_count'], r['name']))

    rows = []
    for i, bus in enumerate(prices.columns):
        col = pv[:, i]
        finite = np.isfinite(col)
        stats = _stats(col, wv)
        peak = int(np.argmax(np.where(finite, col, -np.inf))) if finite.any() else None
        rows.append({'name': str(bus), 'carrier': carrier.iloc[i],
                     'mean_price': stats['mean_price'], 'max_price': stats['max_price'],
                     'max_snapshot': str(sns[peak]) if peak is not None else None,
                     'min_price': stats['min_price'],
                     'load_weighted_mean': _load_weighted(pv[:, [i]], load[:, [i]], wv)})
    rows.sort(key=lambda r: (-(r['max_price'] if r['max_price'] is not None else -np.inf), r['name']))

    drivers = dict(price_drivers) if isinstance(price_drivers, dict) else price_drivers
    if isinstance(drivers, dict) and isinstance(drivers.get('rows'), list):
        drivers = {**drivers, 'rows': list(drivers['rows']), 'rows_omitted': 0}
    digest = {
        'status': 'ok', 'shape': 'digest', 'weighting': 'snapshot_weightings.objective',
        'snapshots': len(sns), 'weighted_hours': _num(w.sum()),
        'prices': {'basis': 'merit_order_corrected',
                   'duals_all_zero': bool(np.allclose(np.nan_to_num(pv), 0.0)),
                   'bus_count': int(prices.shape[1]), 'overall': overall,
                   'by_carrier': by_carrier, 'buses': rows,
                   'buses_omitted': 0, 'carriers_omitted': 0},
        'price_drivers': drivers,
    }
    return _trim(digest)


_ENVELOPE = ('status', 'shape', 'weighting', 'snapshots', 'weighted_hours')
# What a kept digest answers, named when a run_id read asks for more.
FIELDS = ('overall', 'by_carrier', 'buses', 'mean_price', 'median_price', 'p90_price',
          'max_price', 'max_snapshot', 'min_price', 'load_weighted_mean', 'duals_all_zero')


def for_kind(digest: dict, kind: str) -> Any:
    """One read's answer: the prices digest, or price_drivers as the route served it."""
    if kind == 'price_drivers':
        return digest.get('price_drivers')
    return {**{k: digest.get(k) for k in _ENVELOPE}, **digest[kind]}
