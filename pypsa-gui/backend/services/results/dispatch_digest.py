"""LC-03: a bounded digest of a solved network's dispatch.

The Dispatch tab's reads (`generators`, `storage_dispatch`) are per-snapshot
tables, far over a run-evidence section at a year of hours. A past run keeps
this digest instead: per carrier and per unit, weighted energy, peak and its
snapshot, weighted mean and running hours. Storage keeps discharge and charge
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


def _flow(p: pd.Series, w: pd.Series) -> dict:
    """Weighted energy, peak, weighted mean and running hours of a >= 0 series."""
    p = p.fillna(0.0)
    peak = p.max() if len(p) else None
    on = p > _ON_MW
    return {
        'energy_mwh': _num((p * w).sum()),
        'peak_mw': _num(peak),
        'peak_snapshot': str(p.idxmax()) if _num(peak) is not None and peak > _ON_MW else None,
        'mean_mw': _num((p * w).sum() / w.sum()) if w.sum() > 0 else None,
        'hours': _num(w[on].sum()),
    }


def _storage_row(p: pd.Series, w: pd.Series) -> dict:
    out, into = _flow(p.clip(lower=0.0), w), _flow((-p).clip(lower=0.0), w)
    return {'energy_out_mwh': out['energy_mwh'], 'energy_in_mwh': into['energy_mwh'],
            'peak_discharge_mw': out['peak_mw'], 'peak_discharge_snapshot': out['peak_snapshot'],
            'peak_charge_mw': into['peak_mw'], 'peak_charge_snapshot': into['peak_snapshot'],
            'hours_discharging': out['hours'], 'hours_charging': into['hours']}


def _part(static: pd.DataFrame, frame: pd.DataFrame | None, sns, w: pd.Series, row, key: str) -> dict:
    """Per-carrier and per-unit rows of one component, largest `key` first."""
    if frame is None or frame.empty:
        return {'by_carrier': [], 'units': [], 'units_omitted': 0, 'carriers_omitted': 0}
    frame = frame[[c for c in frame.columns if c in static.index]].reindex(sns).astype(float)
    carrier = (static['carrier'] if 'carrier' in static.columns
               else pd.Series('unknown', index=static.index)).astype(str).str.lower()

    def order(rows):
        return sorted(rows, key=lambda r: (-(r[key] or 0.0), r['name']))

    if frame.columns.empty:
        by_carrier = []
    else:
        grouped = frame.T.groupby(carrier.reindex(frame.columns)).sum().T
        by_carrier = order([{'name': c, **row(grouped[c], w)} for c in grouped.columns])
    units = order([{'name': str(u), 'carrier': carrier.get(u), **row(frame[u], w)}
                   for u in frame.columns])
    return {'by_carrier': by_carrier, 'units': units, 'units_omitted': 0, 'carriers_omitted': 0}


def _size(digest: dict) -> int:
    from services import run_evidence   # measured as the store will write it

    return len(run_evidence._json_bytes(digest))


def _trim(digest: dict, parts: tuple[str, ...]) -> dict:
    """Drop the smallest units, then the smallest carriers, until it fits."""
    for field, counter in (('units', 'units_omitted'), ('by_carrier', 'carriers_omitted')):
        while _size(digest) > BUDGET_BYTES:
            # the part holding the most rows of this field loses its last one
            part = max(parts, key=lambda k: len(digest[k][field]))
            if not digest[part][field]:
                break
            digest[part][field].pop()
            digest[part][counter] += 1
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
                            _flow, 'energy_mwh'),
        'storage_dispatch': _part(n.storage_units, getattr(n.storage_units_t, 'p', None), sns, w,
                                  _storage_row, 'energy_out_mwh'),
    }
    return _trim(digest, ('generators', 'storage_dispatch'))


_ENVELOPE = ('status', 'shape', 'weighting', 'snapshots', 'weighted_hours')
# What a kept digest answers, named when a run_id read asks for more.
FIELDS = ('by_carrier', 'units', 'energy_mwh', 'peak_mw', 'peak_snapshot', 'mean_mw', 'hours',
          'energy_out_mwh', 'energy_in_mwh', 'peak_discharge_mw', 'peak_discharge_snapshot',
          'peak_charge_mw', 'peak_charge_snapshot', 'hours_discharging', 'hours_charging')


def for_kind(digest: dict, kind: str) -> dict:
    """One read's slice of the digest (`generators` or `storage_dispatch`)."""
    return {**{k: digest.get(k) for k in _ENVELOPE}, **digest[kind]}
