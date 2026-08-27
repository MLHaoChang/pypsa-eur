// The block-level "unavailable" message, centralised — one layer out from
// UnavailableCell.
//
// `shared.tsx` already made this argument once for the CELL: the word was
// centralised but its PRESENTATION was not, so 19 verbatim copies of a span
// could drift on any single edit with nothing to catch it. That comment names
// the block-level fallback as deliberately NOT the cell's job — a `<p>` where
// a cell would be wrong — and it was right to exclude it. Excluded from the
// cell is not the same as exempt from the argument: CompareView carried six
// verbatim copies of the block, which is the same drift surface one layer out.
//
// The source scan is the load-bearing assertion. A render test proves the
// component works; only reading CompareView's own text proves nobody
// reintroduced the literal it replaced. Vite's `?raw` gives that without
// pulling `node:fs` into the browser-target build (there are no @types/node
// here, so a `readFileSync` version does not typecheck).
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import compareViewSource from '../CompareView.tsx?raw'
import { COST_UNAVAILABLE, UnavailableBlock } from './shared'

// The exact literal the six sites used, matched loosely on whitespace so a
// reformat cannot smuggle a copy back past this test.
const INLINE_BLOCK = /<p\s+className="text-\[11px\] text-muted py-2">\{COST_UNAVAILABLE\}<\/p>/g

describe('UnavailableBlock', () => {
  it('renders the shared word as a block, not a cell', () => {
    // `toBeInTheDocument` needs jest-dom, which this path does not set up;
    // asserting the tag is stronger here anyway — the whole reason this is a
    // separate component from UnavailableCell is that it must be block-level.
    render(<UnavailableBlock />)
    expect(screen.getByText(COST_UNAVAILABLE).tagName).toBe('P')
  })

  it('CompareView contains no verbatim inline copy of the block', () => {
    const copies = compareViewSource.match(INLINE_BLOCK) ?? []
    expect(
      copies.length,
      `${copies.length} inline copies remain — use <UnavailableBlock /> so the ` +
        `copy and its classes cannot drift per site`,
    ).toBe(0)
  })

  it('the scan can actually see CompareView (guard on the guard)', () => {
    // If the ?raw import silently resolved to an empty string, the assertion
    // above would pass while checking nothing.
    expect(compareViewSource.length).toBeGreaterThan(1000)
    expect(compareViewSource).toContain('COST_UNAVAILABLE')
  })
})
