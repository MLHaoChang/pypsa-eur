"""
Two-area sequential MC for the Energy Hub certification: the hub, and the
grid behind its import Link(s).

Plan docs/superpowers/plans/2026-09-27-eh-zonal-mc-import-outages.md, WP2.
The single-area engine (``mc.py``) certifies the hub side with the import
Link as one more sampled unit (v1). That still assumes the grid always has
the power to send. Here the grid side is a second area with its own sampled
fleet and its own demand, and each hour the hub receives

    import_h = min(Σ link_up × cap_h  (+ firm-block Links),
                   max(grid_available_h − grid_residual_h, 0) × delivery_h)

— the grid serves its own load first and only its surplus crosses the Link.

WHY A SEPARATE MODULE. ``_simulate_blocks`` is the load-bearing single-area
kernel behind the MC endpoint, ELCC and both coupling loops; nothing here
edits it. ``mc.mc_adequacy`` gained one keyword (``blocks_fn``) so the
batching, convergence and aggregation stay in one place, and this module
supplies the two-area block function.

THE CRN CONTRACT, KEPT. The hub half is the v1 ``MCInputs`` with the import
Link unit(s) at their v1 positions, sampled with the SAME seed through
``sample_capacity``: generators come from one call (Links excluded, their
paths generated and discarded as the contract requires), the Links from a
second call in which every other unit is a zero-rate placeholder (which
consumes no stream), so each Link draws from its own positional substream.
When the grid never binds, the hub sees exactly the v1 capacity — the
float32 sum is formed in the same order — and the per-draw LOLE / EUE are
bit-identical to v1 (pinned by a test). The grid fleet samples from its own
tagged substream, so adding the grid area moves no hub draw.

WHAT IT DOES NOT MODEL (disclosed on the payload): grid-side storage is not
dispatched (conservative for the hub), the grid is one area (a grid side
that is several disconnected components is refused upstream), and grid
outages are independent of hub outages like every other unit (MC_WARNING_V1).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from services.adequacy.mc import (
    SHORTFALL_TOL,
    MCInputs,
    _active_storage,
    _dispatch,
    block_store_arrays,
    mc_adequacy,
    sample_capacity,
)

#: The grid area's substream key, appended to the batch seed's spawn key.
#: Unit substreams are ``spawn_key + (i,)`` for positions ``i < len(units)``,
#: so a key this large cannot collide with any fleet position.
GRID_STREAM_KEY = 2**32 - 1


@dataclass(frozen=True)
class GridArea:
    """
    One grid-side area behind one or more import Links (plan 2026-09-28).

    ``grid`` is the area's own snapshot — units, residual (its demand net of
    its must-take) and storage. ``None`` means the area has no sampled unit:
    its Links then see an unbounded surplus (v1 for those Links), which is
    how an unsampled grid coexists with a sampled one (WP2) and how a
    common-mode event reaches a v1 hub (WP4).
    """

    grid: MCInputs | None
    import_idx: tuple             # positions of this area's Link units in hub.units
    firm_import_mw: np.ndarray    # (H,) this area's firm-block Links, already
    #                               netted out of hub.residual in the v1 snapshot
    delivery_ratio: np.ndarray    # (H,) MW delivered at the hub per MW
    #                               withdrawn from the grid (Link efficiency)
    stream: int = 0               # the area's index k → substream GRID_STREAM_KEY − k


@dataclass(frozen=True)
class ZonalInputs:
    """The hub and its grid area(s), frozen as plain arrays (the ``MCInputs`` rule)."""

    hub: MCInputs                 # v1 inputs: hub units + import Link unit(s)
    areas: tuple                  # (GridArea, ...)


def single_area(z: ZonalInputs) -> GridArea:
    """The one area of a single-grid hub (tests and the scope builder)."""
    if len(z.areas) != 1:
        raise ValueError(f"expected one grid area, got {len(z.areas)}")
    return z.areas[0]


def _seed_sequence(seed) -> np.random.SeedSequence:
    if isinstance(seed, np.random.SeedSequence):
        return seed
    return np.random.SeedSequence(seed)


def _fresh(ss: np.random.SeedSequence, extra_key: tuple = ()) -> np.random.SeedSequence:
    """
    A NEW SeedSequence equal to ``ss`` (plus an optional key). Needed
    because ``sample_capacity`` spawns from the sequence it is given, and
    spawning advances the sequence's child counter — reusing one object for
    two calls would hand the second call different children.
    """
    return np.random.SeedSequence(ss.entropy,
                                  spawn_key=(*ss.spawn_key, *extra_key),
                                  pool_size=ss.pool_size)


def _discharge_only(need, soc, p_rem, e_nom, eff_s, eff_d):
    """
    Discharge ``need`` (≥ 0, grid-side MW) from the grid stores this hour.

    ``mc._dispatch``'s discharge pass, with ONE difference: the power bound
    is ``p_rem`` (S, draws), the power each store still has this hour —
    grid stores may discharge twice in an hour (their own deficit, then
    remote support) and must not exceed their rating across the two. Same
    pinned order: descending remaining energy. Returns ``(unmet, given)``;
    ``soc`` and ``p_rem`` are updated in place.
    """
    n_store, n_draw = soc.shape
    cols = np.arange(n_draw)
    order = np.argsort(soc, axis=0, kind="stable")
    left = need.copy()
    for r in range(n_store - 1, -1, -1):
        si = order[r]
        cur = soc[si, cols]
        ed = eff_d[si]
        give = np.minimum(np.minimum(p_rem[si, cols], cur * ed), left)
        soc[si, cols] = cur - give / ed
        p_rem[si, cols] -= give
        left -= give
    return left, need - left


def _charge_only(surplus, soc, p_rem, e_nom, eff_s, eff_d):
    """
    Charge from ``surplus`` (≥ 0, grid-side MW), ascending remaining
    energy — ``mc._dispatch``'s charge pass with the ``p_rem`` bound.
    """
    n_store, n_draw = soc.shape
    cols = np.arange(n_draw)
    order = np.argsort(soc, axis=0, kind="stable")
    left = surplus.copy()
    for r in range(n_store):
        si = order[r]
        cur = soc[si, cols]
        es = eff_s[si]
        headroom = np.maximum(e_nom[si] - cur, 0.0) / es
        take = np.minimum(np.minimum(p_rem[si, cols], headroom), left)
        soc[si, cols] = cur + take * es
        p_rem[si, cols] -= take
        left -= take


class _AreaState:
    """Per-area arrays for one simulation call."""

    def __init__(self, area: GridArea, hub, units, ss, H, draws,
                 grid_storage_enabled, initial_soc_frac):
        self.area = area
        imp = frozenset(int(i) for i in area.import_idx)
        link_only = tuple(u if i in imp else dataclasses.replace(u, q=0.0)
                          for i, u in enumerate(units))
        self.link_t = np.ascontiguousarray(sample_capacity(
            link_only, H, draws, _fresh(ss),
            exclude=frozenset(range(len(units))) - imp,
            periods=hub.periods).T)
        self.firm = np.asarray(area.firm_import_mw, dtype=np.float64)
        self.ratio = np.asarray(area.delivery_ratio, dtype=np.float64)
        g = area.grid
        self.own_mwh = self.support_mwh = self.charge_mwh = 0.0
        if g is None:
            self.grid_t = None
            self.stores = ()
        else:
            if g.residual.size != H or tuple(g.periods) != tuple(hub.periods):
                raise ValueError(
                    "grid area and hub disagree on the horizon (residual "
                    f"{g.residual.size} vs {H} hours, periods "
                    f"{len(g.periods)} vs {len(hub.periods)}) — both must come "
                    "from the same network snapshot")
            key = GRID_STREAM_KEY - int(area.stream)
            self.grid_t = np.ascontiguousarray(sample_capacity(
                g.units, H, draws, _fresh(ss, (key,)), periods=g.periods).T)
            self.stores = (_active_storage(g, True, ())
                           if grid_storage_enabled else ())
        self.any_series = any(getattr(s, "capacity_series", None) is not None
                              for s in self.stores)
        self.soc_frac = float(initial_soc_frac)
        self.draws = draws

    def start_block(self, start, end):
        (self.n_s, self.p_nom, self.e_nom, self.eff_s,
         self.eff_d) = block_store_arrays(self.stores, start, end,
                                          any_series=self.any_series)
        self.soc = (np.repeat((self.e_nom * self.soc_frac)[:, None],
                              self.draws, axis=1)
                    if self.n_s else np.zeros((0, self.draws)))

    def offer(self, h):
        """
        Grid-first → ``(offered, imp)`` in hub-side MW for hour ``h``;
        also leaves the hour's grid state for ``support`` / ``charge``.
        """
        offered = self.link_t[h].astype(np.float64) + self.firm[h]
        if self.grid_t is None:
            self.p_rem = None
            self.surplus_g = None
            return offered, offered
        g_def = self.grid_t[h].astype(np.float64)
        g_def = self.area.grid.residual[h] - g_def
        if self.n_s:
            self.p_rem = np.repeat(self.p_nom[:, None], self.draws, axis=1)
            short = np.maximum(g_def, 0.0)
            if short.any():
                unmet, given = _discharge_only(short, self.soc, self.p_rem,
                                               self.e_nom, self.eff_s, self.eff_d)
                self.own_mwh += float(given.sum())
                g_def = np.where(g_def > 0.0, unmet, g_def)
        self.surplus_g = np.maximum(-g_def, 0.0)
        return offered, np.minimum(offered, self.surplus_g * self.ratio[h])

    def support(self, h, deficit, offered, imp):
        """
        Remote support: grid storage covers what the hub still lacks,
        bounded by the Link headroom ``offered − imp``.
        """
        if not self.n_s or self.grid_t is None:
            return deficit
        r = self.ratio[h]
        if r <= 0.0:
            return deficit
        need_h = np.minimum(np.maximum(deficit, 0.0),
                            np.maximum(offered - imp, 0.0))
        if not need_h.any():
            return deficit
        _, given = _discharge_only(need_h / r, self.soc, self.p_rem,
                                   self.e_nom, self.eff_s, self.eff_d)
        self.support_mwh += float(given.sum())
        return deficit - given * r

    def charge(self, h, imp):
        """Charge only from the surplus NOT offered to the hub."""
        if not self.n_s or self.grid_t is None:
            return
        r = self.ratio[h]
        reserved = imp / r if r > 0.0 else 0.0
        left = np.maximum(self.surplus_g - reserved, 0.0)
        if left.any():
            before = self.soc.sum()
            _charge_only(left, self.soc, self.p_rem, self.e_nom, self.eff_s,
                         self.eff_d)
            self.charge_mwh += float(self.soc.sum() - before)


def simulate_zonal_blocks(z: ZonalInputs, *, draws: int, seed,
                          storage_enabled: bool = True,
                          grid_storage_enabled: bool = True,
                          initial_soc_frac: float = 1.0,
                          trace: dict | None = None) -> dict:
    """
    Per-period per-draw ``(lole_h, eue_mwh)`` — ``_simulate_blocks``'s shape,
    for the hub, with each area's import bounded by that area's surplus.

    Grid storage follows the pinned non-anticipative policy (plan
    2026-09-28, WP1), per hour and per draw: (1) grid stores discharge
    against the grid's OWN deficit first; (2) the remaining grid surplus is
    offered through the Link, ``min(link_avail, surplus × delivery)``; (3)
    the hub's own stores dispatch against what is left; (4) grid stores
    then discharge to the hub, bounded by the Link headroom
    ``link_avail − offered``; (5) grid stores charge only from surplus that
    was NOT offered to the hub. With no grid storage (or
    ``grid_storage_enabled=False``) steps 1, 4 and 5 are no-ops and the
    arithmetic is exactly the 2026-09-27 kernel's (pinned by
    ``tests/zonal_oracle.py``).

    ``trace`` (tests / diagnostics): when a dict is passed, it receives one
    entry per area, ``{"own_mwh", "support_mwh", "charge_mwh"}`` — grid-side
    MWh summed over draws and MODELLED hours (unweighted) for step 1, step 4
    and step 5 (charge = SoC gained). It never changes the result.
    """
    hub = z.hub
    residual = hub.residual
    weights = hub.weights
    H = residual.size
    draws = int(draws)
    ss = _seed_sequence(seed)
    units = tuple(hub.units)
    all_imp = frozenset(int(i) for a in z.areas for i in a.import_idx)

    hub_t = np.ascontiguousarray(sample_capacity(
        units, H, draws, _fresh(ss), exclude=all_imp, periods=hub.periods).T)
    areas = [_AreaState(a, hub, units, ss, H, draws, grid_storage_enabled,
                        initial_soc_frac) for a in z.areas]

    # The v1 residual has the firm-block Links netted out; here they are
    # import capacity bounded by the grid like any other, so add them back.
    firm_total = np.zeros(H, dtype=np.float64)
    for a in areas:
        firm_total = firm_total + a.firm
    residual_raw = residual + firm_total

    all_stores = _active_storage(hub, storage_enabled, ())
    any_series = any(getattr(s, "capacity_series", None) is not None
                     for s in all_stores)

    out: dict = {}
    for label, start, end in hub.periods:
        n_store, p_nom, e_nom, eff_s, eff_d = block_store_arrays(
            all_stores, start, end, any_series=any_series)
        single_order = np.zeros((1, draws), dtype=np.intp) if n_store == 1 else None
        lole = np.zeros(draws, dtype=np.float64)
        eue = np.zeros(draws, dtype=np.float64)
        soc = np.repeat((e_nom * float(initial_soc_frac))[:, None], draws,
                        axis=1) if n_store else np.zeros((0, draws))
        for a in areas:
            a.start_block(start, end)
        for h in range(start, end):
            offers = [a.offer(h) for a in areas]
            imp_total = offers[0][1]
            for _o, i in offers[1:]:
                imp_total = imp_total + i
            imp_h = imp_total.astype(np.float32)
            # Same float32 accumulation order as sample_capacity's: the hub's
            # units, then the Link(s) — so an unbound grid replays v1 exactly.
            cap = (hub_t[h] + imp_h).astype(np.float64)
            deficit = residual_raw[h] - cap
            if n_store:
                order = (single_order if n_store == 1
                         else np.argsort(soc, axis=0, kind="stable"))
                deficit = _dispatch(deficit, soc, p_nom, e_nom, eff_s, eff_d,
                                    order)
            for a, (o, i) in zip(areas, offers):
                deficit = a.support(h, deficit, o, i)
            for a, (_o, i) in zip(areas, offers):
                a.charge(h, i)
            w_h = weights[h]
            lole += w_h * (deficit > SHORTFALL_TOL)
            eue += w_h * np.maximum(deficit, 0.0)
        out[label] = (lole, eue)
    if trace is not None:
        trace["areas"] = [{"own_mwh": a.own_mwh, "support_mwh": a.support_mwh,
                           "charge_mwh": a.charge_mwh} for a in areas]
    return out


def zonal_mc_adequacy(z: ZonalInputs, *, draws: int = 500, seed=0,
                      cov_target: float = 0.05, max_draws: int | None = None,
                      batch: int = 250, stop_event=None, **sim_kwargs) -> dict:
    """
    ``mc.mc_adequacy`` on the two-area block function — the same
    batching, convergence rule, CI and payload keys as the single-area MC.
    """
    kw = {} if max_draws is None else {"max_draws": max_draws}

    def _blocks(_inputs, *, draws, seed, **kwargs):
        return simulate_zonal_blocks(z, draws=draws, seed=seed, **kwargs)

    return mc_adequacy(z.hub, draws=draws, seed=seed, cov_target=cov_target,
                       batch=batch, stop_event=stop_event, blocks_fn=_blocks,
                       **kw, **sim_kwargs)


# ── WP3: the grid surplus as the COPT screening sees it ──────────────────

def _grid_expected_shortfall(units, x, periods) -> np.ndarray:
    """
    ``ES_h(x_h) = E[max(x_h − C_h, 0)]`` for the grid fleet ``units``,
    per hour — the COPT surface's own recipe (``copt._screen_block``):
    split the fleet, net the units beyond ``K_EXACT`` at expectation and
    the rate-zero profiled ones at full availability, convolve the two-state
    units, mix the profiled ones exactly. Units with a per-period capacity
    series are evaluated block by block at their constant block capacity,
    as ``screening_analysis`` does.
    """
    from services.adequacy.activity import block_capacity
    from services.adequacy.copt import (
        build_copt,
        deterministic_output,
        mixture_hourly,
        netted_expectation,
        split_fleet,
    )

    x = np.asarray(x, dtype=np.float64)
    H = x.shape[0]
    has_series = any(getattr(u, "capacity_series", None) is not None
                     for u in units)
    blocks = tuple(periods) if has_series else (("ALL", 0, H),)
    out = np.zeros(H, dtype=np.float64)
    for _label, start, end in blocks:
        block_units = []
        for u in units:
            cs = getattr(u, "capacity_series", None)
            cap = block_capacity(u.capacity_mw, cs, start, end)
            if cap <= 0.0:
                continue
            prof = (None if u.profile is None
                    else np.asarray(u.profile, dtype=np.float64)[start:end])
            block_units.append(dataclasses.replace(
                u, capacity_mw=cap, capacity_series=None, profile=prof))
        xb = x[start:end]
        hb = end - start
        split = split_fleet(block_units)
        xb = (xb - netted_expectation(split.netted, hb)
              - deterministic_output(split.deterministic, hb))
        dist = build_copt(list(split.table))
        _lolp, es = mixture_hourly(dist, xb, split.mixed)
        out[start:end] = es
    return out


def expected_surplus_fraction(grid_units, grid_residual, *, cap, ratio,
                              periods) -> np.ndarray:
    """
    ``f_h = E[min(cap_h, S_h)] / cap_h`` with ``S_h = max(C_h − r_h, 0) ×
    ratio_h`` — the share of a Link's hub-side cap the grid area can back,
    in expectation over the area's own outages (plan 2026-09-28, WP3).

    With ``c' = cap / ratio`` (the cap in grid-side MW) and ``ES`` the grid
    fleet's expected shortfall, ``E[min(c', (C − r)⁺)] = c' + ES(r) −
    ES(r + c')`` — exact on the COPT grid — so ``f = ratio × (c' + ES(r) −
    ES(r + c')) / cap``, clipped to ``[0, 1]``. Hours with no cap return 1
    (nothing to derate). Storage is not in the COPT (it never is).
    """
    cap = np.asarray(cap, dtype=np.float64)
    ratio = np.asarray(ratio, dtype=np.float64)
    r = np.asarray(grid_residual, dtype=np.float64)
    live = (cap > 0.0) & (ratio > 0.0)
    c_grid = np.where(live, cap / np.where(ratio > 0.0, ratio, 1.0), 0.0)
    es_r = _grid_expected_shortfall(grid_units, r, periods)
    es_rc = _grid_expected_shortfall(grid_units, r + c_grid, periods)
    with np.errstate(divide="ignore", invalid="ignore"):
        f = np.where(live, ratio * (c_grid + es_r - es_rc) / np.where(
            live, cap, 1.0), 1.0)
    f = np.where(cap > 0.0, np.where(ratio > 0.0, f, 0.0), 1.0)
    return np.clip(f, 0.0, 1.0)
