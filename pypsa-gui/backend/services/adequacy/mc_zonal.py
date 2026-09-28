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
When no grid binds, the hub sees the v1 capacity: each area's import is
added to the hub's float32 sum in Link-position order, so with at most one
sampled Link per area (and no firm-block Link) the per-draw LOLE / EUE are
bit-identical to v1 (pinned by tests, one area and two). Several sampled
Links into ONE area are summed within the area first, which can differ
from v1's unit-by-unit float32 order by rounding only. Each grid area
samples from its own tagged substream (``GRID_STREAM_KEY − k``), so adding
an area moves no hub draw and no other area's draw.

Grid storage (plan 2026-09-28, WP1) follows a pinned non-anticipative
policy (see ``simulate_zonal_blocks``); several disconnected grid
components are several areas (WP2); an area with no sampled unit leaves
its Links on v1. WHAT IT DOES NOT MODEL (disclosed on the payload): grid
outages are independent of hub outages like every other unit
(MC_WARNING_V1), and offered-but-unused power is not stored in grid stores.
"""
from __future__ import annotations

import dataclasses
import itertools
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
#: Common-mode chains (WP4): ``CM_STREAM_KEY − j``. 2²⁰ below the grid keys,
#: so neither range can reach the other for any realistic area / event count.
CM_STREAM_KEY = 2**32 - 2**20


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
    # WP4: common-mode events that take this area's Links AND its grid down
    # together — (CommonMode, ...), one two-state chain each.
    common_mode: tuple = ()


@dataclass(frozen=True)
class CommonMode:
    """One common-mode event from an import Link's opt-in data (WP4)."""

    link: str
    q: float
    mttr_hours: float
    stream: int                   # global index j → substream CM_STREAM_KEY − j


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


def _placeholder(u):
    """
    A slot-keeping stand-in for a unit another ``sample_capacity`` call
    owns: ``q = 0`` consumes no stream, and without a profile / series it
    skips the per-unit array work — so a unit's path is generated once per
    batch, by the one call that keeps it.
    """
    return dataclasses.replace(u, q=0.0, profile=None, capacity_series=None)


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
                 grid_storage_enabled, initial_soc_frac, track=False):
        self.area = area
        # The trace sums cost a pass over (S, draws) per hour — only when
        # a caller asked for them.
        self.track = bool(track)
        imp = frozenset(int(i) for i in area.import_idx)
        link_only = tuple(u if i in imp else _placeholder(u)
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
        # WP4: the area is UP only while every common-mode chain is up. A
        # q = 0 chain consumes nothing and is skipped (bit-identity).
        self.cm_up = None
        for cm in getattr(area, "common_mode", ()) or ():
            if float(cm.q) <= 0.0:
                continue
            from services.adequacy.copt import CoptUnit

            chain = CoptUnit(name=f"cm:{cm.link}", capacity_mw=1.0,
                             q=float(cm.q), mttr_hours=float(cm.mttr_hours))
            up = np.ascontiguousarray(sample_capacity(
                (chain,), H, draws, _fresh(ss, (CM_STREAM_KEY - int(cm.stream),)),
                periods=hub.periods).T).astype(np.float64)
            self.cm_up = up if self.cm_up is None else self.cm_up * up
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
        if self.cm_up is not None:
            # A common-mode event takes the Links AND the grid down together.
            offered = offered * self.cm_up[h]
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
                if self.track:
                    self.own_mwh += float(given.sum())
                g_def = np.where(g_def > 0.0, unmet, g_def)
        self.surplus_g = np.maximum(-g_def, 0.0)
        if self.cm_up is not None:
            self.surplus_g = self.surplus_g * self.cm_up[h]
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
        if self.track:
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
            before = self.soc.sum() if self.track else 0.0
            _charge_only(left, self.soc, self.p_rem, self.e_nom, self.eff_s,
                         self.eff_d)
            if self.track:
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

    # The Links are excluded here and sampled by their area's own call; as
    # placeholders they keep their slots without generating their paths twice.
    hub_units = tuple(_placeholder(u) if i in all_imp else u
                      for i, u in enumerate(units))
    hub_t = np.ascontiguousarray(sample_capacity(
        hub_units, H, draws, _fresh(ss), exclude=all_imp,
        periods=hub.periods).T)
    areas = [_AreaState(a, hub, units, ss, H, draws, grid_storage_enabled,
                        initial_soc_frac, track=trace is not None)
             for a in z.areas]
    add_order = sorted(range(len(z.areas)), key=lambda k: (
        min(z.areas[k].import_idx) if z.areas[k].import_idx else len(units), k))

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
            # float32 accumulation in Link-position order, like
            # sample_capacity's: the hub's units, then each area's import —
            # so unbound areas replay v1 (see the module docstring).
            cap32 = hub_t[h]
            for k in add_order:
                cap32 = cap32 + offers[k][1].astype(np.float32)
            cap = cap32.astype(np.float64)
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

def _grid_surfaces(units, xs, periods) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    Per hour ``(P[C_h < x_h], E[max(x_h − C_h, 0)])`` for the fleet
    ``units``, once per threshold array in ``xs`` — the COPT surface's own
    recipe (``copt._screen_block``): split the fleet, net the units beyond
    ``K_EXACT`` at expectation and the rate-zero profiled ones at full
    availability, convolve the two-state units, mix the profiled ones
    exactly. Units with a per-period capacity series are evaluated block by
    block at their constant block capacity, as ``screening_analysis`` does.
    The split and the table depend on the fleet only, so they are built
    ONCE per block and every threshold is read off them.
    """
    from services.adequacy.activity import block_capacity
    from services.adequacy.copt import (
        build_copt,
        deterministic_output,
        mixture_hourly,
        netted_expectation,
        split_fleet,
    )

    xs = [np.asarray(x, dtype=np.float64) for x in xs]
    if not xs:
        return []
    H = xs[0].shape[0]
    has_series = any(getattr(u, "capacity_series", None) is not None
                     for u in units)
    blocks = tuple(periods) if has_series else (("ALL", 0, H),)
    out = [(np.zeros(H, dtype=np.float64), np.zeros(H, dtype=np.float64))
           for _ in xs]
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
        hb = end - start
        split = split_fleet(block_units)
        shift = (netted_expectation(split.netted, hb)
                 + deterministic_output(split.deterministic, hb))
        dist = build_copt(list(split.table))
        for x, (lolp, es) in zip(xs, out):
            lolp[start:end], es[start:end] = mixture_hourly(
                dist, x[start:end] - shift, split.mixed)
    return out


def _grid_surface(units, x, periods) -> tuple[np.ndarray, np.ndarray]:
    """``_grid_surfaces`` for one threshold array."""
    return _grid_surfaces(units, [x], periods)[0]


def _grid_expected_shortfall(units, x, periods) -> np.ndarray:
    """``E[max(x_h − C_h, 0)]`` per hour (``_grid_surface``'s second half)."""
    return _grid_surface(units, x, periods)[1]


#: ``f`` within this of 1 is 1: an ample grid with q > 0 gives f = 1 − 5e-15,
#: which would otherwise profile the Link (and cost it a K_EXACT slot) for
#: nothing (review of WP3).
SURPLUS_SNAP_TOL = 1e-9


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
    (_, es_r), (_, es_rc) = _grid_surfaces(grid_units, [r, r + c_grid],
                                           periods)
    with np.errstate(divide="ignore", invalid="ignore"):
        f = np.where(live, ratio * (c_grid + es_r - es_rc) / np.where(
            live, cap, 1.0), 1.0)
    f = np.where(cap > 0.0, np.where(ratio > 0.0, f, 0.0), 1.0)
    f = np.clip(f, 0.0, 1.0)
    return np.where(f >= 1.0 - SURPLUS_SNAP_TOL, 1.0, f)


# ── review of WP3: the exact import metric ───────────────────────────────

#: At most this many levels for the hub-side import distribution; the level
#: width is ``max(1 MW, total import cap / MAX_IMPORT_LEVELS)``.
MAX_IMPORT_LEVELS = 128


def _area_common_mode_q(area) -> float:
    """
    Probability that the area's common-mode event is DOWN in an hour
    (independent chains: 1 − Π(1 − q_j)); 0 without common-mode data.
    """
    up = 1.0
    for c in getattr(area, "common_mode", ()) or ():
        up *= 1.0 - float(c.q)
    return 1.0 - up


def exact_import_metrics(z: ZonalInputs, *,
                         max_levels: int = MAX_IMPORT_LEVELS) -> dict:
    """
    LOLE / EUE with the import's per-hour DISTRIBUTION mixed in exactly —
    the analytic twin of the zonal MC without storage.

    The class-A screening holds the grid at its expected surplus
    (``expected_surplus_fraction``), which the WP3 review measured off by
    −39 % … +39 % in LOLE. Hours are independent in the COPT, so the exact
    per-hour answer is a mixture over the import's levels:

        LOLP_h = Σ_j P[D_h = j·δ] · P[C_hub,h < r_h − j·δ]

    where ``D_h = Σ_areas up_cm · min(Σ s_i·cap_i,h + firm_h,
    ratio_h·(C_grid,h − r_grid,h)⁺)``. Per area: every Link state
    combination (2^m, m sampled Links in the area), the grid survival
    ``P[D ≥ j·δ] = P[C_grid ≥ r + j·δ/ratio]`` from the area's own COPT, the
    common-mode event (WP4) as a point mass at 0; areas are independent and
    convolved per hour. ``D`` is rounded DOWN to the level grid (≤ δ MW
    conservative; exact when every quantity is a multiple of δ, e.g. δ = 1
    MW on integer data). With no hub or grid storage this equals the MC's
    expected LOLE / EUE (pinned against a 4000-draw MC).
    """
    from services.adequacy.copt import _availability_mw

    hub = z.hub
    H = hub.residual.size
    imp_all = {int(i) for a in z.areas for i in a.import_idx}
    base = [u for i, u in enumerate(hub.units) if i not in imp_all]
    firm_total = np.zeros(H, dtype=np.float64)
    specs = []
    total_max = 0.0
    for a in z.areas:
        firm = np.asarray(a.firm_import_mw, dtype=np.float64)
        firm_total = firm_total + firm
        links = [(float(hub.units[i].q), _availability_mw(hub.units[i], H))
                 for i in a.import_idx]
        omax = float(firm.max(initial=0.0)) + sum(float(v.max(initial=0.0))
                                                  for _q, v in links)
        total_max += omax
        specs.append((a, firm, links, omax))
    delta = max(1.0, total_max / float(max_levels)) if total_max > 0 else 1.0
    res = hub.residual + firm_total

    pmf = np.ones((H, 1), dtype=np.float64)
    for a, firm, links, omax in specs:
        L = int(np.floor(omax / delta + 1e-9))
        js = np.arange(L + 2)
        # S[:, j] = P[D_grid ≥ j·δ] before the Link cap; S[:, 0] = 1.
        S = np.zeros((H, L + 2), dtype=np.float64)
        S[:, 0] = 1.0
        if a.grid is None:
            S[:, 1:] = 1.0
        else:
            ratio = np.asarray(a.delivery_ratio, dtype=np.float64)
            r = a.grid.residual
            finite = ratio > 0.0
            if finite.any():
                inv = 1.0 / np.where(finite, ratio, 1.0)
                surfaces = _grid_surfaces(
                    a.grid.units,
                    [np.where(finite, r + j * delta * inv, 0.0)
                     for j in range(1, L + 2)],
                    a.grid.periods)
                for j, (lp, _) in enumerate(surfaces, start=1):
                    # A dead Link (ratio 0) passes nothing: survival 0.
                    S[:, j] = np.where(finite, 1.0 - lp, 0.0)
        pk = np.zeros((H, L + 1), dtype=np.float64)
        for bits in itertools.product((0, 1), repeat=len(links)):
            p = 1.0
            o = firm.copy()
            for (q, avail), b in zip(links, bits):
                p *= (1.0 - q) if b else q
                if b:
                    o = o + avail
            if p <= 0.0:
                continue
            J = np.floor(o / delta + 1e-9).astype(np.int64)      # (H,)
            capped = np.where(js[None, :] <= J[:, None], S, 0.0)  # (H, L+2)
            pk += p * (capped[:, :-1] - capped[:, 1:])
        q_cm = _area_common_mode_q(a)
        if q_cm > 0.0:
            pk *= (1.0 - q_cm)
            pk[:, 0] += q_cm
        new = np.zeros((H, pmf.shape[1] + L), dtype=np.float64)
        for j in range(pmf.shape[1]):
            new[:, j:j + L + 1] += pmf[:, j:j + 1] * pk
        pmf = new

    lolp = np.zeros(H, dtype=np.float64)
    eue = np.zeros(H, dtype=np.float64)
    levels = [j for j in range(pmf.shape[1]) if (pmf[:, j] > 0.0).any()]
    surfaces = _grid_surfaces(base, [res - j * delta for j in levels],
                              hub.periods)
    for j, (lp, es) in zip(levels, surfaces):
        w = pmf[:, j]
        lolp += w * lp
        eue += w * es
    weights = np.asarray(hub.weights, dtype=np.float64)
    return {
        "lole_hours": float((weights * lolp).sum()),
        "eue_mwh": float((weights * eue).sum()),
        "delta_mw": float(delta),
        "levels": int(pmf.shape[1]),
        "storage_included": False,
        "note": ("analytic LOLE / EUE with the import's per-hour distribution "
                 "(Link states × grid-area COPT × common mode) mixed in "
                 "exactly; hours independent, no storage — the MC's "
                 "expectation when neither the hub nor the grid has storage; "
                 f"import rounded down to {delta:g} MW levels"),
    }
