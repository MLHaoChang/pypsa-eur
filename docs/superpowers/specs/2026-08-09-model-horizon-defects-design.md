# Model Horizon tab: defect fixes — design

**Date:** 2026-08-09
**Status:** approved, ready for planning
**Scope:** eleven defects found by audit of the Model Horizon tab and its ten
backing endpoints. Fixes only. The page's structural redesign — it does six
unrelated jobs on one scroll — is deliberately deferred to a separate brainstorm
against a corrected baseline.

## Why fixes first

The page is 1,489 lines with **no test file**. Three of the defects below
(wrong-period writes, duplicate React keys, a status card that reads form state
instead of network state) live in exactly the surfaces a decomposition would
rewrite. Landing the redesign on top of them would either carry the bugs forward
into new code or entangle "did the redesign break this?" with "was this already
broken?". Correct first, then restructure.

## The defects

Severity is by user-visible consequence, not by fix size.

| # | Severity | Defect | Site |
|---|---|---|---|
| B1 | High | Snapshot-weight edits write to the wrong investment period | `ModelHorizon.tsx:1418` |
| B2 | High | Duplicate React keys in the weightings table on multi-period | `ModelHorizon.tsx:1420,1424` |
| B3 | Med-high | "Resolution" stat card reports form state, not the network | `ModelHorizon.tsx:102,627` |
| B4 | Med-high | Manual and automatic discount paths disagree by the `years` factor | `ModelHorizon.tsx:412` vs `solver_service.py:4410` |
| B5 | Med-high | Adding an investment year destroys per-period operational ranges | `network.py:1691-1697` |
| B6 | Med-high | Build Year dropdown reads a config field nothing ever writes | `cardKit.tsx:353` |
| B7 | Low | Multi-period range string shows the base year, hiding the periods | `ModelHorizon.tsx:628` |
| B8 | Low | `updateLoadScaler` declared, never called | `ModelHorizon.tsx:289` |
| B9 | Low | Deleting a period leaves its CAPEX budget and load scalers behind | `ModelHorizon.tsx:211-221` |
| B10 | Low | Rep-week sampling overwrites custom weights, warns only in the audit log | `network.py:1610` |
| B11 | Process | No frontend test coverage for the page at all | — |

### B1 — the wrong-period write

`ModelHorizon.tsx:1418` derives the row key for both display and mutation:

```ts
const iso = String(wm.snapshot ?? wm.name ?? snap.snapshots[pageStart + i])
```

On a MultiIndex network neither of the first two exists.
`df_to_json(n.snapshot_weightings)` calls `reset_index()`, and the index levels
are named `period` / `timestep` (`network.py:485`), so the emitted record is:

```
['period', 'timestep', 'objective', 'generators', 'stores']
```

*(verified by execution against the same MultiIndex construction the endpoint
uses, not by inspection)*

So the expression falls to `snap.snapshots[pageStart + i]` — a bare ISO
timestep with no period. `update_snapshot_weightings` registers **two** keys per
row (`network.py:1267-1284`): the period-qualified `"2030|2024-01-01T00:00:00"`
and the bare `"2024-01-01T00:00:00"`, iterating period-major. The bare key is
therefore last-write-wins and resolves to the **final** period.

Net effect on a 2030/2040/2050 model: editing the 2030 row writes 2050's
weight. The refetch shows the edited row unchanged, so it reads as a dead input
rather than a misdirected one. The backend comment already names the
period-qualified form as canonical "for multi-period clients"; the only
multi-period client never sends it.

### B2 — duplicate keys, same root cause

`key={iso}` (line 1420) and `key={`sw-${iso}-…`}` (1424) inherit the same
unqualified value, so the identical operational hour under each period produces
identical keys. Any page spanning more than one period collides. Guaranteed
whenever timesteps-per-period is under `PAGE_SIZE` (100) — a 3-period × 24-hour
model is 72 rows on one page with 48 duplicate keys.

### B3 — the status card that isn't

`freq` is `useState('h')` (line 102) and `setFreq` has exactly one call site:
the single-period `<select>` at line 1227. That select does not render in
multi-period mode, and the state resets on every reload. The card therefore
reports "Hourly (h)" for a 3-hourly MultiIndex, and for a Daily flat network
after any refresh.

It sits in the strip the page's own header comment describes as *"what the
network is RIGHT NOW … read-only summary so the user grounds themselves"*.

### B4 — two discount paths, one claim of equivalence

| path | formula |
|---|---|
| Manual "Apply discounts" (`ModelHorizon.tsx:412`) | `objective = (1+r)^-(p-base)` |
| Auto-discount (`solver_service.py:4410-4413`) | `objective = (1+r_real)^-(p-ref) × years[p]` |

`r_real` additionally applies the Fisher correction for `inflation_rate`. With
`years=10` the two differ by 10×. The checkbox's own comment (line 843) states
it is *"Equivalent to clicking 'Apply discounts' every time before solving"*.

The manual path drops the period span, which is the term PyPSA's objective
weighting exists to carry.

### B5 — per-period ranges collapse on edit

`set_investment_periods` rebuilds with `[base_idx] * len(new_periods)`
(`network.py:1694`), where `base_idx` is the **first** existing period's
timestep level. The snapshot constructor's "Different year per period" mode —
built for multi-year weather data — is therefore destroyed by clicking `+` on a
year chip. No confirmation, no warning.

### B6 — two stores for one concept

`useBuildYearOptions` (`cardKit.tsx:353`) reads `cfg.investment_periods` from
SolverConfig. Nothing in the frontend writes that field; Model Horizon writes
`n.investment_periods` through `POST /network/investment_periods`. The dropdown
consequently always takes its fallback branch — a generic 5-year grid from the
current year.

Configure periods 2026/2027/2028 and the Build Year dropdown offers
2025/2030/2035/… Since `build_year ≤ period` gates asset availability, a
mismatched year makes the asset invisible to the LP without any error.

## The fixes

### 1. One key function, three consumers (B1, B2)

Extract a pure helper in `ModelHorizon.tsx`:

```ts
export function snapshotWeightKey(
  row: Record<string, unknown>,
  isMultiPeriod: boolean,
  fallbackIso: string,
): string
```

Returns `` `${row.period}|${row.timestep}` `` when multi-period and both fields
are present, plain ISO otherwise. It feeds the PATCH body, the React `key`, and
the displayed cell — the three that currently disagree. A **Period** column is
added to the table, rendered only when `snap.periods` is non-empty.

No backend contract change. One warning log is added in
`update_snapshot_weightings` when a bare ISO key resolves against a MultiIndex,
so an ambiguous write is visible in the audit log rather than silent.

### 2. Resolution from the network (B3)

`GET /snapshots` gains `freq: string | null` — `pd.infer_freq` over the timestep
level (period-0's slice when MultiIndex), falling back to the modal successive
delta, `null` when neither resolves. The stat card renders that value; `null`
renders as "irregular" rather than guessing.

The single-period `<select>` keeps its local state — it composes a *new* index,
which is a different job from reporting the current one.

### 3. One discount path (B4)

Delete the manual calculator: `discRate`, `discBase`, `discRateTouchedRef`,
`discBaseTouchedRef`, `applyDiscountFactors`, their two seeding effects, and the
input row. Auto-discount becomes the only automated path.

Add a read-only **PV factor** column to the period table showing what
auto-discount will write at solve time — `(1+r_real)^-(p-ref) × years[p]`,
computed frontend-side from `cfg.discount_rate`, `cfg.inflation_rate` and the
row's `years`, greyed when the checkbox is off. Manual override of the
`objective` cell is unaffected.

**Accepted cost:** the one-off "compute factors now" workflow becomes
hand-editing the objective column.

### 4. Per-period ranges survive editing (B5)

`set_investment_periods` builds `{period: DatetimeIndex}` from the existing
MultiIndex before rebuilding:

- a period in both the old and new sets keeps **its own** block
- a genuinely new period inherits the first existing period's range as a template
- a removed period's block is dropped

This replaces `[base_idx] * len(new_periods)`. The flat→multi promotion path is
unchanged — there, every period legitimately gets the flat range.

**Accepted cost:** no migration. A project already homogenised by the old code
stays homogenised; the fix prevents future collapse only.

### 5. Build Year reads the real store (B6)

`useBuildYearOptions` switches to a `useQuery` on `/network/investment_periods`,
project-keyed the same way `ModelHorizon` keys it. `cfg.investment_periods` is
left untouched — `solver_service.py:4329` still uses it for the API-level
cfg-only promotion path, which is a separate feature.

### 6. Mechanical (B7–B10)

- **B7** — `rangeStr` gains the `toDisplay` year-remap already used in
  `HorizonFilter.tsx`, so a 2030/2040/2050 model reads as spanning those years.
- **B8** — delete `updateLoadScaler`.
- **B9** — add `solverConfig` to `applyPeriods`' invalidation.
- **B10** — `sample_weeks` returns `had_custom_weights`; the toast says so when true.

## Tests

New `frontend/src/pages/ModelHorizon.test.tsx`:

| case | asserts |
|---|---|
| `snapshotWeightKey`, flat network | returns bare ISO |
| `snapshotWeightKey`, multi-period row | returns `"2030\|2024-01-01T00:00:00"` |
| `snapshotWeightKey`, missing fields | falls back to the positional ISO |
| React keys across a 3-period × 24-hour page | 72 distinct keys, no collision |
| resolution card, `freq: "3h"` | renders 3-hourly |
| resolution card, `freq: null` | renders "irregular", never "Hourly" |
| PV-factor preview | matches `(1+r_real)^-(p-ref) × years[p]` including the Fisher term |

New backend test: a 3-period network with three **distinct** operational ranges
keeps all three after `POST /investment_periods` adds a fourth year, and the new
period receives the first period's range.

Both suites land RED first, with the failing command and its output recorded in
the task report before any fix is written.

## Out of scope

- The structural redesign of the page. Separate brainstorm, after this lands.
- `cfg.investment_periods` as a concept — B6 stops reading it from the
  Properties panel but does not remove it.
- The `n.investment_periods` / `cfg.investment_periods` duality in general.

## Concurrency note

Checked at design time: tree clean on `feature/local-app-impl`, 26 live Claude
sessions, and the three target files (`ModelHorizon.tsx`, `network.py`,
`cardKit.tsx`) last modified 2026-07-25 / 07-31 / 07-29 — no overlap. Re-check
before implementation; this worktree is shared.
