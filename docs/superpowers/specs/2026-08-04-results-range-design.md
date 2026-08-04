# Results range — design

**Date:** 2026-08-04
**Status:** approved, ready for planning
**Scope:** a positional snapshot range on the 16 `/results/*` endpoints that return per-snapshot series, and adaptive chunked fetching on the client.

## Problem

No `/results/*` endpoint accepts a snapshot range. The only query parameter
anywhere on the surface is `source: str = "lopf"`, declared as a bare default;
`grep -n "Query(" routers/results.py` returns nothing at all, so there is not
even a validated-parameter idiom in the file to follow. Every call serialises
the full horizon for every asset.

Measured, not estimated — a synthetic frame of the exact shape `ts_payload`
emits, serialised with `json.dumps`:

| assets | snapshots | values | payload |
|---|---|---|---|
| 20 | 168 | 3,360 | 0.03 MB |
| 50 | 8,760 | 438,000 | 4.6 MB |
| 200 | 8,760 | 1,752,000 | 17.5 MB |
| 200 | 26,280 | 5,256,000 | **52.6 MB** |

That is **one endpoint**. `CanvasResultsContext.tsx` fetches **nine** of them —
`generators`, `loads`, `lines`, `links`, `line_reactive`, `storage_dispatch`,
`store_dispatch`, `storage`, `store_energy` — roughly **470 MB** on a 200-asset,
3-period network, and reads **one row** from each, at `resultsSnapshotIdx`, to
build the `byBus` / `byLine` / `byLink` maps the canvas overlay renders.

This is the ceiling on network size. It is not a rendering problem: a
26,280-point line chart is already unusable, so the Results tabs already window
client-side via the Horizon filter. The window exists; it is simply applied
after the bytes have crossed the wire.

## The constraint that shapes everything

**85 call sites across 5 files compute totals client-side.**
`shared.tsx::weightedSum` walks every row of the payload, multiplies by that
row's snapshot weighting, and returns MWh or €. `perPeriodWeightedSum`,
`weightedSumSplit`, `aggregateTS` and friends do the same.

So any change that alters *which values the client receives* alters every
energy and cost figure in the UI.

**Consequence: no downsampling.** Averaging 24 hourly values into one daily
point and applying a daily weight is not the sum of 24 hourly values times
their hourly weights. A downsampled payload would make every total an
approximation whose error depends on the zoom level the user happened to pick —
the exact defect class the 2026-08-01 trustworthy-numbers work removed from the
economic surfaces. Downsampling is out of scope, deliberately, not as an
oversight.

**Range slicing is safe for the same reason downsampling is not.** The tabs
already pass `rowRange` to `weightedSum`; the Horizon filter already windows.
This moves an existing window from after the download to before it. Same rows,
same weights, same number. With no range supplied the response is byte-identical
to today's.

## The API

```
GET /results/<series>?from=<int>&to=<int>[&source=lopf|ac_pf]
```

Both bounds optional and **inclusive**. Omitted means the full series.

### Addressing is positional, not by timestamp

`from`/`to` are positions in the snapshot index, not timestamps. Two reasons:

1. The frontend is already positional throughout — `resultsSnapshotIdx`,
   `rowRange {from, to}`, `SnapshotPicker`'s `activeRange {start, end}`. No
   translation layer is needed.
2. Multi-period networks replicate one operational year under every investment
   period, so a bare timestamp matches N rows. CLAUDE.md documents this trap
   against the Horizon filter, which needs a year-remap to cope. Positional
   addressing has no such ambiguity, and `df.iloc[from:to+1]` behaves identically
   on a flat DatetimeIndex and a `(period, timestep)` MultiIndex.

### Clamping mirrors the client

- `from < 0` → `0`
- `to >= len(df)` → `len(df) - 1`
- `to` omitted → `len(df) - 1`; `from` omitted → `0`

This matches `weightedSum`'s own `Math.max(0, Math.min(...))`, so client and
server agree by construction rather than by coincidence.

`from > to` returns an **empty data array**, not a 400. The client already
treats an inverted range as empty (`if (rFrom > rTo) return 0`), and a 400 would
turn a benign transient UI state into an error toast. The response's `range`
block makes the emptiness self-describing, so nothing is silent.

Non-integer input falls through to FastAPI's own 422.

### The response echoes what it served

Every ranged response carries a `range` block:

```json
{
  "index": ["2030-01-01T00:00:00", "..."],
  "columns": ["Gas_B2", "PV_B3", "..."],
  "data": [[512.0, 0.0], ["..."]],
  "range": { "from": 0, "to": 167, "total": 26280, "complete": false, "capped": false }
}
```

| field | meaning |
|---|---|
| `from`, `to` | the inclusive bounds actually served, after clamping |
| `total` | full length of the snapshot index, regardless of the slice |
| `complete` | `from == 0 and to == total - 1` |
| `capped` | the server reduced `to` to satisfy its own row cap |

**`complete` is the load-bearing field.** Without it a windowed payload is
indistinguishable from a whole-horizon one, and `weightedSum` would report a
year's energy from a week's data with no way to tell. A consumer that renders a
horizon total must check it. This is the same rule the economics surfaces
follow: a number you cannot back must say so rather than look confident.

`periods` continues to appear alongside `index` on multi-period networks,
unchanged.

**`/unit_commitment` is the one composite.** It returns
`{generators, status_grid, n_committable}`, where only `status_grid` carries
`index`/`columns`/`data`. Its `range` block belongs **inside `status_grid`**,
beside the fields it describes — a consumer reading `status_grid.index` reads
`status_grid.range`. The sibling `generators` array is per-asset, not
per-snapshot, and is unaffected by slicing.

### Server-side row cap

A backstop against a request that would build a response nobody can use:

```python
MAX_RESPONSE_VALUES = 2_000_000   # ~20 MB at the measured ~10 bytes/value
```

When `(to - from + 1) * len(df.columns)` exceeds it, `to` is reduced to fit and
`capped: true` is set. Truncating silently would be the defect this whole design
exists to avoid; the echo is what makes the cap honest.

The cap never applies when no range is requested — that path stays exactly as it
is today, so no existing consumer changes behaviour.

## Backend structure

### `services/serialization.py`

A new pure function beside the existing `ts_payload`:

```python
def slice_ts(
    df: pd.DataFrame,
    from_: int | None,
    to_: int | None,
    *,
    max_values: int = MAX_RESPONSE_VALUES,
) -> tuple[pd.DataFrame, dict]:
    """Positionally slice a time-series frame; return (sliced, range_meta)."""
```

It imports no FastAPI and touches no request object, so it is directly
unit-testable — the same discipline `app_paths.py` and `local_settings.py`
follow.

`ts_payload` gains one keyword:

```python
def ts_payload(df, *, extra: dict | None = None, range_meta: dict | None = None) -> dict:
```

When `range_meta` is supplied it is emitted as the `range` key. Omitted, the
payload is byte-identical to today's — which is what keeps the twelve aggregate
endpoints and every untouched consumer working.

### `routers/results.py`

`_serve_ts` grows two pass-through parameters:

```python
def _serve_ts(accessor, attr, source, *, from_=None, to_=None, echo_source=False):
```

It calls `slice_ts` between `_result_df` and `ts_payload`. That single edit
covers the twelve endpoints already routed through it.

Each of the sixteen series endpoints declares the query parameters:

```python
@results_router.get("/generators")
def get_generator_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from"),
    to_: int | None = Query(None, alias="to"),
):
    return _serve_ts("generators_t", "p", source, from_=from_, to_=to_)
```

`alias="from"` is required — `from` is a Python keyword.

**The sixteen series endpoints** (classified by payload shape, not by name):
`/generators`, `/storage_dispatch`, `/store_dispatch`, `/store_energy`,
`/storage`, `/lines`, `/links`, `/transformers`, `/unit_commitment`,
`/voltages`, `/line_reactive`, `/transformer_reactive`, `/prices`,
`/curtailment`, `/lost_load`, `/loads`.

**Eleven** route through `_serve_ts`: `/generators`, `/storage_dispatch`,
`/store_dispatch`, `/store_energy`, `/storage`, `/lines`, `/links`,
`/transformers`, `/voltages`, `/line_reactive`, `/transformer_reactive`.

**Five** have bespoke bodies and call `slice_ts` directly before building their
payload: `/unit_commitment`, `/prices`, `/curtailment`, `/lost_load`, `/loads`.

Three of the sixteen take no `source` parameter at all — `/unit_commitment`,
`/curtailment` and `/lost_load` read LP-only artefacts, so there is no AC-PF
variant to select. Their signatures gain `from`/`to` only.

**The twelve aggregate endpoints are untouched**: `/cost_breakdown`,
`/objective_decomposition`, `/economics_by_carrier`, `/statistics`, `/lcoh`,
`/ac_pf/status`, `/losses`, `/carrier_kpis`, `/emissions`, `/line_duals`,
`/price_drivers`, `/asset_economics`. A snapshot range is meaningless for a
scalar or a per-asset aggregate, and adding an ignored parameter would be a
worse lie than not having one.

## Frontend structure

### `src/pages/results/chunking.ts` (new)

```ts
export const CHUNK_STEPS = [24, 168, 720, 8760] as const   // day, week, month, year
export const TARGET_CHUNK_BYTES = 512_000
export const BYTES_PER_VALUE = 10   // measured: 5,256,000 values -> 52.6 MB

export function chooseChunk(assetCount: number, totalSnapshots: number): number {
  if (assetCount <= 0) return 168
  const ideal = TARGET_CHUNK_BYTES / (assetCount * BYTES_PER_VALUE)
  const fit = [...CHUNK_STEPS].reverse().find(s => s <= ideal) ?? CHUNK_STEPS[0]
  return Math.min(fit, Math.max(1, totalSnapshots))
}

export function chunkBounds(idx: number, chunk: number, total: number, clampTo?: { start: number; end: number }) {
  const lo = clampTo?.start ?? 0
  const hi = clampTo?.end ?? total - 1
  const from = lo + Math.floor((idx - lo) / chunk) * chunk
  return { from: Math.max(lo, from), to: Math.min(from + chunk - 1, hi) }
}
```

**Why bytes rather than a fixed window.** 168 snapshots of 20 assets is a
wastefully small request; 168 of 2000 assets is too big. Deriving the chunk from
asset count self-selects:

| assets | ideal | chunk | payload |
|---|---|---|---|
| 20 | 2560 | 720 (month) | 0.14 MB |
| 50 | 1024 | 720 (month) | 0.36 MB |
| 200 | 256 | 168 (week) | 0.34 MB |
| 2000 | 25 | 24 (day) | 0.48 MB |

At 200 assets it independently lands on 168 — a week — which is the value a
human would have picked. At the other sizes it does not, which is the argument
for computing it.

**The floor is a day, and above ~2100 assets the target is exceeded.**
`CHUNK_STEPS[0]` is 24, so a 5000-asset network gets a 1.2 MB chunk rather than
0.5 MB. This is deliberate: a sub-day chunk would fetch a fragment of a diurnal
cycle, which is the unit every dispatch pattern in this domain is built on, and
the server-side row cap already backstops the pathological case. If networks
that wide become common, the answer is column selection, not a shorter window.

**Steps are calendar units on purpose.** Snapping to day / week / month / year
keeps boundaries meaningful, makes cache keys legible when debugging, and makes
"this month" exactly one request instead of one and a bit.

**Alignment is what makes caching work.** `chunkBounds` floors to a chunk
boundary, so the request key depends only on which chunk the cursor occupies.
Scrubbing or playing within a chunk issues zero requests; crossing a boundary
issues exactly one; scrubbing back is a cache hit.

**Stability beats optimality.** The chunk is computed once per (project,
endpoint) from `columns.length`, which is fixed for a solved network. Recomputing
it as data changed would shift cache keys underneath the user and destroy the
hit rate that makes playback smooth.

**`activeRange` is a clamp, not the window.** `SnapshotPicker`'s `activeRange`
is the whole snapshot range on a flat network, so using it *as* the window would
achieve nothing there. Passing it as `clampTo` stops a chunk from crossing an
investment-period boundary on multi-period networks, so the canvas never pulls
rows from a period the user is not looking at.

### Learning the asset count

The client cannot choose a chunk before it knows `columns.length`. It probes
with `?from=0&to=0`: one row, but the response carries the full `columns` array
and `range.total`. A few KB, and it reuses the parameter being added rather than
requiring a new endpoint.

### `src/api/simulation.ts`

The existing `srcParam` helper becomes range-aware:

```ts
const tsParams = (source?: ResultSource, range?: { from: number; to: number }) => {
  const params: Record<string, string | number> = {}
  if (source) params.source = source
  if (range) { params.from = range.from; params.to = range.to }
  return Object.keys(params).length ? { params } : undefined
}
```

Every series getter takes an optional `range` argument. Omitting it preserves
today's call signature and today's behaviour.

### `src/components/CanvasResultsContext.tsx`

The nine query keys gain the chunk start:

```ts
queryKey: nk(currentProject, 'results', 'generators', resultSource, bounds.from)
```

and each `queryFn` passes `bounds`. Row lookup changes from `ts.data[idx]` to
`ts.data[idx - ts.range.from]`.

**This is the single biggest win**: ~470 MB to ~3.1 MB on a 200-asset,
3-period network.

`line_reactive` is in this set and is an AC-PF series, so the chunk bounds must
be computed per endpoint from that endpoint's own `columns.length` — the
reactive frame has a different width from `generators`, and sharing one chunk
size across all nine would size every request by whichever endpoint was probed
first.

### Results tabs

Out of scope for the first iteration. They already window client-side and
already produce correct totals; converting them is a follow-up that must be done
one tab at a time with the `complete` flag wired into anything that renders a
horizon total. Doing it here would put 85 total call sites in the blast radius
of a payload change, which is exactly what this design is trying to avoid.

## Testing

**`slice_ts` units.** Clamping at both ends; `to` beyond the end; omitted
bounds; inverted range yields empty with correct meta; MultiIndex slices
positionally and preserves `periods` alignment; the row cap trips and sets
`capped`; an empty input frame does not raise.

**Contract loop over all sixteen series endpoints**, in the style of the
`SURFACES` coverage matrix from the trustworthy-numbers work: each accepts
`from`/`to`, each echoes a `range` block, and each returns exactly the requested
number of rows. The endpoint list lives in one constant so a seventeenth series
endpoint added later fails the test until it is classified.

**The invariant that matters most.** On a solved golden network, for each series
endpoint: the rows returned by a server-side slice must equal, value for value,
the same slice taken client-side from the unranged payload. This is what pins
"moving the window server-side changed no number", and it is the test that would
catch an off-by-one in the inclusive bounds.

**`complete` correctness.** It is computed from what was **served**, not from
what was **asked**: `from == 0 and to == total - 1` after clamping. So a request
with `to=99999` against a 168-row series returns `complete: true`, because the
caller does hold every row. Reporting `false` there would make a consumer refuse
to compute a horizon total on data that is in fact whole — the mirror image of
the defect this field exists to prevent.

**Payload size.** A one-row request on a wide network stays under a stated
kilobyte bound — the regression guard for the canvas win.

**`chooseChunk` / `chunkBounds` units.** The four asset-count cases in the table
above; alignment (two indices in the same chunk produce identical bounds);
boundary crossing produces adjacent non-overlapping chunks; `clampTo` prevents
crossing a period edge; `totalSnapshots` shorter than a step clamps down.

## What this does not do

- No downsampling, at any resolution. See "The constraint that shapes
  everything".
- No pagination over assets or columns. A network with 2000 assets and a
  one-day chunk is a 0.48 MB response; column paging would add a second
  dimension of partialness for no measured benefit.
- No change to the twelve aggregate endpoints.
- No conversion of the Results tabs. The canvas is the demonstrated win; the
  tabs are a follow-up with a wider blast radius.
- No caching layer on the backend. Slicing a DataFrame already in memory is
  cheap; the expensive part was serialisation, and that is what shrinks.
