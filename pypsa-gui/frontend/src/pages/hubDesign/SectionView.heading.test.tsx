// ui-modern 05: a section's heading separates the state from its summary with "·" (data), never an em dash.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowSection } from '../../api/flow'
import { SectionView } from './SectionView'

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
})
