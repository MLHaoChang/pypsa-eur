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
class MediaQueryListStub implements Partial<MediaQueryList> {
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
window.matchMedia = ((media: string) =>
  new MediaQueryListStub(media) as unknown as MediaQueryList) as typeof window.matchMedia

// window.localStorage is broken in this environment — NOT a plain jsdom gap
// (jsdom does implement Storage), but the practical effect is identical, so
// it gets the same fix-in-setup treatment. This Node runtime (v25) ships an
// experimental native `globalThis.localStorage` gated behind
// `--localstorage-file`; without a valid file path it still installs an
// object (hence `typeof localStorage === 'object'`, not 'undefined') but
// that object has no working methods (`typeof localStorage.getItem ===
// 'undefined'`), which is exactly the "provided without a valid path"
// warning vitest prints once per run. Confirmed by probe:
// `sameRef: localStorage === window.localStorage` is `true`, so this one
// broken object is shadowing jsdom's own Storage before jsdom ever gets to
// install it, on both bindings at once — so overriding `window.localStorage`
// via `Object.defineProperty` (a plain `=` assignment risks failing silently
// against a getter-only descriptor) fixes bare `localStorage` references
// too. ChatPanel.tsx reads three keys unconditionally on mount
// (`chat:promptHeight`, `chat:autoUncheckAfterSend`) and on first send
// (`chat:firstSendAck`) with no try/catch, so `TypeError: localStorage
// .getItem is not a function` was fatal before a single assertion ran — same
// pattern as the ResizeObserver / matchMedia stubs above.
// topologyLayoutStore.ts wraps every localStorage call in try/catch and its
// own test (topologyLayoutStore.test.tsx) deliberately asserts against the
// server write instead of localStorage for this exact reason (see that
// file's comment) — this stub makes those try/catch paths succeed instead
// of silently no-op, which is a strict improvement (closer to real browser
// behavior) and does not change what that test asserts. `afterEach` clears
// the store (alongside `cleanup()` above) so no key set by one test's
// mount effects (e.g. `chat:firstSendAck`) leaks into a later test in the
// same file. First needed by ChatPanel.test.tsx (Task 26).
class LocalStorageStub implements Storage {
  private store = new Map<string, string>()
  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value))
  }
  removeItem(key: string): void {
    this.store.delete(key)
  }
  clear(): void {
    this.store.clear()
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }
  get length(): number {
    return this.store.size
  }
}
Object.defineProperty(window, 'localStorage', {
  value: new LocalStorageStub(),
  writable: true,
  configurable: true,
})

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
