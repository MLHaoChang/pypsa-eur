"""
Spatial clustering endpoint.

POST /api/network/cluster — reduce the in-memory network to a coarser bus set
using one of four modes (nodal / zone / region / custom). The "custom" mode
exposes four PyPSA algorithms (kmeans / hac / greedy_modularity / stubs) with
their full parameter surfaces.

The endpoint is intentionally a single mutation: it builds a busmap, runs
PyPSA's `get_clustering_from_busmap` (which handles every component class via
the default aggregation strategies — lines combine impedances correctly,
one-port components sum capacities and weight-average costs), and swaps the
result into the singleton via `PyPSAService.set_network`. The change_log gets
a single audit entry summarising the bus-count delta.
"""
from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services import change_log_service
from services.pypsa_service import PyPSAService

router = APIRouter()


# ── Request schema ─────────────────────────────────────────────────────────

class KmeansOpts(BaseModel):
    n_init: int = 10
    max_iter: int = 300
    tol: float = 1e-4
    random_state: int = 0


class HacOpts(BaseModel):
    affinity: Literal["euclidean", "l1", "l2", "manhattan", "cosine"] = "euclidean"
    linkage: Literal["ward", "complete", "average", "single"] = "ward"
    # 'none' → uniform feature (all zeros, every bus equally similar → HAC
    # falls back to whatever the connectivity adjacency dictates).
    # 'renewable_cf' → concatenate p_max_pu time-series of all extendable
    # wind/solar generators on each bus into a feature row (the PyPSA-Eur
    # canonical choice — clusters with similar weather/profile group together).
    feature_source: Literal["none", "renewable_cf"] = "none"


class StubsOpts(BaseModel):
    # Bus attributes that a stub must agree with its neighbour on before
    # being folded. Common picks: v_nom (don't merge across voltage levels),
    # carrier (don't merge AC with H2/heat), country, sub_network.
    matching_attrs: list[str] = Field(default_factory=list)


class ClusterRequest(BaseModel):
    mode: Literal["nodal", "zone", "region", "custom"]
    algorithm: Literal["kmeans", "hac", "greedy_modularity", "stubs"] | None = None
    n_clusters: int | None = None
    # 'load' weights each bus by its summed peak p_set (only consumed by the
    # k-means algorithm — HAC / modularity ignore the weighting).
    weighting: Literal["uniform", "load"] = "load"
    kmeans: KmeansOpts | None = None
    hac: HacOpts | None = None
    stubs: StubsOpts | None = None


# ── Service helpers ────────────────────────────────────────────────────────

def _load_bus_weightings(n: Any, source: Literal["uniform", "load"]) -> pd.Series:
    """
    Build the per-bus integer weighting used by k-means. Empty weightings
    are forbidden by sklearn (each weight must be a positive integer count of
    "duplicate points"), so floors clip to 1 to keep every bus represented
    at least once.
    """
    if source == "uniform":
        return pd.Series(1, index=n.buses.index, dtype=int)
    # 'load' — sum p_set across loads on each bus, normalise to [1, 100].
    if n.loads.empty:
        return pd.Series(1, index=n.buses.index, dtype=int)
    bus_load = n.loads.groupby("bus")["p_set"].sum().reindex(n.buses.index, fill_value=0.0)
    if bus_load.sum() <= 0:
        return pd.Series(1, index=n.buses.index, dtype=int)
    # Normalise to integers in [1, 100] — the bus_weightings argument to
    # sklearn.KMeans is multiplied as a duplication count, so larger ranges
    # explode the point count. PyPSA-Eur uses the same normalisation.
    w = (bus_load / bus_load.sum() * 100.0).clip(lower=1).round().astype(int)
    return w


def _build_renewable_feature(n: Any) -> pd.DataFrame:
    """
    Per-bus renewable capacity-factor feature row for HAC.

    For each renewable generator on a bus, take the p_max_pu time-series (or
    fall back to the static p_max_pu if no per-snapshot data exists) and
    concatenate horizontally. Buses with no renewable generators get a row of
    zeros. The result has shape (n_buses, n_snapshots × n_renewable_carriers).
    """
    renew = {"wind", "onwind", "offwind", "offwind-ac", "offwind-dc",
             "solar", "pv", "solar rooftop", "rooftop solar",
             "ror", "hydro", "biomass", "geothermal"}
    gens = n.generators
    is_renew = gens["carrier"].astype(str).str.lower().isin(renew)
    if not is_renew.any():
        return pd.DataFrame(0.0, index=n.buses.index, columns=["uniform"])
    renew_gens = gens[is_renew]
    rows: dict[str, list[float]] = {b: [] for b in n.buses.index}
    for _, g in renew_gens.iterrows():
        bus = g["bus"]
        if bus not in rows:
            continue
        gname = g.name
        ts = n.generators_t.p_max_pu.get(gname)
        if ts is not None and not ts.empty:
            vals = ts.values.tolist()
        else:
            vals = [float(g.get("p_max_pu", 1.0))]
        rows[bus].extend(vals)
    # Pad shorter rows with zeros so the matrix is rectangular.
    max_len = max((len(v) for v in rows.values()), default=1)
    for b, v in rows.items():
        if len(v) < max_len:
            v.extend([0.0] * (max_len - len(v)))
    return pd.DataFrame.from_dict(rows, orient="index").fillna(0.0)


def _build_busmap(n: Any, req: ClusterRequest) -> pd.Series | None:
    """
    Return a busmap (Series: original_bus -> new_cluster_id) for the
    chosen mode/algorithm, or None for the 'nodal' no-op.
    """
    if req.mode == "nodal":
        return None

    if req.mode == "zone":
        if "country" not in n.buses.columns or n.buses["country"].isna().all():
            raise HTTPException(
                400,
                "Zone mode requires buses to have a 'country' attribute set. "
                "Add country labels on each bus before clustering by zone.",
            )
        # NaN buses get a unique stand-in so they aren't collapsed into a
        # single phantom "nan" cluster — usually indicates a missing label
        # the user should fix before relying on the result.
        return n.buses["country"].fillna(n.buses.index.to_series()).astype(str)

    if req.mode == "region":
        # User-set sub_network labels win; gaps fall through to PyPSA's
        # connected-components determination.
        #
        # Rationale: the Bus editor in the right-panel lets users assign an
        # arbitrary `sub_network` string (e.g. "North", "DE-onshore"). For
        # "region" clustering we treat that label as the cluster key — every
        # bus sharing the label collapses into one node. Buses left blank fall
        # through to `determine_network_topology()` so a partially-labelled
        # network still produces a sensible busmap.
        raw_labels = (
            n.buses["sub_network"].astype(str).fillna("").str.strip()
            if "sub_network" in n.buses.columns
            else pd.Series("", index=n.buses.index, dtype=str)
        )
        # Distinguish typed labels from auto-detected remnants. PyPSA persists
        # integer SubNetwork ids on netcdf round-trip whenever a previous
        # solve or `n.determine_network_topology()` populated the column —
        # so a freshly-loaded project usually has values like '0', '1', '2'
        # in `sub_network` that the user never typed. If we treated those as
        # "user-set", region clustering would collapse along the stale
        # topology partition silently. Heuristic: a value that's only digits
        # (optionally signed) is auto-detected unless at least one bus has a
        # non-integer label — in which case the user is clearly using the
        # feature and we honor every value including their integers.
        looks_integer = raw_labels.str.fullmatch(r"-?\d+", na=False)
        has_typed = ((raw_labels != "") & ~looks_integer).any()
        user_labels = raw_labels if has_typed else raw_labels.where(~looks_integer, "")
        needs_auto = (user_labels == "")
        n_user, n_auto = int((~needs_auto).sum()), int(needs_auto.sum())

        if needs_auto.any():
            # `determine_network_topology()` mutates more than `n.buses`:
            # it ALSO rebuilds `n.sub_networks.static` and overwrites
            # `n.lines["sub_network"]` / `n.transformers["sub_network"]` with
            # auto-detected ids. Without restoring the branch columns,
            # `aggregatelines` consense (called inside
            # `get_clustering_from_busmap`) raises ValueError on a user-defined
            # cluster that spans two auto-detected components — the lines
            # between them carry disagreeing `sub_network` values and PyPSA's
            # consense strategy rejects the disagreement.
            saved_buses = n.buses["sub_network"].copy() if "sub_network" in n.buses.columns else None
            saved_lines = n.lines["sub_network"].copy() if "sub_network" in n.lines.columns else None
            saved_trafos = (
                n.transformers["sub_network"].copy()
                if "sub_network" in n.transformers.columns
                else None
            )
            n.determine_network_topology()
            if "sub_network" not in n.buses.columns:
                raise HTTPException(500, "Failed to determine sub-network topology.")
            # Auto labels prefixed with `auto:` so they can't collide with a
            # user-set bare integer (when has_typed is True we keep integers,
            # and a user typing "0" intentionally must not be merged with the
            # auto-detected SubNetwork id 0).
            auto_labels = "auto:" + n.buses["sub_network"].astype(str).fillna("")
            if saved_buses is not None:
                n.buses["sub_network"] = saved_buses
            if saved_lines is not None:
                n.lines["sub_network"] = saved_lines
            if saved_trafos is not None:
                n.transformers["sub_network"] = saved_trafos
        else:
            auto_labels = pd.Series("", index=n.buses.index, dtype=str)

        busmap = user_labels.where(user_labels != "", auto_labels).astype(str)
        # Stash the split for the audit log at the bottom of apply_clustering.
        # `_build_busmap` has no other channel to return metadata, so attach
        # to the Series as private attrs — read-only consumption only.
        busmap.attrs["region_n_user"] = n_user
        busmap.attrs["region_n_auto"] = n_auto
        return busmap

    # mode == 'custom'
    algo = req.algorithm
    if algo is None:
        raise HTTPException(400, "Custom mode requires an algorithm.")

    if algo == "stubs":
        from pypsa.clustering.spatial import busmap_by_stubs
        opts = req.stubs or StubsOpts()
        return busmap_by_stubs(n, matching_attrs=opts.matching_attrs or None)

    # k-means / HAC / modularity all take an n_clusters target.
    n_clusters = req.n_clusters
    if n_clusters is None or n_clusters < 1:
        raise HTTPException(400, f"{algo} requires n_clusters ≥ 1.")
    n_buses = len(n.buses)
    if n_clusters > n_buses:
        raise HTTPException(400, f"n_clusters ({n_clusters}) cannot exceed bus count ({n_buses}).")

    if algo == "kmeans":
        from pypsa.clustering.spatial import busmap_by_kmeans
        weights = _load_bus_weightings(n, req.weighting)
        opts = req.kmeans or KmeansOpts()
        return busmap_by_kmeans(
            n, weights, n_clusters,
            n_init=opts.n_init,
            max_iter=opts.max_iter,
            tol=opts.tol,
            random_state=opts.random_state,
        )

    if algo == "hac":
        from pypsa.clustering.spatial import busmap_by_hac
        opts = req.hac or HacOpts()
        # ward only supports euclidean — guard up front with a friendlier
        # error than sklearn's "linkage='ward' requires euclidean".
        if opts.linkage == "ward" and opts.affinity != "euclidean":
            raise HTTPException(
                400,
                "HAC ward linkage requires euclidean affinity. Pick a different linkage or switch affinity to euclidean.",
            )
        feature = _build_renewable_feature(n) if opts.feature_source == "renewable_cf" else None
        return busmap_by_hac(
            n, n_clusters,
            feature=feature,
            affinity=opts.affinity,
            linkage=opts.linkage,
        )

    if algo == "greedy_modularity":
        from pypsa.clustering.spatial import busmap_by_greedy_modularity
        return busmap_by_greedy_modularity(n, n_clusters)

    raise HTTPException(400, f"Unknown algorithm: {algo}")


# ── Endpoint ───────────────────────────────────────────────────────────────

@router.post("/cluster")
def apply_clustering(req: ClusterRequest) -> dict:
    n = PyPSAService.get_network()
    before_buses = len(n.buses)
    before_lines = len(n.lines)

    with PyPSAService.get_lock():
        try:
            busmap = _build_busmap(n, req)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface PyPSA errors
            raise HTTPException(500, f"Busmap construction failed: {exc}")

        if busmap is None:
            # nodal — no-op
            return {
                "bus_count": before_buses,
                "line_count": before_lines,
                "message": f"Nodal mode — no clustering applied ({before_buses} buses).",
            }

        from pypsa.clustering.spatial import get_clustering_from_busmap
        try:
            clustering = get_clustering_from_busmap(n, busmap)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"PyPSA clustering failed: {exc}")

        new_n = clustering.n
        # Preserve coords, x/y aren't lost — aggregatebuses averages them.
        PyPSAService.set_network(new_n)

    # Audit log entry summarising the reduction. For region mode, also report
    # the user-set vs auto-determined split so the user can confirm partial
    # labelling worked as expected.
    label = req.mode if req.mode != "custom" else f"custom/{req.algorithm}"
    region_split = ""
    if req.mode == "region" and busmap is not None:
        n_user = busmap.attrs.get("region_n_user", 0)
        n_auto = busmap.attrs.get("region_n_auto", 0)
        region_split = f" — {n_user} manually labelled, {n_auto} auto-determined"
    change_log_service.log(
        "cluster",
        "Network",
        label,
        f"Clustered ({label}): {before_buses} → {len(new_n.buses)} buses, "
        f"{before_lines} → {len(new_n.lines)} lines{region_split}",
    )

    return {
        "bus_count": int(len(new_n.buses)),
        "line_count": int(len(new_n.lines)),
        "message": (
            f"Clustered: {before_buses} → {len(new_n.buses)} buses · "
            f"{before_lines} → {len(new_n.lines)} lines{region_split}"
        ),
    }
