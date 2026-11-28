import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import CampusHarmonicEmissionSection, { backgroundText } from './CampusHarmonicEmissionSection'
import type { HarmonicEmission } from '../api/campusElectrical'

const rows = [
  { level: 'poc' as const, poc: 'B1', label: 'With background', thd_pct: 1.2, thd_limit_pct: 3, verdict: 'pass' as const },
  { level: 'unit' as const, unit: 'EL1', thd_pct: 4, thd_limit_pct: 3, verdict: 'fail' as const },
  { level: 'unit' as const, unit: 'EL2', verdict: 'unknown' as const, note: 'no spectrum for EL2' },
]

describe('CampusHarmonicEmissionSection', () => {
  it('says so for a run without the screen and for an error', () => {
    const { rerender } = render(<CampusHarmonicEmissionSection emission={null} />)
    expect(screen.getByTestId('emission-not-computed')).toBeTruthy()
    rerender(<CampusHarmonicEmissionSection emission={{ error: 'boom' }} />)
    expect(screen.getByTestId('emission-error').textContent).toContain('boom')
  })
  it('counts the verdicts, keys the chips and gives a reason per not-judged row', () => {
    const e: HarmonicEmission = { background: { status: 'none' }, label: 'Screening', rows }
    render(<CampusHarmonicEmissionSection emission={e} />)
    expect(screen.getByText('1 pass · 1 fail · 1 not judged')).toBeTruthy()
    expect(screen.getByTestId('emission-reason').textContent).toBe('no spectrum for EL2')
    expect(screen.getByTestId('emission-key')).toBeTruthy()
    expect(screen.getByTestId('emission-no-background')).toBeTruthy()
  })
  it('names the background in words', () => {
    expect(backgroundText({ status: 'none' })).toBe('Background: none.')
    expect(backgroundText({ status: 'used', citation: 'utility measurement of 2026-10-03' }))
      .toBe('Background: utility measurement of 2026-10-03, used.')
    expect(backgroundText({ status: 'unusable', reason: 'it was measured at another bus' }))
      .toBe('Background not used: it was measured at another bus.')
  })
})
