# Margin-loop spec (Phase 9) — v1, BINDING

Workers implement THIS document. Rationale and review findings live in the plan
(`docs/superpowers/plans/2026-08-29-fmea-phase9-lever-refactor.md`, v2).
Where the two disagree, this spec wins and the master is told. Amendments are
recorded at the bottom, never silently.

## 0. The hard constraint

**`services/adequacy/coupling.py` MUST NOT BE MODIFIED.** Not a line. The
entire design rests on the controller already being correct for this lever
under the substitution below, and the Phase-7 suite is the regression oracle
that proves it. ★ A worker-visible check: `git diff --stat` must show no
change to that file, and `tests/test_adequacy_coupling.py` must pass verbatim.

Two live bugs the review surfaced are ALREADY FIXED and are not in scope:
the margin-only `ens_mwh = 0.0` report (`22a040e`) and the never-bound verdict
prescribing a margin the user already set (`9026c68`).

## 1. The substitution

```
x = 1 / (1 + m)          m ≥ 0  ⇒  x ∈ (0, 1]
m = (1 / x) − 1
```

Smaller `x` ⇒ larger `m` ⇒ stricter, which is the ordering `coupling.py`
assumes throughout. Both directions live in ONE place —
`services/adequacy/margin_lever.py`, a pure module (no routes, no `_state`, no
`pypsa`) — with:

```python
def to_x(m: float) -> float           # 1/(1+m); m<0 → ValueError
def to_margin(x: float) -> float      # 1/x - 1; x<=0 → ValueError
```

★ **A5, property test**: over a spread of `m` in `[0, 5]`, `to_margin(to_x(m))
== m` to 1e-12, and `m1 < m2 ⇔ to_x(m1) > to_x(m2)` (strict antitone). This is
the phase's only new mathematics; pin it.

## 2. The route — `POST /results/margin_loop`, `GET`, `/abort`

Mirror `post_coupling_loop`'s structure (worker thread, closed-over record,
rebind-not-append `iterations`, abort, `restore`). It is a SEPARATE study key
`_state["margin_loop"]`, registered in the 409 mesh in BOTH directions
alongside `coupling_loop` (add it to `study_state.STUDY_KEYS`/`STUDY_LABELS`
and to every existing guard, including the two foreground solve entrypoints).

### 2.1 Bindings

- `solve_at(x)` = `dataclasses.replace(base_cfg, reserve_margin=to_margin(x))`
  + `sweep._solve_once`. It returns the SolveResult shape unchanged EXCEPT:
  **`cap_mwh=None`** (spec §2.3 explains why this is load-bearing, not
  incidental) and `binding` taken from the MARGIN's own block —
  `any(p["binding"] for p in rep["reserve_margin"]["by_period"])` mapped to
  `"system_cap"` when true, else `"voll"`. The controller's `reusable`
  pre-test (`binding != "system_cap"`) then carries real information for this
  lever instead of always being True.
- `evaluate` is IDENTICAL to the coupling loop's (same snapshot,
  `keep_zero_capacity=True`, the same pinned `mc_adequacy(inputs, draws=N,
  seed=S, max_draws=N)` call, the same plan hash).
- `restore: "base" | "final"`; `"final"` writes **`reserve_margin`**, never
  `ens_cap_permyriad`, and a user-set ENS cap is left untouched throughout
  (both `solve_at` and the restore build from `base_cfg`). The verdict on a met
  run must therefore say the certified plan met **both** standards when a cap
  was also set.

### 2.2 `cap_mwh=None` is mandatory

`coupling.py` ends the search with `unreachable` when
`row["cap_mwh"] is not None and row["cap_mwh"] < ENERGY_FLOOR_MWH`. On a
margin-only report `cap_mwh` is `0.0` (the per-period loop never runs), so
passing it through fires that test on the FIRST miss and every run ends
`unreachable` after one solve. Returning `None` makes the test a genuine
no-op — the only correct reading for a lever with no energy cap.
★ bitten test: pass `0.0` instead and the run must end `unreachable` with
`solves_used == 1`.

### 2.3 The informed step

The controller's `_tighten` receives `(x, ens_mwh, cap_mwh)`. With
`cap_mwh=None` its informed term is skipped and it degrades to the blind
`x/4` — which in margin terms is `m: 0 → 3`, a large but *safe* first jump.
The route sharpens it by **pre-positioning `x0`**, not by changing the
controller: before the loop, compute from a single probing solve at the user's
current margin (or 0):

```
m_tight = min over P of (firm_mw_P / peak_mw_P) − 1
x0      = to_x(m_tight · (1 + STEP_OVERSHOOT))     # STEP_OVERSHOOT = 0.05
```

`m_tight` is the smallest margin at which the incumbent plan is *tight* — at
exactly that value the plan is feasible, unchanged, same hash, same LOLE, and
flagged `binding` while nothing moved. The step MUST strictly exceed it, hence
the overshoot. Aggregate by **`min`** over periods: the first period to bind is
the binding one, and `max` would overshoot the bracket entirely.
★ bitten test: a non-binding start reaches a binding margin in ONE solve;
bite = drop the overshoot and assert the plan is unchanged.

### 2.4 Synchronous refusals (all pre-solve, via `reserve_margin_facts`)

- **Unreachable ceiling**: `m_max = min over P of (max_achievable_P / peak_P)
  − 1`, non-finite ⇒ `+inf`; clamp to the schema's `le=5`. Refuse 422 naming
  the ceiling when the target cannot be met at any `m ≤ m_max`… but note the
  target is an MC-LOLE, not a margin, so the reachable statement is about the
  MARGIN's own reachability: refuse when `m_max <= 0` (no headroom at all).
  The loop stops at `m_max` and reports `unreachable` naming it otherwise.
- **Unpriceable assets**: refuse 422, reusing the validator's sentence
  verbatim. Otherwise every iterate fails validation identically and the run
  ends `budget_exhausted` advising "raise max_solves", which can never work.
- **`rolling` only** — NOT `myopic`, which the margin's validator downgrades to
  a warning with a stated reason. Copying the cap loop's blanket refusal would
  deny a supported configuration.
- **No VoLL requirement.** The margin is a constraint, not a price; a margin
  loop on a VoLL-free network is well defined. (The cap loop's 422 stays.)
- The usual: `target_lole_h > 0`, draws in `[1, MAX_DRAWS]`, `max_solves` in
  `[1, MAX_LOOP_SOLVES]`, `restore` in `{base, final}`, nothing-to-sample, and
  the up-front resolution floor.

### 2.5 `validation_failed` is final for this lever

An out-of-reach margin surfaces as `validation_failed`, which
`_is_infeasible` does not match — the controller would keep stepping. §2.4's
pre-solve ceiling is the primary defence; belt-and-braces, the route's
`solve_at` maps a `validation_failed` condition to `condition="infeasible"`
**only when** `reserve_margin_facts` confirms the margin is the cause, so the
controller's nesting logic applies correctly. ★ bitten test.

### 2.6 Payload

`{study: "margin_loop", lever: "reserve_margin", lever_label,
lever_unit: "%", status, verdict, target_lole_h, basis, confident,
lever_star (the certified MARGIN, not x), resolution_floor_h, restore,
base_restored, solves_used, iterations: [rows], final, warning}`.

Rows are the controller's rows with `eps_permyriad` **translated to
`lever_value` (a margin)** by the route before storage — the controller's
internal `x` must never reach the wire. ★ bitten test: no payload field
anywhere contains an `x` value.

## 3. Frontend

A `lever` discriminator, NOT a nullable alias — `compact()` is typed `number`
and `isFinite(null)` is `true` in JS, so a null alias throws inside `rows.map`
and takes the panel down. Either a second panel or one panel driven by `lever`;
the column header, badge suffix (`‱` vs `%`) and **`restoreSentence`'s config
field name** all come from the payload. That last one is a second hard-coded
`ens_cap_permyriad` site the plan's v1 missed; it renders unconditionally.

## 4. Acceptance

★ **A1** — the margin loop reaches `met` on the S17-shaped fixture where the
cap loop reports `unreachable`. Target derived, never chosen.
★ **A2** — §2.3's one-solve step. ★ **A3** — §2.4's ceiling refusal on a
fixture with a **finite** `p_nom_max`. ★ **A4** — `coupling.py` unchanged
(`git diff`) and `test_adequacy_coupling.py` passes verbatim. ★ **A5** — §1's
property test.

## 5. Gates

Backend adequacy gate 0 failed with the 4 slow benchmarks unchanged; frontend
`tsc -b` + vitest 0 failed; live S19 in `qa_e2e.py`. Every ★ red first, bitten,
no worker commits.

## Amendments

(none yet)
