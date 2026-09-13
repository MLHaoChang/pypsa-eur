"""
Load / generator / link profile routes (list, template download, upload).

Split out of `routers/network.py` after the bulk lift (#32). Same cut as
Phase 5's time-axis sibling: these routes are deep as a cluster (~500 LOC)
and shallow individually elsewhere; the ~80 CRUD factory calls stay put.

Shared helpers that came along:
  `_xlsx_response`         — only the three template downloads use it
  `_apply_profile_upload`  — only the three upload routes use it
  `_LOAD_SHAPES`           — load template dispatch table

`routers/network.py` re-exports every handler here because
`services/chat_tools.py` imports them BY NAME and calls them in-process.
`tests/test_network_profiles_surface.py` pins that. This module never
imports `routers.network` (would be a cycle).
"""
from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from services import change_log_service
from services.http_filenames import content_disposition
from services.profile_shapes import (
    _double_peak_profile,
    _flat_cf_profile,
    _gen_category,
    _h2_load_profile,
    _heat_load_profile,
    _link_category,
    _load_section,
    _profile_meta_for,
    _shape_for_section,
    _solar_cf_profile,
    _template_snapshots,
    _wind_cf_profile,
)
from services.pypsa_service import PyPSAService
from services.transient_rows import filter_transient_names
from services.upload_guard import read_capped
from services.user_timeseries import (
    _ensure_snapshots_cover_user_ts,
    _parse_upload,
    _reapply_user_ts_to_network,
    _reject_nonfinite_timeseries,
    _user_ts,
    _user_ts_lock,
)

router = APIRouter()

# Alias matching routers.network so moved bodies stay byte-identical.
_filter_transient_names = filter_transient_names


def _xlsx_response(df: pd.DataFrame, fname: str) -> StreamingResponse:
    """
    Serialise `df` to an .xlsx StreamingResponse with a safely-encoded
    attachment filename. Shared tail of the load/generator/link profile-template
    download endpoints.

    The filename embeds a COMPONENT NAME, and component names are created
    through `POST /api/network/loads` (and friends) with no character
    validation at all — so a load called `ev"il` used to close the header's
    quoted-string early, and one containing a newline made uvicorn raise
    `RuntimeError: Invalid HTTP header value.` mid-send and the browser get an
    empty reply. `content_disposition` is byte-identical to the old f-string
    for every ordinary template name.
    """
    buf = io.BytesIO()
    df.to_excel(buf, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(fname)},
    )

@router.get("/loads/profiles")
def get_load_profiles():
    """
    Return profile metadata for every load — whether a p_set time series exists.

    Checks user-uploaded _user_ts first, then falls back to n.loads_t.p_set so
    that time series loaded from a .nc file are also reported correctly.
    Each entry includes a `section` field ('electricity'|'hydrogen'|'heat'|'other')
    derived from the load's bus carrier.
    """
    n = PyPSAService.get_network()
    net_p_set = getattr(n.loads_t, "p_set", None)
    result: dict[str, dict] = {}
    # Skip transient rows (none today on Load, but registry is class-agnostic).
    keep_names = _filter_transient_names("Load", list(n.loads.index))
    for load_name in keep_names:
        section = _load_section(n, load_name)
        bus = str(n.loads.at[load_name, "bus"]) if "bus" in n.loads.columns else ""
        s = _user_ts.get(('loads', 'p_set', load_name))
        if s is None and net_p_set is not None and not net_p_set.empty and load_name in net_p_set.columns:
            s = net_p_set[load_name]
        if s is not None:
            col = s.dropna()
            # Multi-period (period, timestep) MultiIndex: use the timestep
            # level for the ISO timestamps; raw tuples don't have .isoformat.
            if len(col) and isinstance(col.index, pd.MultiIndex):
                _ts_lvl = col.index.get_level_values(-1)
                _start, _end = _ts_lvl[0], _ts_lvl[-1]
            elif len(col):
                _start, _end = col.index[0], col.index[-1]
            else:
                _start = _end = None
            result[load_name] = {
                "has_profile": True,
                "rows": int(len(col)),
                "start": (_start.isoformat() if hasattr(_start, "isoformat") else None),
                "end": (_end.isoformat() if hasattr(_end, "isoformat") else None),
                "mean": float(col.mean()) if len(col) else 0.0,
                "peak": float(col.max()) if len(col) else 0.0,
                # Σ of the profile values. For an hourly p_set profile this is
                # the delivered energy in MWh; the GUI labels it accordingly.
                "sum": float(col.sum()) if len(col) else 0.0,
                "section": section,
                "bus": bus,
            }
        else:
            result[load_name] = {"has_profile": False, "section": section, "bus": bus}
    return result

_LOAD_SHAPES = {
    "electricity": None,  # use _double_peak_profile (defined below)
    "hydrogen": _h2_load_profile,
    "heat": _heat_load_profile,
    "other": None,
}

@router.get("/loads/template")
def download_load_profile_template(
    section: str | None = None,
    load_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
):
    """
    Download a 1-week (168 h) hourly template Excel for load p_set profiles.

    Query params:
      - ``section``: ``electricity`` | ``hydrogen`` | ``heat`` | ``other``.
        Filters which loads appear in the template AND picks the daily shape
        (electricity → double peak, hydrogen → flat industrial, heat →
        morning/evening peaks). Defaults to all loads using their per-load
        section shape.
      - ``load_name``: if set, generate a single-column template for that load
        only (useful for per-load uploads).
    """
    n = PyPSAService.get_network()
    if n.loads.empty:
        raise HTTPException(400, "No loads in network")

    # Pick the load set
    all_loads = list(n.loads.index)
    if load_name is not None:
        if load_name not in n.loads.index:
            raise HTTPException(404, f"Load '{load_name}' not found")
        target_loads = [load_name]
    elif section:
        sec = section.lower().strip()
        target_loads = [name for name in all_loads if _load_section(n, name) == sec]
        if not target_loads:
            raise HTTPException(400, f"No loads belong to section '{sec}'")
    else:
        target_loads = all_loads

    snapshots, _src = _template_snapshots(n, start, end, freq, use_snapshots)

    # If section is given, every column uses that section's shape. Otherwise
    # each load uses its own carrier-derived shape — produces a mixed sheet.
    forced_shape = _shape_for_section(section) if section else None

    data: dict[str, np.ndarray] = {}
    for idx, name in enumerate(target_loads):
        p_max = float(n.loads.at[name, "p_set"]) if "p_set" in n.loads.columns else 100.0
        if not math.isfinite(p_max) or p_max <= 0:
            p_max = 100.0
        shape_fn = forced_shape or _shape_for_section(_load_section(n, name))
        data[name] = shape_fn(snapshots, p_max, noise_seed=42 + idx)

    df = pd.DataFrame(data, index=snapshots)
    df.index.name = "timestamp"

    if load_name:
        fname = f"load_{load_name.replace(' ', '_')}_template.xlsx"
    elif section:
        fname = f"load_{section}_template.xlsx"
    else:
        fname = "load_profiles_template.xlsx"
    return _xlsx_response(df, fname)

@router.get("/loads/aggregate")
def aggregate_load_profile(
    section: str | None = None,
    names: str | None = None,
):
    """
    Return the time-aligned sum of p_set across the requested loads.

    Query params:
      - ``names``: comma-separated explicit load names (takes precedence).
      - ``section``: aggregate every load in this section.
    Either may be supplied; if both, ``names`` wins. The response shape mirrors
    /timeseries: ``{index, values, total_loads, peak, mean}``.
    """
    n = PyPSAService.get_network()
    targets: list[str]
    if names:
        targets = [c.strip() for c in names.split(",") if c.strip()]
        targets = [c for c in targets if c in n.loads.index]
    elif section:
        sec = section.lower().strip()
        targets = [name for name in n.loads.index if _load_section(n, name) == sec]
    else:
        targets = list(n.loads.index)

    if not targets:
        return {"index": [], "values": [], "total_loads": 0, "peak": 0.0, "mean": 0.0}

    # Pull each series from _user_ts first, fall back to n.loads_t.p_set.
    net_p_set = getattr(n.loads_t, "p_set", None)
    series_list: list[pd.Series] = []
    for name in targets:
        s = _user_ts.get(("loads", "p_set", name))
        if s is None and net_p_set is not None and not net_p_set.empty and name in net_p_set.columns:
            s = net_p_set[name]
        if s is not None:
            series_list.append(s.rename(name))

    if not series_list:
        return {"index": [], "values": [], "total_loads": len(targets), "peak": 0.0, "mean": 0.0}

    df = pd.concat(series_list, axis=1).fillna(0.0)
    total = df.sum(axis=1)
    idx = [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in total.index]
    values = [None if isinstance(v, float) and not math.isfinite(v) else float(v) for v in total.tolist()]
    finite_vals = [v for v in values if v is not None]
    return {
        "index": idx,
        "values": values,
        "total_loads": len(targets),
        "loads_with_profile": len(series_list),
        "peak": float(max(finite_vals)) if finite_vals else 0.0,
        "mean": float(sum(finite_vals) / len(finite_vals)) if finite_vals else 0.0,
    }

def _apply_profile_upload(n, comp_attr: str, attribute: str, display_class: str, df) -> dict:
    """
    Shared body of the load/generator/link profile-upload endpoints.

    Matches uploaded `df` columns to the component's names, stores each matched
    column in `_user_ts[(comp_attr, attribute, col)]` (under `_user_ts_lock`,
    since autosave's `_serialize_user_ts` iterates concurrently), then reapplies
    to the network under the PyPSA lock — `_ensure_snapshots_cover_user_ts`
    auto-expands `n.snapshots` so a longer-than-current upload isn't truncated,
    and `_reapply_user_ts_to_network` aligns everything to the (possibly grown)
    index. Returns `{matched, unmatched, rows, snapshot_count}` where
    snapshot_count reflects the post-expansion `n.snapshots` so the frontend can
    refresh its counter.
    """
    valid = set(getattr(n, comp_attr).index)
    matched   = [c for c in df.columns if c in valid]
    unmatched = [c for c in df.columns if c not in valid]
    if not matched:
        raise HTTPException(
            400,
            f"No column names matched any {display_class.lower()}. "
            f"{display_class}s in network: {list(valid)[:10]}",
        )
    # Phase 12f: refuse before `_user_ts` is written — an entry that lands here
    # survives project reload and is re-injected on every solve, so a rejected
    # upload must leave nothing behind. Only the matched columns are checked:
    # an unmatched one is discarded anyway and its blanks are not the user's
    # problem.
    _reject_nonfinite_timeseries(df[matched], display_class, attribute)
    with _user_ts_lock:
        for col in matched:
            _user_ts[(comp_attr, attribute, col)] = df[col].astype(float)
    with PyPSAService.get_lock():
        _ensure_snapshots_cover_user_ts(n)
        _reapply_user_ts_to_network(n)
    names_preview = ", ".join(matched[:3]) + ("…" if len(matched) > 3 else "")
    cls_lower = display_class.lower()
    change_log_service.log(
        "timeseries", display_class, names_preview,
        f"Uploaded {cls_lower} {attribute} profiles: {len(matched)} {cls_lower}(s), {len(df)} rows",
    )
    return {
        "matched": matched, "unmatched": unmatched, "rows": len(df),
        "snapshot_count": len(n.snapshots),
    }

@router.post("/loads/upload_profile")
async def upload_load_profile(file: UploadFile = File(...)):
    """
    Upload an Excel or CSV file whose columns are load names and index is
    timestamps.  Matched columns are written into the user profile store and
    also into n.loads_t.p_set for simulation.
    """
    content = await read_capped(file)
    df = _parse_upload(content, file.filename or "")
    return _apply_profile_upload(PyPSAService.get_network(), "loads", "p_set", "Load", df)

@router.get("/generators/profiles")
def get_generator_profiles():
    """
    Return p_max_pu / p_min_pu / marginal_cost profile metadata for every
    generator.

    Top-level has_profile/rows/mean/peak fields describe p_max_pu (back-compat
    with the renewable/DR/links tabs). Conventional-tab sub-attributes are
    exposed under nested keys:
      • p_min_pu       — minimum dispatch floor (must-run)
      • marginal_cost  — time-varying €/MWh dispatch cost (e.g. fuel-price
        traces, market scenarios). Frontend renders the Conventional tab's
        third sub-toggle from this.
    """
    n = PyPSAService.get_network()
    net_p_max_pu = getattr(n.generators_t, "p_max_pu", None)
    net_p_min_pu = getattr(n.generators_t, "p_min_pu", None)
    net_mc       = getattr(n.generators_t, "marginal_cost", None)
    result: dict[str, dict] = {}
    # Skip transient generator rows (VOLL slacks, vintage clones) so the
    # frontend's profile-tab list mirrors what /api/network/generators
    # returns. Iterating filtered names is cheap; iterating the raw
    # index and conditional-skipping per-row would be equivalent.
    keep_names = _filter_transient_names("Generator", list(n.generators.index))
    for name in keep_names:
        carrier = str(n.generators.at[name, 'carrier']) if 'carrier' in n.generators.columns else ''
        category = _gen_category(carrier)
        max_meta = _profile_meta_for(
            name, _user_ts.get(('generators', 'p_max_pu', name)), net_p_max_pu,
        )
        min_meta = _profile_meta_for(
            name, _user_ts.get(('generators', 'p_min_pu', name)), net_p_min_pu,
        )
        # marginal_cost: user_only=True so solver-written CO2 surcharge
        # columns (from co2_price_per_period mode) don't show up as
        # "uploaded profiles" in the Time Series Manager. See solver_service
        # ~line 3736 — the per-period CO2 path writes to generators_t.marginal_cost
        # before solve; a project saved mid-solve carries those columns even
        # though the user never uploaded a marginal_cost profile.
        mc_meta = _profile_meta_for(
            name, _user_ts.get(('generators', 'marginal_cost', name)), net_mc,
            user_only=True,
        )
        result[name] = {
            **max_meta,
            'carrier': carrier,
            'category': category,
            'p_min_pu': min_meta,
            'marginal_cost': mc_meta,
        }
    return result

@router.get("/generators/template")
def download_generator_profile_template(
    category: str = "renewable",
    attribute: str = "p_max_pu",
    name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
):
    """
    Download a template Excel for generator capacity factors. Default
    horizon is the simulation snapshots; pass `use_snapshots=false` plus
    `start`/`end`/`freq` to override.

    Pass ``name`` to get a single-column file for one generator (used by the
    per-row download button on the Time Series page). When ``name`` is set,
    ``category`` is ignored — the shape is derived from the generator's own
    carrier so a "wind" pick gets the wind profile regardless of which tab
    triggered the download.
    """
    n = PyPSAService.get_network()
    if n.generators.empty:
        raise HTTPException(400, "No generators in network")

    snapshots, _src = _template_snapshots(n, start, end, freq, use_snapshots)

    # Must-run templates start from a flat baseline floor (the user can edit it
    # freely afterwards). Per-carrier defaults reflect typical minimum stable
    # loads: nuclear is the highest, lignite/coal next, others lower.
    def _must_run_floor(carrier_lower: str) -> float:
        if 'nuclear' in carrier_lower:                 return 0.5
        if 'lignite' in carrier_lower or 'coal' in carrier_lower: return 0.4
        return 0.3

    if name is not None:
        if name not in n.generators.index:
            raise HTTPException(404, f"Generator '{name}' not found")
        target_gens = [(0, name, n.generators.loc[name])]
    else:
        target_gens = [
            (idx, gname, row)
            for idx, (gname, row) in enumerate(n.generators.iterrows())
            if _gen_category(str(row.get('carrier', '') or '')) == category
        ]

    # Per-carrier baseline marginal_cost for the template (€/MWh). PyPSA-Eur
    # ballpark values — the user is expected to edit them. Falls back to the
    # generator's own scalar `marginal_cost` when no carrier-default exists,
    # then to 50 €/MWh as a generic baseline.
    def _mc_baseline(carrier_lower: str, fallback: float) -> float:
        if 'nuclear' in carrier_lower:                                 return 10.0
        if 'lignite' in carrier_lower:                                 return 35.0
        if 'coal' in carrier_lower:                                    return 45.0
        if 'ccgt' in carrier_lower:                                    return 60.0
        if 'ocgt' in carrier_lower or 'gas' in carrier_lower:          return 90.0
        if 'oil' in carrier_lower:                                     return 120.0
        if 'biomass' in carrier_lower or 'biogas' in carrier_lower:    return 70.0
        if fallback and 0 < fallback < 1000:                           return float(fallback)
        return 50.0

    data: dict[str, np.ndarray] = {}
    for idx, gname, row in target_gens:
        carrier = str(row.get('carrier', '') or '')
        c_lower = carrier.lower()
        if attribute == 'p_min_pu':
            data[gname] = np.full(len(snapshots), _must_run_floor(c_lower))
        elif attribute == 'marginal_cost':
            # Flat per-carrier baseline; user typically overlays a fuel-price
            # trace by editing the file. We keep the baseline FLAT (not noisy)
            # so it's obvious the values are seed defaults rather than a real
            # forecast — and so that an unedited upload doesn't quietly
            # perturb the LP with random per-hour cost noise.
            baseline = _mc_baseline(c_lower, float(row.get('marginal_cost', 0) or 0))
            data[gname] = np.full(len(snapshots), baseline)
        elif 'solar' in c_lower or 'pv' in c_lower:
            data[gname] = _solar_cf_profile(snapshots, noise_seed=42 + idx)
        elif 'wind' in c_lower:
            data[gname] = _wind_cf_profile(snapshots, noise_seed=42 + idx)
        else:
            data[gname] = _flat_cf_profile(snapshots, noise_seed=42 + idx)

    if not data:
        raise HTTPException(400, f"No generators in category '{category}'")

    df = pd.DataFrame(data, index=snapshots)
    df.index.name = "timestamp"

    if name:
        safe = name.replace(' ', '_')
        fname = f"generator_{safe}_{attribute}_template.xlsx"
    else:
        fname = f"generator_{attribute}_{category}_template.xlsx"
    return _xlsx_response(df, fname)

@router.post("/generators/upload_profile")
async def upload_generator_profile(
    attribute: str = "p_max_pu",
    file: UploadFile = File(...),
):
    """Upload an Excel or CSV file whose columns are generator names."""
    content = await read_capped(file)
    df = _parse_upload(content, file.filename or "")
    return _apply_profile_upload(PyPSAService.get_network(), "generators", attribute, "Generator", df)

@router.get("/links/profiles")
def get_link_profiles():
    """
    Return p_max_pu / p_min_pu / marginal_cost profile metadata for every
    link, including category.

    Top-level has_profile/rows/mean/peak describe p_max_pu (back-compat
    with the existing Links tab). p_min_pu and marginal_cost are exposed
    as nested keys so the Links tab's sub-toggle can render correct
    has-profile indicators per attribute.
    """
    n = PyPSAService.get_network()
    net_p_max_pu = getattr(n.links_t, "p_max_pu", None)
    net_p_min_pu = getattr(n.links_t, "p_min_pu", None)
    net_mc       = getattr(n.links_t, "marginal_cost", None)
    result: dict[str, dict] = {}
    # Skip transient link rows (vintage clones `parent@<year>`).
    keep_names = _filter_transient_names("Link", list(n.links.index))
    for name in keep_names:
        category = _link_category(n, name)
        max_meta = _profile_meta_for(
            name, _user_ts.get(('links', 'p_max_pu', name)), net_p_max_pu,
        )
        min_meta = _profile_meta_for(
            name, _user_ts.get(('links', 'p_min_pu', name)), net_p_min_pu,
        )
        # Same user_only treatment as generators — see get_generator_profiles.
        # Links don't currently get solver-written marginal_cost (only
        # generators do, via co2_price_per_period), but applying the flag
        # symmetrically keeps the policy consistent if a future solver
        # transform writes to links_t.marginal_cost.
        mc_meta = _profile_meta_for(
            name, _user_ts.get(('links', 'marginal_cost', name)), net_mc,
            user_only=True,
        )
        result[name] = {
            **max_meta,
            'category': category,
            'p_min_pu': min_meta,
            'marginal_cost': mc_meta,
        }
    return result

@router.get("/links/template")
def download_link_profile_template(
    attribute: str = "p_max_pu",
    name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
):
    """
    Download a flat-1.0 template for link availability profiles. Default
    horizon is the simulation snapshots; pass `use_snapshots=false` plus
    `start`/`end`/`freq` to override. Pass ``name`` for a single-column
    template (per-row download button on the Time Series page).
    """
    n = PyPSAService.get_network()
    if n.links.empty:
        raise HTTPException(400, "No links in network")

    snapshots, _src = _template_snapshots(n, start, end, freq, use_snapshots)

    if name is not None:
        if name not in n.links.index:
            raise HTTPException(404, f"Link '{name}' not found")
        target_links = [name]
    else:
        target_links = list(n.links.index)

    data = {lname: np.ones(len(snapshots)) for lname in target_links}
    df = pd.DataFrame(data, index=snapshots)

    if name:
        safe = name.replace(' ', '_')
        fname = f"link_{safe}_{attribute}_template.xlsx"
    else:
        fname = f"links_{attribute}_template.xlsx"
    return _xlsx_response(df, fname)

@router.post("/links/upload_profile")
async def upload_link_profile(
    attribute: str = "p_max_pu",
    file: UploadFile = File(...),
):
    """Upload an Excel or CSV file whose columns are link names."""
    content = await read_capped(file)
    df = _parse_upload(content, file.filename or "")
    return _apply_profile_upload(PyPSAService.get_network(), "links", attribute, "Link", df)
