// ui-modern 05: a section's heading separates the state from its summary with "·" (data), never an em dash.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowSection } from '../../api/flow'
import { GenericSectionCard, SectionView } from './SectionView'

const rows: Record<string, Partial<FlowSection>> = {
  assets: { state: 'complete', summary: '9 assets', items: [] },
  connection: { state: 'not_needed', summary: 'Off-grid: no grid connection needed', items: [] },
  commercial: { state: 'complete', summary: null, items: [] },
}
vi.mock('../../hooks/useProjectFlow', () => ({
  useProjectFlow: () => ({ isError: false, flow: null, section: (id: string) => rows[id] ?? null }),
}))
vi.mock('../../components/flowScope/NotInScopeGroup', () => ({ SectionNotInScope: () => null }))

afterEach(cleanup)

function heading(id: 'assets' | 'connection' | 'commercial'): string {
  render(<QueryClientProvider client={new QueryClient()}><SectionView id={id} /></QueryClientProvider>)
  return screen.getByTestId('section-summary').textContent ?? ''
}

describe('Guided section heading', () => {
  it('state · summary', () => { expect(heading('assets')).toBe('complete · 9 assets') })
  it('a summary with its own colon stays readable', () => {
    expect(heading('connection')).toBe('not needed · Off-grid: no grid connection needed')
  })
  it('no summary: the state alone', () => { expect(heading('commercial')).toBe('complete') })
  it('the card lists what the section holds: as many rows as the heading counts', () => {
    rows.assets.holds = Array.from({ length: 9 }, (_, i) => ({ component: 'Generator', entity: `g${i}`,
      carrier: null, field: 'p_nom', value: 1, unit: 'MW', extendable: false, series: false }))
    expect(heading('assets')).toBe('complete · 9 assets')
    render(<QueryClientProvider client={new QueryClient()}><GenericSectionCard id="assets" /></QueryClientProvider>)
    fireEvent.click(screen.getByTestId('section-held-show-all'))
    expect(screen.getAllByTestId('section-held')).toHaveLength(9)
    expect(screen.getByTestId('section-open-expert-assets').textContent).toBe('Open assets & sizing in Expert ›')
  })
  it('a section that waits only on its assumed defaults says so on the collapsed card', () => {
    const assumed = (n: number) => ({ code: `assumed_r${n}`, severity: 'warning' as const, source: 'template' as const,
      blocking: false, acknowledged: false, card: null, in_scope: true, message: `Row ${n}`,
      target: { section: 'connection' as const, panel: null, component: null, entity: `e${n}`, field: null } })
    rows.connection = { state: 'warning', summary: null, items: [assumed(1), assumed(2)] }
    heading('connection')
    expect(screen.getByTestId('assumptions-toggle').textContent).toBe('▸ Assumed · 2 not yet confirmed')
    expect(screen.getByTestId('assumptions-waiting').textContent).toContain('waits on these 2 defaults')
  })
})
