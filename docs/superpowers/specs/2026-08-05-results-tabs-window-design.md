# Results tabs: default window and ranged fetches — design

**Date:** 2026-08-05
**Status:** approved, ready for planning
**Scope:** give the six windowing Results tabs a bounded default view and fetch only that window. AggregatedOverview is deliberately excluded.

## Problem

The 2026-08-04 range work converted the canvas overlay, which needed one row and
now fetches an aligned chunk. The Results tabs were left out on purpose: 85 call
sites across five files compute MWh and € via `shared.tsx::weightedSum`, and
putting all of them in the blast radius of a payload change at once was not
worth it.

The tabs are still on full payloads, and their default view is the worst case.
`Results.tsx:169-170` initialises `fromIso`/`toIso` to `''` and `:222` sets
`selectedPeriod` to `'all'`, so `resolveRange` returns `{from: 0, to: length-1}`
— the entire horizon. `Dispatch.tsx` issues nineteen queries, nine of them
per-snapshot series. On the measured 200-asset × 26,280-snapshot shape that is
roughly **470 MB**, rendered into a 26,280-point chart nobody can read.

**A plain range conversion would not fix this.** The tabs already window
client-side; converting them moves an existing window earlier, which helps only
when the user has set a filter. The expensive default — no filter — would be
untouched. The default has to change first, and then the ranged fetch is worth
doing.

## The constraint that defines the scope

**`AggregatedOverview` computes over the whole payload by design.**

```ts
const fullRange = useMemo(() => {
  const n = refTs?.data.length ?? 0
  return { from: 0, to: Math.max(0, n - 1) }
}, [refTs])
```

It uses `fullRange` at thirteen sites and `resolveRange` at none — it ignores
the Horizon filter deliberately, because its job is the whole-horizon summary.
Every other tab is the mirror image:

| tab | `fullRange` sites | `range` sites |
|---|---|---|
| AggregatedOverview | 13 | 0 |
| Dispatch | 0 | 55 |
| LoadFlow | 0 | 30 |
| Prices | 0 | 10 |
| LostLoadTab | 0 | 10 |
| Curtailment | 0 | 9 |
| StorageCycling | 0 | 3 |

So there is a clean boundary: **one summary tab that must see everything, and
six exploration tabs that already narrow.** Windowing the six is safe for the
same reason windowing the canvas was — the window already exists, it just moves
earlier. Windowing AggregatedOverview would silently turn its headline energy
and cost figures into window totals under whole-horizon labels.

**The cache collision that would have caused exactly that does not exist**, by an
accident of the current code:

```
AggregatedOverview:  ['proj', 'results', 'generators']                 3 elements
Dispatch:            ['proj', 'results', 'generators', resultSource]   4 elements
```

React Query compares keys structurally, so these are already distinct entries
and AggregatedOverview already pays for its own copy. Adding window bounds to
the six tabs' keys keeps them distinct; this design must not remove that
separation as a tidy-up.

## No probe is needed

The canvas had to probe with `?from=0&to=0` because it could not know a series'
length before fetching it. The tabs do not have that problem: `Results.tsx:174`
already fetches `/api/network/snapshots` as its own query
(`nk(currentProject, 'snapshots')`, `staleTime: 5_000`), giving the full ISO
index, `count`, and the `periods` array. `resolveRange(snap.snapshots, filter,
snap.periods)` therefore yields positional bounds with no results payload in
hand, and all six tabs read it from one cached entry.

## Part 1 — the default window

`Results.tsx:194-200` already has an effect that sets `fromIso`/`toIso` to the
network's full span once snapshots load. That effect becomes the place the
default window is chosen:

| network | default view | mechanism |
|---|---|---|
| any network, **total ≤ 8760 snapshots** | whole horizon, unchanged | as today |
| multi-period, total > 8760 | the **first investment period** | `selectedPeriod = uniquePeriods[0]` |
| flat, total > 8760 | the **first month** (720 rows) | `fromIso`/`toIso` |

**The threshold is tested against the TOTAL horizon, before any structural
branch.** An earlier draft of this table branched on multi-period first, which
would have windowed a 2-period × 24-snapshot network down to 24 rows — a
usability regression on exactly the small models where the whole point is that
nothing changes. It would also have altered the default view of the golden test
fixture, which is 2 periods × 24 snapshots. Total first, structure second.

**Multi-period defaults via `selectedPeriod`, not ISO bounds.** `resolveRange`
handles `selectedPeriod` natively by matching the parallel `periods` array,
whereas ISO bounds on a multi-period network are the documented trap: every
period replicates the same base operational year, so an ISO range matches rows
in every period at once. `filterContext.tsx:108-112` and CLAUDE.md both record
this. Using the period selector also reads correctly in the UI — "showing period
2030" rather than an opaque date span.

**8760 is the threshold** because a single hourly year is the largest horizon
that renders as a chart without windowing, and it is the natural unit of this
domain. Below it nothing changes, so small models behave exactly as they do
today.

**Discoverability is already built.** `Results.tsx:343` renders a chip showing
`from → to`, `:248-252` already computes whether a filter is active, and Reset
targets the network's own span. The default window therefore appears as an
active filter the user can see and clear in one click — not as a hidden
truncation.

## Part 2 — ranged fetches for the six

Each per-snapshot query in the six tabs passes the resolved window to its getter
and carries the window in its key:

```ts
const win = useMemo(
  () => resolveRange(snap?.snapshots ?? [], filter, snap?.periods),
  [snap, filter],
)
const { data: gensTS } = useQuery({
  queryKey: nk(currentProject, 'results', 'generators', resultSource, win.from, win.to),
  queryFn: () => resultsApi.getGeneratorResults(resultSource, win),
})
```

Both bounds go in the key, not just `from`: unlike the canvas's fixed-size
chunks, a tab window can change either end independently when the user edits the
filter.

**Client-side re-slicing stays exactly as it is.** Every `aggregateTS(ts, names,
range)` and `weightedSum(ts, cols, ctx, range)` call keeps its `range` argument
untouched. Once the payload is already the window, `resolveRange` against the
payload's own index returns the whole payload, so the second slice is an
identity operation. Leaving it in place means no arithmetic changes, and the
tabs keep working unchanged if a fetch ever returns more than requested.

## Part 3 — make `complete` bite

The 2026-08-04 work added `range?: {from,to,total,complete,capped}` to
`shared.tsx`'s canonical `TSPayload` precisely so this could be checked, and
nothing has checked it yet.

`AggregatedOverview` gains one guard before it computes any horizon total:

```ts
// This tab reports WHOLE-HORIZON energy and cost. If it is ever handed a
// windowed payload, its KPIs become window totals under horizon labels —
// silently. Fail loudly instead.
const partial = [gensTS, loadTS, storPowerTS].some(
  ts => ts?.range && !ts.range.complete,
)
```

When `partial` is true the tab renders an explicit "showing a partial payload —
totals unavailable" state rather than a number. Today the condition is
unreachable, since AggregatedOverview's getters are called with zero arguments and
receive no `range` key at all. It exists for the day someone converts it by
accident, which is the single most likely way this design gets broken later.

## Testing

**`defaultWindow` as a pure function.** Extract the choice into
`export function defaultWindow(count, periods, snapshots)` returning
`{selectedPeriod} | {fromIso, toIso} | null`, so it is unit-testable without
rendering `Results.tsx`. Cases: multi-period picks the first period; flat 26,280
picks a 720-row month; flat 8,760 returns `null` (whole); flat 168 returns
`null`; an empty snapshot list returns `null`.

**The identity property.** For each of the six tabs' reference series: fetching
window `[a,b]` and re-slicing client-side must equal fetching the whole series
and slicing to `[a,b]`. This is the same invariant the backend contract loop
asserts, checked one layer up, and it is what proves Part 2 changed no number.

**The guard.** Hand `AggregatedOverview` a payload carrying
`range.complete === false` and assert it renders the partial state rather than a
total. Name the production change it catches: converting AggregatedOverview's
getters to ranged calls.

**Key separation.** Assert that a windowed tab query key and
AggregatedOverview's unranged key are not equal for the same series — the
regression that would feed a window into the summary tab.

## What this does not do

- **No downsampling.** Unchanged from the previous design: 85 call sites compute
  totals by summing rows, so averaging would make every MWh and € figure depend
  on the zoom level.
- **AggregatedOverview is not converted.** It is the whole-horizon tab; that is
  the point of the split.
- **The duplicate fetch is not removed.** AggregatedOverview and Dispatch each
  download `generators` separately today. That is pre-existing, and collapsing
  it would remove exactly the key separation Part 3 depends on.
- **No change to the filter UI.** The chip, the inputs and Reset already exist
  and already communicate an active window.
