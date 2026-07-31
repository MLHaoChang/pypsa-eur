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
