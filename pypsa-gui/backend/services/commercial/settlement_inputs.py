"""
Settlement inputs committed with a solve (Edge Investment Case P2 WP2.2-0).

Part a — the DSR dispatch record. The per-bus DSR slack generators are
transient (`services/solver/assumptions.py` adds them and captures their
dispatch in the undo, which also runs on failed and sweep solves). The
captured frame (columns = bus names) is committed here as
`buses_t["ic_dsr_p"]` with `meta["ic_dsr"] = {axis_hash, total_mwh}` ONLY
after a successful, non-operational solve (the P1 `_ic_operational` rule: an
adequacy sweep solve is not the design), and cleared after a successful solve
without DSR. The custom `buses_t` frame rides network.nc. A DR contract's
activation reads it through `dsr_activation`; a missing record or one made on
another snapshot axis is `None` + `dr_activation_not_established` (ADR-0001).

Part b — reference series and the `ic:` namespace. A contract's reference
price and the config's `grid_cfe_share_ref` are Library series resolved at
`PUT /solver_config` (`binding.bind_commercial`, the export price's path) and
written, fully covered only, as `buses_t["ic_ref_price"]` (column
`ic:contract:<id>`) and `buses_t["ic_grid_cfe_share"]` (column `ic:cfe:grid`)
with `meta["ic_ref_series"][column] = {ref, axis_hash}`. The columns are not
bus names: bus names starting `ic:` are refused on every user entry point
(`reserved_bus_name`); the frames survive netCDF, `copy()`, bus removal and
rename; PyPSA's load warning about them is filtered (`install_log_filter`).
Readers return the series or `None` + `reference_price_missing` /
`grid_cfe_share_missing` (not bound, another axis, NaN), plus
`reference_changed_since_binding` when the config names another ref version.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib
import logging

import pandas as pd

DSR_ATTR = "ic_dsr_p"
META_DSR = "ic_dsr"
OPERATIONAL_ATTR = "_ic_operational"  # = connection.OPERATIONAL_ATTR (not imported: no cycle)


def axis_hash(snapshots: pd.Index) -> str:
    raw = "|".join(map(str, snapshots.tolist())).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def commit_dsr(n, dsr_t: pd.DataFrame | None, total_mwh: float | None = None) -> None:
    """Record (or clear) the DSR dispatch after a SUCCESSFUL solve. The caller
    runs it only on success; an operational solve leaves the record alone."""
    if getattr(n, OPERATIONAL_ATTR, False):
        return
    if dsr_t is None or dsr_t.empty:
        if DSR_ATTR in n.buses_t:
            n.buses_t[DSR_ATTR] = pd.DataFrame(index=n.snapshots)
        n.meta.pop(META_DSR, None)
        return
    frame = dsr_t.reindex(n.snapshots).astype(float)
    frame.columns = [str(c) for c in frame.columns]
    n.buses_t[DSR_ATTR] = frame
    n.meta[META_DSR] = {"axis_hash": axis_hash(n.snapshots),
                        "total_mwh": None if total_mwh is None else float(total_mwh),
                        "buses": list(frame.columns)}


def dsr_activation(n) -> tuple[pd.DataFrame | None, list[str]]:
    """(the committed DSR dispatch, MW per snapshot per bus; flags)."""
    rec = (n.meta or {}).get(META_DSR) if hasattr(n, "meta") else None
    frame = n.buses_t.get(DSR_ATTR) if hasattr(n.buses_t, "get") else None
    if not rec or frame is None or frame.empty or rec.get("axis_hash") != axis_hash(n.snapshots):
        return None, ["dr_activation_not_established"]
    # Every column, never the recorded list: a bus rename renames the column
    # AND the loads' bus, so the list would drop it silently (review 0a #1).
    out = frame.reindex(n.snapshots)
    if out.isna().any().any():
        return None, ["dr_activation_not_established"]
    return out, []


# ── reference series (part b) ──────────────────────────────────────────────

REF_PRICE_ATTR = "ic_ref_price"
CFE_ATTR = "ic_grid_cfe_share"
META_REF = "ic_ref_series"
CFE_COLUMN = "ic:cfe:grid"
RESERVED_BUS_PREFIX = "ic:"


def contract_column(contract_id: str) -> str:
    return f"ic:contract:{contract_id}"


def reserved_buses_in_netcdf(path) -> list[str]:
    """Bus names in the `ic:` namespace inside a network.nc, read without
    loading it (checked BEFORE a load resets the live network)."""
    import xarray as xr

    try:
        with xr.open_dataset(path) as ds:
            names = [str(b) for b in ds["buses_i"].values] if "buses_i" in ds else []
    except Exception:  # noqa: BLE001 — an unreadable file fails in the loader itself
        return []
    return [b for b in names if reserved_bus_name(b)]


def reserved_bus_name(name) -> bool:
    """Bus names in the `ic:` namespace would collide with the reference
    frames' columns: refused on create, rename and import."""
    return str(name).startswith(RESERVED_BUS_PREFIX)


def _ref_key(ref) -> dict:
    d = ref if isinstance(ref, dict) else ref.model_dump(mode="json")
    return {"id": d["id"], "version": int(d["version"]), "hash": d["hash"]}


def write_reference_series(n, series_by_column: dict, *, frame: str,
                           keep: set[str] | None = None) -> None:
    """Write fully covered series (aligned to the snapshots) under `frame`,
    recording each column's ref and the axis hash; drop the frame's columns
    not in `keep` (a contract no longer in the config). `keep=None` keeps the
    other columns."""
    current = n.buses_t.get(frame) if hasattr(n.buses_t, "get") else None
    if not series_by_column and (current is None or current.empty):
        return  # nothing to write or prune: no empty frame on every network
    out = (current.copy() if current is not None and not current.empty
           else pd.DataFrame(index=n.snapshots))
    out = out.reindex(n.snapshots)
    meta = dict((n.meta or {}).get(META_REF) or {})
    if keep is not None:
        drop = [c for c in out.columns if c not in keep and c not in series_by_column]
        out = out.drop(columns=drop)
        for c in drop:
            meta.pop(c, None)
    for col, (series, ref) in series_by_column.items():
        out[col] = pd.Series(series, dtype=float).to_numpy()
        meta[col] = {"ref": _ref_key(ref), "axis_hash": axis_hash(n.snapshots)}
    n.buses_t[frame] = out
    n.meta[META_REF] = meta


def _read(n, frame: str, col: str, ref, missing: str) -> tuple[pd.Series | None, list[str]]:
    if ref is None:
        return None, [missing]
    rec = ((n.meta or {}).get(META_REF) or {}).get(col) if hasattr(n, "meta") else None
    df = n.buses_t.get(frame) if hasattr(n.buses_t, "get") else None
    if not rec or df is None or col not in df.columns:
        return None, [missing]
    if rec.get("ref") != _ref_key(ref):
        return None, [missing, "reference_changed_since_binding"]
    if rec.get("axis_hash") != axis_hash(n.snapshots):
        return None, [missing]
    s = df[col].reindex(n.snapshots).astype(float)
    if s.isna().any():
        return None, [missing]
    return s, []


def reference_price(n, contract) -> tuple[pd.Series | None, list[str]]:
    """€/MWh per snapshot of a contract's reference price."""
    return _read(n, REF_PRICE_ATTR, contract_column(contract.id),
                 getattr(contract, "reference_price", None), "reference_price_missing")


def grid_cfe_share(n, cfg) -> tuple[pd.Series | None, list[str]]:
    """The grid's carbon-free share per snapshot."""
    return _read(n, CFE_ATTR, CFE_COLUMN, getattr(cfg, "grid_cfe_share_ref", None),
                 "grid_cfe_share_missing")


class _IcFrameLogFilter(logging.Filter):
    """PyPSA warns on load that the `ic:` columns of `buses_t` frames are not
    buses: documented, expected, dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Only the `ic:` reference columns — a stale P1 `ic_*` column of a
        # removed component still warns (review 0b #5).
        return not ("not in main components dataframe" in msg and "attribute ic_" in msg
                    and "'ic:" in msg)


_FILTER = _IcFrameLogFilter()


def install_log_filter() -> None:
    lg = logging.getLogger("pypsa.network.io")
    if _FILTER not in lg.filters:
        lg.addFilter(_FILTER)


install_log_filter()  # every loader (compare, projects, the runtime path) from first import


# ── the contracts a settlement ran with (WP2.2c) ───────────────────────────
# A settlement is pure on the dispatch and the contracts, so a contract edit
# after the SOLVE is not drift (re-settle freely); WP2.5's settlement record
# stores this and compares it (review 2.2c #5). A dispatch PPA's drift against
# the solve is `ic_ppa` (WP2.2d).

META_CONTRACTS = "ic_contracts"   # legacy key of the pre-review solve record


def contracts_record(cfg) -> dict:
    """The contracts' content hash — order-insensitive (sorted by id) and with
    `site_party` (it decides DR's payee) — and their ids."""
    from services.commercial import hashing as _H

    ordered = sorted(cfg.contracts, key=lambda c: c.id)
    payload = {"contracts": _H.canonical(ordered), "site_party": cfg.site_party}
    return {"hash": _H.digest(payload), "hash_version": _H.HASH_VERSION,
            "ids": [c.id for c in ordered]}


def contracts_state(record: dict | None, cfg) -> str | None:
    """None — `record` (a settlement's) is the config's contracts; "config" —
    they changed since (`config_changed_since_solve` on that settlement);
    "not_recorded" — no record while the config names contracts."""
    if not record:
        return "not_recorded" if cfg.contracts else None
    return None if record.get("hash") == contracts_record(cfg)["hash"] else "config"
