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
  localStorage.clear()
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

// jsdom does not implement window.matchMedia. `useIsCoarsePointer`
// (src/hooks/useIsCoarsePointer.ts) calls `window.matchMedia('(pointer:
// coarse)')` unconditionally — with no `typeof window.matchMedia ===
// 'function'` guard, unlike ProjectsHomePage.tsx's prefers-reduced-motion
// check — from a useState initializer AND a mount effect. ChatPanel.tsx
// calls that hook unconditionally near the top of its render (used to pick
// the touch-device send affordance), so every test that mounts ChatPanel
// throws `TypeError: window.matchMedia is not a function` before a single
// assertion runs, the same fatal-passive-effect/unmount pattern the
// ResizeObserver stub above documents. `matches: false` is a deliberate
// choice, not a placeholder: it selects the mouse/trackpad (non-coarse)
// pointer path, which is the correct default for a jsdom test environment
// that has no real pointer at all. `addEventListener`/`removeEventListener`
// are stubbed as no-ops because nothing in jsdom ever fires a `change`
// event on a media query, mirroring the ResizeObserver callback never
// firing either. First needed by ChatPanel.test.tsx (Task 26).
class MediaQueryListStub implements MediaQueryList {
  matches = false
  media: string
  onchange = null
  constructor(media: string) {
    this.media = media
  }
  addEventListener() {}
  removeEventListener() {}
  addListener() {}
  removeListener() {}
  dispatchEvent() {
    return false
  }
}
window.matchMedia = (media: string) => new MediaQueryListStub(media)

// jsdom does not implement Element.scrollIntoView (it does no layout at
// all, so "scroll to this element" has nothing to compute). ChatPanel.tsx's
// auto-scroll-to-latest-message effect calls
// `messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block:
// 'end' })` unconditionally whenever `stickToBottom` is true (the default),
// which fires on first mount — so every test that mounts ChatPanel throws
// `TypeError: messagesEndRef.current?.scrollIntoView is not a function` in
// a passive effect, fatal to the tree exactly like the ResizeObserver /
// matchMedia / localStorage gaps above. A no-op is sufficient: the test
// only needs the call not to throw, not to actually move a viewport that
// jsdom doesn't render.
Element.prototype.scrollIntoView = () => {}

// Node ≥22 defines its own experimental `globalThis.localStorage` (behind
// `--experimental-webstorage`, on by default on the Node 25 this repo's pixi
// env ships). Vitest's jsdom environment only overrides a global name if it
// appears in its own KEYS whitelist — `localStorage`/`sessionStorage` aren't
// on it — so when the name already exists on `globalThis` (as it now does,
// natively), vitest leaves Node's version in place instead of swapping in
// jsdom's. Node's own instance here is an inert stub (`{}`, no
// getItem/setItem/clear), so any test that touches `localStorage` either
// throws in `beforeEach` (`localStorage.clear is not a function`) or every
// read/write silently no-ops. jsdom's real Storage still exists — it's just
// shadowed — reachable via the `jsdom` handle vitest's environment stashes on
// `globalThis`. Force it into place before any test runs.
const jsdomWindow = (globalThis as { jsdom?: { window?: Window } }).jsdom?.window
if (jsdomWindow && typeof jsdomWindow.localStorage?.clear === 'function') {
  Object.defineProperty(globalThis, 'localStorage', {
    value: jsdomWindow.localStorage,
    configurable: true,
    writable: true,
  })
  Object.defineProperty(globalThis, 'sessionStorage', {
    value: jsdomWindow.sessionStorage,
    configurable: true,
    writable: true,
  })
}
