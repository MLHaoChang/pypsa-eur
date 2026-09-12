# Model Horizon: guided steps and page decomposition — design

**Date:** 2026-08-12
**Status:** approved, ready for planning
**Scope:** restructure the Model Horizon page from one 1,476-line scroll doing six
jobs into a summary-first guided-step flow, decomposed into focused files. Pure
frontend. No API change, no backend change, no change to what any control does.

## Why

The page's own header comment describes a top-to-bottom flow — status, mode,
mode-specific config, weightings — and that flow was sound when the page was
small. It now runs to 1,476 lines and does six unrelated jobs on one scroll, one
of which ("Multi-period planning") is 490 lines and contains three jobs by
itself.

Two facts about actual use shape the design, and they pull against each other:

- Configuring the horizon is **mostly one-time**, revisited rarely.
- But **all four major jobs get touched** — range/resolution, investment years
  and their weights, representative-week sampling, and the per-snapshot weights.

So nothing here can be buried behind an "advanced" flap on the grounds that it
is rarely used; everything is used. What varies is *depth within* a job, not
whether the job matters. The redesign therefore walks the user through every job
once, well, and hides only the depth inside each.

## Page shape

**Two entry states, decided by the network rather than stored UI state.** A
project whose horizon is unset opens at step 1; anything else opens on the
summary. "Unset" is `snap.count <= 1` — the default single "now" snapshot — the
same heuristic the page already uses to decide whether to hydrate the
multi-period constructor's defaults.

**The summary** is one line per step, each a sentence of current state
("Multi-period, 3 investment years"; "2024 calendar year, hourly"), each
clicking through to its step. It is the everyday view.

**Per-step Apply.** Each step commits on its own, exactly as every section does
today. Deliberately NOT a staged wizard with one final commit: that needs
transactional support the backend does not have, and a half-applied stage would
leave the UI and the network disagreeing — a failure mode this codebase has
already been bitten by elsewhere.

## The steps, grouped by index level

The page configures PyPSA's two-level `(period, timestep)` snapshot MultiIndex,
and the six jobs split cleanly along that seam. Grouping by it teaches the
underlying model rather than merely partitioning the screen.

**Period level** — absent entirely in single-period mode:

1. **Mode** — single vs multi. The one decision that reshapes everything below.
2. **Investment years** — the year chips.
3. **Period economics** — years, objective, PV preview.
   *Advanced:* per-carrier load scalers, per-period CAPEX budgets.

**Timestep level** — always present:

4. **Operational window** — single-period: start / end / resolution.
   Multi-period: one operational year replicated per period.
   *Advanced:* a distinct range per period.
5. **Representative weeks** — sampling, gated on `can_sample_weeks` as today.
6. **Snapshot weights** — bulk apply.
   *Advanced:* CSV round-trip and the paginated per-row table.

A single-period user therefore sees **four** steps (1, 4, 5, 6), roughly three
controls each. A multi-period user sees six. No control is removed.

## Code structure

`ModelHorizon.tsx` becomes a shell of roughly 200 lines: queries, mutations,
step routing. Everything else moves out.

| File | Responsibility |
|---|---|
| `pages/ModelHorizon.tsx` | Shell: queries, mutations, routing between summary and steps |
| `pages/modelHorizon/HorizonSummary.tsx` | The summary view |
| `pages/modelHorizon/StepShell.tsx` | Numbered rail, step chrome, the Advanced disclosure primitive |
| `pages/modelHorizon/StepMode.tsx` | Step 1 |
| `pages/modelHorizon/StepYears.tsx` | Step 2 |
| `pages/modelHorizon/StepPeriodEconomics.tsx` | Step 3 |
| `pages/modelHorizon/StepWindow.tsx` | Step 4 |
| `pages/modelHorizon/StepSampling.tsx` | Step 5 |
| `pages/modelHorizon/StepWeights.tsx` | Step 6 |
| `pages/modelHorizonModel.ts` | Existing pure derivations, plus summary sentences and step visibility/completion predicates |

Mutations stay in the shell and are passed to steps as props, so each step file
is presentational and independently readable. `modelHorizonModel.ts` stays pure —
no React, no network — which is what makes the new predicates unit-testable
without rendering.

## Testing — and a debt this must not repeat

The final review of the preceding defect branch recorded, accurately:

> the page component still has zero render tests; every assertion is on extracted
> pure functions, so the JSX wiring — the actual B1/B2/B3 defect surface — is
> verified by reading only.

**This redesign rewrites exactly that JSX.** Six defect fixes landed on this page
across two branches; their pure helpers and 27 unit tests survive untouched, but
the wiring that connects those helpers to the DOM is being rebuilt. Doing that
with no render coverage would put every one of those fixes back at risk with
nothing to catch the regression.

So render tests are a requirement of this design, not a nice-to-have. Three are
mandatory, chosen because they are the fixes most exposed by a JSX rewrite:

| Render test | Pins |
|---|---|
| Weightings table on a multi-period network | the PATCH key is `period\|iso`, not a bare ISO (defect B1) |
| Resolution display | reads `snap.freq` from the network, never local form state (defect B3) |
| PV preview column | greys out when auto-discount is inert (Task 3 + its later gating fix) |

Plus unit tests for the new pure predicates: summary sentences for each step in
both modes, step visibility (period steps absent in single-period mode), and the
unset-horizon entry rule at the `snap.count <= 1` boundary.

## Out of scope

- Any change to what a control does, to the API, or to the backend.
- The two known-and-ruled residuals: stale `capex_budget_per_period` /
  `load_scalers_by_carrier` keys are still not pruned on period removal, and the
  unreachable demotion branch in `_reapply_snapshot_weights` stays.
- `SolverSettings.tsx`, whose tab pattern was considered as a model and rejected:
  six equal doors give a first-time user no ordering, and its three-coupled-edits
  fragility is documented in CLAUDE.md as a trap.

## Accepted costs

- **A large frontend change to a page that just received six fixes.** Mitigated
  by §Testing, not by hope.
- **No "cancel the whole setup".** Per-step Apply means each step commits as you
  go, as today. Reverting means editing the step back.
- **The summary adds a second place that describes horizon state**, alongside the
  status strip. They must not drift: both derive from the same
  `modelHorizonModel.ts` functions, never from separately computed values.

## Concurrency note

Checked at design time: `feature/local-app-impl` clean at `bd4c9ea5`, with
another session committing chat/assistant work to the same branch throughout the
preceding two branches. This work touches `pages/ModelHorizon.tsx`,
`pages/modelHorizonModel.ts` and a new `pages/modelHorizon/` directory — outside
that session's file set — but re-check before implementing; this checkout is
shared.
