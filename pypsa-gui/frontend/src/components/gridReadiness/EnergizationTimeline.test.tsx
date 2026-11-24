import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { EnergizationTimelineView, shortfallSentences } from './EnergizationTimeline'
import type { EnergizationTimeline } from '../../api/flow'

vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts')
  return { ...actual, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }
})

const base: EnergizationTimeline = {
  unit: 'MW',
  basis: 'inputs',
  basis_note: 'Drawn from the inputs.',
  periods: [
    { period: '2027', start: '2027-01-01', end: '2030-01-01', demand_mw: 40, grid_mw: 15, grid_set: true,
      onsite: [{ kind: 'genset', mw: 20, units: ['genset_1'] }], onsite_mw: 20, shortfall_mw: 5 },
    { period: '2030', start: '2030-01-01', end: '2035-01-01', demand_mw: 50, grid_mw: 55, grid_set: true,
      onsite: [], onsite_mw: 0, shortfall_mw: 0 },
  ],
  time_to_power: { status: 'available', months: 14, first_power: '2028-03-01', stage: '2027' },
  stage_reason: null,
}

describe('EnergizationTimeline', () => {
  it('names the shortfall in words, per period', () => {
    expect(shortfallSentences(base)).toEqual([
      '2027: 5 MW of the peak demand has no firm supply (demand 40 MW, grid 15 MW, on-site 20 MW).',
    ])
  })

  it('says its basis, the shortfall and the first power date', () => {
    render(<EnergizationTimelineView data={base} />)
    expect(screen.getByTestId('energization-timeline-basis').textContent).toContain('inputs, no run')
    expect(screen.getByTestId('energization-timeline-shortfall').textContent).toContain('2027: 5')
    expect(screen.getByTestId('energization-timeline-ttp').textContent).toContain('2028-03-01')
    expect(screen.queryByTestId('energization-timeline-run')).toBeNull()
  })

  it('after a run shows served, shed and curtailed and says so', () => {
    const run = { ...base, basis: 'run' as const, periods: base.periods.map(p => ({
      ...p, run: { served_mwh: 1000, shed_mwh: 12, curtailed_mwh: 3 } })) }
    render(<EnergizationTimelineView data={run} />)
    expect(screen.getByTestId('energization-timeline-basis').textContent).toContain('Last run')
    expect(screen.getByTestId('energization-timeline-run').textContent).toContain('shed 12\u00A0MWh')
  })

  it('says when the run is out of date and keys power apart from energy', () => {
    const run = { ...base, basis: 'run' as const, run_state: 'out_of_date' as const,
      periods: base.periods.map(p => ({ ...p, run: { served_mwh: 1, shed_mwh: 0, curtailed_mwh: 0 } })) }
    render(<EnergizationTimelineView data={run} />)
    expect(screen.getByTestId('energization-timeline-basis').textContent).toContain('out of date')
    expect(screen.getByTestId('energization-timeline-run').textContent).toContain('MWh (not power)')
    expect(screen.getByTestId('energization-timeline-key').textContent).toContain('assumed profile')
  })

  it('reads a missing time to power as not available, never 0', () => {
    const none = { ...base, time_to_power: { status: 'not_available' as const, words: 'the project start date is not set' } }
    render(<EnergizationTimelineView data={none} />)
    expect(screen.getByTestId('energization-timeline-ttp').textContent).toContain('not available')
  })

  it('is mounted in both Guided (Site card) and Expert (Issues)', () => {
    for (const f of ['../../pages/hubDesign/cards/SiteCard.tsx', '../../pages/IssuesPanel.tsx']) {
      expect(readFileSync(new URL(f, import.meta.url), 'utf8')).toContain('<EnergizationTimeline />')
    }
  })
})
