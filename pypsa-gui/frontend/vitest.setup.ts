// Registers @testing-library/react's per-test DOM cleanup explicitly.
//
// @testing-library/react only self-registers `afterEach(cleanup)` when it
// finds a global `afterEach` (see its dist source: `typeof afterEach ===
// 'function'`). This suite runs with `globals: false` (see vite.config.ts),
// so that global is never defined and the library's auto-cleanup silently
// never ran: every `render()` call from every `it()` block in a file stayed
// mounted in `document.body`, so later `screen` queries in the same file
// could match stale nodes left by earlier tests. Dialog.test.tsx was the
// first file to call `render()` more than once per file and is what
// surfaced this.
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})

// jsdom does not implement ResizeObserver. recharts' `ResponsiveContainer`
// constructs one unconditionally in a passive effect on mount
// (`new ResizeObserver(callback)` — see
// node_modules/recharts/es6/component/ResponsiveContainer.js), so every
// results-tab test that renders a chart (LoadFlow, LostLoadTab,
// AggregatedOverview, StorageCycling, DispatchStack, Dispatch, Prices,
// shared.tsx, Economics, Curtailment, CapacityExpansion all use
// `ResponsiveContainer`) throws an uncaught `ReferenceError` before a single
// assertion runs. React treats the throw in that passive effect as fatal and
// unmounts the whole tree, so `screen`/`container` queries see an empty
// `<body>`. A minimal observe/unobserve/disconnect stub is enough to satisfy
// the constructor call; the callback itself is never invoked because nothing
// in jsdom ever fires a resize.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver = ResizeObserverStub

// The stub above is necessary but not sufficient. `ResponsiveContainer` also
// sizes itself, on the same mount effect, from
// `containerRef.current.getBoundingClientRect()` *before* any ResizeObserver
// callback would ever fire — so the callback existing and doing nothing is
// fine, but the initial rect it reads still has to be non-zero. jsdom does
// not run layout, so its default `getBoundingClientRect` always returns 0
// for every dimension on every element. recharts treats width/height of 0 as
// "not yet measured" and renders the chart's children as `null`
// (`validateWidthHeight` in generateCategoricalChart.js) rather than
// throwing — so a test would stop crashing and start passing while
// asserting against an empty chart, which is a check that passes for the
// wrong reason. Confirmed against this codebase directly: with the stub
// above but without this override, mounting AggregatedOverview's carrier
// chart produces a `.recharts-responsive-container` wrapper but zero
// `svg.recharts-surface` and zero `.recharts-bar-rectangle` nodes; adding
// this override brings those counts to non-zero. See
// task-16b-report.md for the verbatim before/after.
Element.prototype.getBoundingClientRect = () => ({
  width: 500,
  height: 500,
  top: 0,
  left: 0,
  right: 500,
  bottom: 500,
  x: 0,
  y: 0,
  toJSON() {},
})
