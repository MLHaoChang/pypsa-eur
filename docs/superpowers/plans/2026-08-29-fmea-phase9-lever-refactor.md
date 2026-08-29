# Phase 9 — the loop's lever, generalised (plan, v1)

## Why

Phase 7 built a loop that tunes the ENS cap until the plan meets an MC-LOLE
target. Phase 8 built the reserve margin because that loop's commonest honest
verdict was **`unreachable` — the cap never bound**: on a network whose firm
capacity already covers demand the LP sheds nothing at any ceiling, so no cap
changes the plan and the MC's loss of load comes entirely from outages the
proxy never models. Phase 8's acceptance run measured what the margin does on
exactly that shape of network: **LOLE 12.41 h → 1.32 h**, intervals separated.

So the tool now has a lever that works and a loop that cannot pull it. Phase 9
connects them.

**This was explicitly NOT a Phase-8 bolt-on.** The Phase-7 review (finding
[B5]) walked the controller line by line against a margin lever and found
seven independent breaks; the Phase-8 plan cut the lever into this phase on
that evidence. What follows is that list, re-verified against the code as it
stands today, with one item already resolved by Phase 8.

## 1. What actually breaks (verified against `services/adequacy/coupling.py`)

| # | Code today | Why a margin breaks it |
|---|---|---|
| 1 | `_tighten`: `step = eps/4.0` … `max(step, EPS_FLOOR_PERMYRIAD)` | Strictly **decreasing**. A stricter margin is a **larger** one, so the loop would walk `m` toward zero — toward *no standard* — while believing it was tightening. |
| 2 | `informed = 0.5 * eps * (ens/cap)` | Divides by `cap_mwh`. There is no cap for a margin lever; the term is dimensionally meaningless. |
| 3 | `cap_mwh < ENERGY_FLOOR_MWH ⇒ unreachable` | An energy floor. With no ENS cap set, `cap_mwh` is None and the test never fires. |
| 4 | `eps = max(eps0, EPS_FLOOR_PERMYRIAD)`, `assert e > 0` | `EPS_FLOOR_PERMYRIAD = 0.01` is 1e-6 *of demand* as a cap; as a margin it is 1 %. **`m = 0` is a legitimate request** and would be silently converted. |
| 5 | infeasible ⇒ `unreachable` | The nesting is still valid (larger `m` ⇒ strictly smaller feasible set) but the loop must be moving toward larger `m` for it to mean anything. |
| 6 | refinement `if not miss > met * (1+1e-9): break` | For a margin the **met** end is the larger value, so this is false on the first pass and refinement silently never runs. |
| 7 | tie-break `key=(cost, -eps_permyriad)`; the field name itself | Prefers the larger value (right for a cap, backwards for a margin), and `eps_permyriad` is **wire-visible** in `LoopPanel.tsx` and `api/simulation.ts`. |

**Resolved by Phase 8, verified:** the report no longer fires only on the ENS
stash — `solver_service.py` reads both and the comment says so ("the report
fires when EITHER standard was enforced"). Without that, every margin iterate
would have returned `no_report` and the loop would have ground the margin to
zero. It is worth naming as the one break that closed itself, because it means
the remaining work is a controller refactor and not a plumbing rescue.

**Route-level, all currently hard-coded to the cap:** `solve_at` builds
`dataclasses.replace(cfg, ens_cap_permyriad=eps)`; `_restore_closing` writes
`ens_cap_permyriad = eps_star`; `_verdict_copy` tells the user to "set
ens_cap_permyriad = …"; the record keys are `eps0` / `eps_star`.

## 2. The shape: a `Lever`, injected

The controller stops knowing what it is tuning. A `Lever` supplies:

```python
@dataclass(frozen=True)
class Lever:
    name: str                    # "ens_cap" | "reserve_margin"
    apply: Callable              # (cfg, value) -> cfg
    stricter: Callable           # (value, SolveResult) -> next value  (informed)
    limit: float                 # the value beyond which the search stops
    at_limit: Callable           # (value) -> bool
    start: Callable              # (cfg) -> the default starting value
    label: str                   # what the verdict tells the user to set
```

- **Direction lives in `stricter`**, not in the controller. For the cap,
  stricter is smaller; for the margin, larger. Every `<`/`>` in the search that
  today encodes "smaller is stricter" becomes a comparison **through the
  lever's own ordering**, so bracketing, refinement and tie-breaks are
  direction-agnostic by construction rather than by two parallel code paths.
- **`at_limit` replaces the energy floor** (#3). The cap's limit stays the
  `≤ 0` no-target sentinel; the margin's is the schema ceiling *and* —
  better — the point where Phase 8's stash already proves the case hopeless.

### 2.1 The margin's informed step — and why Phase 8 makes it exact

The cap's informed step used `achieved_ens / cap` to cross a slack region in
one solve. The margin has a **sharper** analogue, and it is free: Phase 8's
report block publishes `required_mw` and `firm_mw` per period. When the margin
is not binding, the plan already carries `firm_mw`, so the smallest margin that
would bind is `firm_mw / peak_mw − 1`. Jump there and add the step, instead of
creeping. A non-binding margin costs exactly one wasted solve, never a
geometric walk.

### 2.2 The margin's limit — `max_achievable_mw`

Phase 8 stashes `max_achievable_mw` (derated fixed + derated `p_nom_max` of
active extendables) precisely so preflight can refuse an unreachable margin.
The loop gets it for free: `limit = max_achievable_mw / peak_mw − 1`. Above
that no plan exists, so the loop stops and reports `unreachable` **without
spending a solve to discover it** — and the verdict can say "your candidate set
tops out at m = X", which is a different and far more useful statement than
"the search did not reach it".

## 3. Wire compatibility — the part that must not break

`eps_permyriad` is read by `LoopPanel.tsx` and typed in `api/simulation.ts`.
The rename is therefore additive, not a swap:

- Rows carry **`lever_value`** and **`lever`** (the discriminator), and keep
  **`eps_permyriad` as a deprecated alias** populated only when
  `lever == "ens_cap"`. Same for `eps0`/`eps_star` beside `lever_start` /
  `lever_star`.
- `POST /results/coupling_loop` gains `lever: "ens_cap" | "reserve_margin"`,
  **defaulting to `"ens_cap"`** — an existing caller sees no change at all.
  ★ bitten test: a body with no `lever` produces a payload byte-identical in
  its cap fields to the Phase-7 behaviour.
- `restore: "final"` writes **the lever's own config field**, and the verdict
  names it. A loop that certified a margin and then told the user to set an ENS
  cap would be worse than no advice.

## 4. What the loop can now say that it could not before

The Phase-7 `NEVER_BOUND` verdict — "the cap never bound; the loss of load
comes from outages the LP does not model; an energy cap has no leverage" — has
a next action for the first time. When the cap lever ends `unreachable` with
that diagnosis, the verdict should name the margin lever explicitly. **Not
auto-switch**: the two levers buy different things (a cap constrains energy, a
margin buys firm capacity), the cost consequences differ, and silently changing
which standard a study enforced is exactly the kind of thing this program does
not do. Recommend, don't act.

## 5. Acceptance (self-calibrated, per Phase 8's lesson)

★ **A1 — the margin lever reaches `met` where the cap lever reports
`unreachable`, on the same network.** This is the phase's whole claim and the
fixture is already known: S17's, where Phase 7 measured `unreachable` in two
solves and Phase 8's acceptance measured LOLE 12.41 → 1.32 at a derived margin.
The target is derived, never chosen (a margin inside the largest-unit step
moves EUE but not LOLE).
★ **A2 — the informed step does not creep.** On a non-binding start the loop
reaches a binding margin in ONE solve, using `firm_mw / peak_mw − 1`. Bite:
geometric stepping.
★ **A3 — the limit is respected without spending solves.** A target that needs
more than `max_achievable_mw` reports `unreachable` naming the ceiling, with
`solves_used` strictly less than the budget. Bite: search to the budget.
★ **A4 — the cap lever is unchanged.** The whole Phase-7 controller suite must
pass untouched, and a no-`lever` request must be byte-identical in its cap
fields. This is the regression that matters most: Phase 9 is a refactor of
working code, and the way refactors go wrong is silently.

## 6. Non-goals

- Auto-selecting or auto-switching levers (§4).
- Tuning both levers jointly — a two-dimensional search whose Pareto surface
  needs its own design.
- ELCC-weighted derating (still the Phase-8 seam).
- Per-zone margins.

## 7. Open decisions for review

1. Should `Lever` live in `coupling.py` or its own module? (It is data + three
   callables; `coupling.py` keeps the controller's purity argument intact, but
   the route owns the config-writing halves.)
2. `eps_permyriad` as a deprecated alias forever, or removed once the panel
   reads `lever_value`? (Same session, so a swap is cheap — but the payload is
   also what a user's saved study record contains.)
3. Is `firm_mw / peak_mw − 1` right when several periods disagree? (Take the
   max over periods, presumably — the binding period is the one that matters —
   but state it.)
4. Should the loop refuse a margin lever when preflight would error anyway
   (unpriceable assets), or let the first solve surface it?
5. Does `restore: "final"` on a margin need to clear any ENS cap the user had
   set, or leave both standards in place? (Leave both, presumably: the user set
   the cap deliberately. But then the certified plan met *both*, and the verdict
   must say so.)
