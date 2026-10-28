"""LC-03: a bounded digest of a solved network's dispatch.

The Dispatch tab's reads (`generators`, `storage_dispatch`) are per-snapshot
tables, far over a run-evidence section at a year of hours. A past run keeps
this digest instead: per carrier and per unit, weighted energy (net, with
any negative output as `absorbed_mwh`), peak and its snapshot, weighted mean
and running hours. Storage keeps discharge and charge
apart, so cycling is not hidden in a net figure.

Energy and hours use `snapshot_weightings.generators` (the basis of the
Results tab and Compare's dispatch by carrier); the mean is the weighted mean.
Units are ordered by energy, then name, and trimmed from the tail until the
digest fits `BUDGET_BYTES`, then carriers the same way; what was trimmed is
counted. Multi-period networks are not digested.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from services.period_utils import is_multi_period, snapshot_weights

BUDGET_BYTES = 12 * 1024   # under run_evidence.MAX_SECTION_BYTES with room for the envelope
_ON_MW = 1e-6               # below this a unit counts as idle (solver noise)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _flow(frame: pd.DataFrame, w: pd.Series) -> pd.DataFrame:
    """Per column of a >= 0 frame: weighted energy, peak and its snapshot,
    weighted mean and running hours (vectorised: thousands of units are cheap)."""
    frame = frame.fillna(0.0)
    energy = frame.mul(w, axis=0).sum()
    peak = frame.max()
    return pd.DataFrame({
        'energy_mwh': energy,
        'peak_mw': peak,
        'peak_snapshot': frame.idxmax().astype(str).where((peak > _ON_MW) & np.isfinite(peak)),
        'mean_mw': energy / w.sum() if w.sum() > 0 else float('nan'),
        'hours': (frame > _ON_MW).astype(float).mul(w, axis=0).sum(),
    })


def _generator_rows(p: pd.DataFrame, w: pd.Series) -> pd.DataFrame:
    """Energy is net of negative output (Compare's dispatch by carrier sums the
    signed p too); `absorbed_mwh` is that negative part, so it is not hidden.
    Peak, mean and hours describe the output (p > 0) only."""
    out = _flow(p.clip(lower=0.0), w)
    out['energy_mwh'] = p.fillna(0.0).mul(w, axis=0).sum()
    out['mean_mw'] = out['energy_mwh'] / w.sum() if w.sum() > 0 else float('nan')
    out['absorbed_mwh'] = (-p).clip(lower=0.0).fillna(0.0).mul(w, axis=0).sum()
    return out


def _storage_rows(p: pd.DataFrame, w: pd.Series) -> pd.DataFrame:
    out, into = _flow(p.clip(lower=0.0), w), _flow((-p).clip(lower=0.0), w)
    return pd.DataFrame({
        'energy_out_mwh': out['energy_mwh'], 'energy_in_mwh': into['energy_mwh'],
        'peak_discharge_mw': out['peak_mw'], 'peak_discharge_snapshot': out['peak_snapshot'],
        'peak_charge_mw': into['peak_mw'], 'peak_charge_snapshot': into['peak_snapshot'],
        'hours_discharging': out['hours'], 'hours_charging': into['hours']})


def _records(stats: pd.DataFrame, **extra) -> list[dict]:
    """One row per index entry; NaN/inf (and a missing snapshot) become None."""
    return [{'name': str(name), **{k: f(name) for k, f in extra.items()},
             **{k: x if isinstance(x, str) else _num(x) for k, x in r.items()}}
            for name, r in stats.iterrows()]


def _part(static: pd.DataFrame, frame: pd.DataFrame | None, sns, w: pd.Series, rows, key: str) -> dict:
    """Per-carrier and per-unit rows of one component, largest `key` first."""
    empty = {'by_carrier': [], 'units': [], 'units_omitted': 0, 'carriers_omitted': 0}
    if frame is None or frame.empty:
        return empty
    frame = frame[[c for c in frame.columns if c in static.index]].reindex(sns).astype(float)
    if frame.columns.empty:
        return empty
    carrier = (static['carrier'] if 'carrier' in static.columns
               else pd.Series('unknown', index=static.index)).astype(str).str.lower()

    def order(recs):
        return sorted(recs, key=lambda r: (-(r[key] or 0.0), r['name']))

    grouped = frame.T.groupby(carrier.reindex(frame.columns)).sum().T
    return {'by_carrier': order(_records(rows(grouped, w))),
            'units': order(_records(rows(frame, w), carrier=lambda u: carrier.get(u))),
            'units_omitted': 0, 'carriers_omitted': 0}


def _size(value: Any) -> int:
    from services import run_evidence   # measured as the store will write it

    return len(run_evidence._json_bytes(value))


_SLACK = 64   # the omitted counters' digits grow while trimming


def _trim(digest: dict, parts: tuple[str, ...]) -> dict:
    """Drop the smallest units, then the smallest carriers, until it fits.

    Each row is measured once and dropped against a running total, then the
    whole digest is measured once more. The part holding the most rows of a
    field loses its last row; on a tie the first part (generators) does.
    """
    while (excess := _size(digest) + _SLACK - BUDGET_BYTES) > 0:
        dropped = False
        for field, counter in (('units', 'units_omitted'), ('by_carrier', 'carriers_omitted')):
            cost = {k: [_size(r) + 2 for r in digest[k][field]] for k in parts}   # + ", "
            while excess > 0:
                part = max(parts, key=lambda k: len(digest[k][field]))
                if not digest[part][field]:
                    break
                digest[part][field].pop()
                excess -= cost[part].pop()
                digest[part][counter] += 1
                dropped = True
        if not dropped:
            break
    return digest


def dispatch_digest(n) -> dict:
    """The digest, or {'status': 'unavailable', 'reason': ...}."""
    if is_multi_period(n):
        return {'status': 'unavailable', 'reason': 'multi_period_not_digested'}
    sns = n.snapshots
    w = snapshot_weights(n, 'generators').reindex(sns).fillna(0.0).astype(float)
    digest = {
        'status': 'ok', 'shape': 'digest', 'weighting': 'snapshot_weightings.generators',
        'snapshots': len(sns), 'weighted_hours': _num(w.sum()),
        'generators': _part(n.generators, getattr(n.generators_t, 'p', None), sns, w,
                            _generator_rows, 'energy_mwh'),
        'storage_dispatch': _part(n.storage_units, getattr(n.storage_units_t, 'p', None), sns, w,
                                  _storage_rows, 'energy_out_mwh'),
    }
    return _trim(digest, ('generators', 'storage_dispatch'))


_ENVELOPE = ('status', 'shape', 'weighting', 'snapshots', 'weighted_hours')
# What a kept digest answers, named when a run_id read asks for more.
FIELDS = ('by_carrier', 'units', 'energy_mwh', 'absorbed_mwh', 'peak_mw', 'peak_snapshot', 'mean_mw', 'hours',
          'energy_out_mwh', 'energy_in_mwh', 'peak_discharge_mw', 'peak_discharge_snapshot',
          'peak_charge_mw', 'peak_charge_snapshot', 'hours_discharging', 'hours_charging')


def for_kind(digest: dict, kind: str) -> dict:
    """One read's slice of the digest (`generators` or `storage_dispatch`)."""
    return {**{k: digest.get(k) for k in _ENVELOPE}, **digest[kind]}
