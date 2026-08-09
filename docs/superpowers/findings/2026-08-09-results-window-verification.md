# Verification of the Results-tabs window at real scale

**Date:** 2026-08-09
**Scope:** the six windowing Results tabs shipped by
`docs/superpowers/plans/2026-08-05-results-tabs-window.md`. That plan's own
final verification was typechecks + unit tests only, but its goal is a claim
about a **26,280-snapshot** network — "~470 MB and an unreadable 26,280-point
chart". Nothing had ever exercised that size. This closes that gap.
**Verdict: PASS.** 16/16 checks, plus a mutation test proving the checks
discriminate.

**Driver:** `pypsa-gui/backend/smoke/verify_results_window.py`. Run directly
(no server needed):

```
.pixi/envs/test/bin/python -u pypsa-gui/backend/smoke/verify_results_window.py
```

Backend Python must be `.pixi/envs/test/bin/python` — a bare `python`
resolves the `default` env, which has no `pywebview`.

## Method, and its one deliberate limit

The driver calls the router functions in-process (the same objects FastAPI
serves) against three networks sized past `WINDOW_THRESHOLD`.

**Dispatch is synthesised, not solved.** Windowing is a pure serialisation
concern — `_serve_ts` → `slice_ts` → `_ts_payload` never consults the
optimiser, only the shape of `generators_t.p`. A real 2-million-variable LP
would take minutes and verify nothing extra about the slice path. The
fixtures therefore carry dispatch that is internally consistent with their
topology, which is exactly what `_dispatch_ready` gates on. Cell values are
`row*1000 + col` so a mis-sliced window cannot coincidentally equal the
right one.

**The window bounds are read out of the frontend source**, not hardcoded:
the driver greps `WINDOW_THRESHOLD` and `DEFAULT_FLAT_WINDOW` out of
`filterContext.tsx`. Had it hardcoded 720, it would keep passing after
someone changed the constant while measuring a window no tab requests.

## Case A — the plan's motivating case (26,280 flat hourly, 12 generators)

| # | Check | Observed |
|---|---|---|
| A1 | Unranged payload carries **no** `range` key | `keys=[columns, data, index]` |
| A2 | Unranged serves the whole horizon | 26,280 rows |
| A3 | Windowed payload serves exactly the window | 720 rows |
| A4 | `range` metadata is honest | `{from:0, to:719, total:26280, complete:false, capped:false}` |
| A5 | **Server slice == client-side slice** | 720 rows × 12 cols identical |
| A6 | Window is a large payload reduction | **4.3 MB → 0.103 MB (42× smaller)** |
| A7 | `complete` would trip AggregatedOverview's guard | windowed `complete=false`; unranged emits no `range` at all |

A5 is the identity property asserted live rather than by proxy. A1/A7
together are why `isPartialPayload` stays false for the summary tab: its
unranged getters produce a payload with no `range` key at all, so the guard
cannot fire on the normal path — and *would* fire the moment someone
converts them.

On A6: the absolute 470 MB figure depends on the model's column count (12
generators here gives 4.3 MB). The **ratio** is what generalises — 26,280 →
720 rows is 36.5×, and 42× measured including index overhead.

## Case B — `fetchRange: undefined` is load-bearing, not a simplification

`useResultsWindow` returns `undefined` rather than `{0, count-1}` for a
whole-horizon window, on the stated grounds that only the *ranged* path
applies `MAX_RESPONSE_VALUES`. That is the subtlest decision in the feature
and it now has direct evidence. Fixture: 12,000 snapshots × 170 generators =
2,040,000 values, against a cap of 2,000,000.

| # | Check | Observed |
|---|---|---|
| B1 | Unranged is **not** capped | 12,000 of 12,000 rows, no `range` key |
| B2 | The same window as **explicit** bounds **is** capped | 11,764 of 12,000 rows, `capped=true` |
| B3 | ⇒ `undefined` avoids a real truncation | 12,000 rows vs 11,764 |

Passing `{0, count-1}` would round-trip the same numbers on small networks
and **silently drop 236 rows** here. The comment in `useResultsWindow` is
correct and should not be "simplified".

## Case C — long multi-period opens on its first period

3 periods × 8,760 = 26,280 snapshots.

| # | Check | Observed |
|---|---|---|
| C1 | Period window serves exactly one period | 8,760 rows |
| C2 | It equals the **first** period's rows | 8,760 rows identical |
| C3 | `range` reports the multi-period total | `{from:0, to:8759, total:26280, complete:false}` |

## Mutation test — the checks discriminate

Changing the requested window from `(0, 719)` to `(1, 720)`:

- **A4 FAILED** (`range` reports `from:1, to:720`)
- **A5 FAILED** (content no longer matches the client-side slice)
- **A3 still PASSED** — 720 rows either way

A3 passing under the mutation is the point: a row-count check alone would
have missed an off-by-one entirely. A5 is what earns its place.

## Scope notes

- Frontend `defaultWindow` is covered at this size by the existing
  `filterContext.test.ts` (`stamps(26280)` → `{iso, s[0], s[719]}`); this
  driver verifies the *backend serves those bounds* and what that saves.
- The cross-endpoint slice-equality property across all sixteen endpoints
  is already asserted by
  `tests/test_results_range.py::test_a_server_slice_equals_the_same_slice_taken_client_side`
  against a really-solved network. This driver adds scale, not breadth.
