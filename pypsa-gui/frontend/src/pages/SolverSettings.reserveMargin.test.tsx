// The reserve-margin entry field, beside the ENS target (Phase 8 spec §6).
//
// The section is rendered DIRECTLY rather than through the whole SolverSettings
// page: `ReliabilityAssumptions` is a pure `(draft, patch)` component with no
// hooks of its own, so mounting it needs no query client, no router and no
// network — and a test that had to mount the page to check a caveat sentence
// would be a test nobody keeps.
//
// ★ The caveat is the load-bearing assertion here. A margin field with no
// caveat sells the number as a reliability result: the LP meets it by
// arithmetic on derating factors the user mostly did not enter, and nothing in
// this path samples an outage. The sentence is what stops "margin met" being
// read as "target met".
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { SolverConfig } from '../api/types'
import { ReliabilityAssumptions } from './SolverSettings'

const DRAFT = {
  voll: 3000,
  ens_cap_permyriad: 1,
  ens_zone_cap_multiple: 3,
  reserve_margin: 0.15,
  dsr_price_eur_per_mwh: 0,
  dsr_share_of_load: 0,
  dsr_buses: [],
} as unknown as SolverConfig

function field(label: RegExp): HTMLInputElement {
  const span = screen.getByText(label)
  const input = span.closest('label')?.querySelector('input')
  if (!input) throw new Error(`no input under label ${label}`)
  return input as HTMLInputElement
}

function renderSection(patch = vi.fn(), draft: SolverConfig = DRAFT) {
  render(<ReliabilityAssumptions draft={draft} patch={patch} />)
  return patch
}

describe('Solver settings — the reserve-margin field', () => {
  it('renders beside the ENS target, showing the configured value', () => {
    renderSection()
    expect(field(/ENS target/)).toBeTruthy()
    const input = field(/Reserve margin/i)
    expect(input.value).toBe('0.15')
  })

  it('is bounded like its neighbours — the schema is ge=0, le=5', () => {
    renderSection()
    const input = field(/Reserve margin/i)
    expect(input.getAttribute('min')).toBe('0')
    expect(input.getAttribute('max')).toBe('5')
  })

  it('clamps out-of-range entries instead of sending a 422', () => {
    const patch = renderSection()
    const input = field(/Reserve margin/i)
    fireEvent.change(input, { target: { value: '9' } })
    expect(patch).toHaveBeenCalledWith({ reserve_margin: 5 })
    patch.mockClear()
    fireEvent.change(input, { target: { value: '-1' } })
    expect(patch).toHaveBeenCalledWith({ reserve_margin: null })
  })

  it('sends null (off), not 0, when the field is cleared — the same "0 = off" '
    + 'convention the ENS target uses', () => {
    const patch = renderSection()
    fireEvent.change(field(/Reserve margin/i), { target: { value: '0' } })
    expect(patch).toHaveBeenCalledWith({ reserve_margin: null })
  })

  it('★ states that a met margin is NOT a met reliability target', () => {
    renderSection()
    const caveat = screen.getByTestId('reserve-margin-caveat-setting')
    const s = caveat.textContent ?? ''
    expect(s).toMatch(/not a met reliability target/i)
    expect(s).toMatch(/proxy/i)
    expect(s).toMatch(/convention/i)
    expect(s).toMatch(/derating/i)
    expect(s).toMatch(/sampler|Monte.?Carlo/i)
  })
})
