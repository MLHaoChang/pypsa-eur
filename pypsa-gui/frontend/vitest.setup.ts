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
import { afterEach, expect } from 'vitest'
import { cleanup } from '@testing-library/react'
import * as matchers from '@testing-library/jest-dom/matchers'

expect.extend(matchers)

afterEach(() => {
  cleanup()
})
