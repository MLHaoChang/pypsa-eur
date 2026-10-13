import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import ValueFlowsView from './ValueFlowsView'
import { TWO_WAY } from './valueFlows.test'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'
import * as shared from '../shared'

describe('ValueFlowsView', () => {
  it('shows the table with its sums and a Sankey of a two-way pair', () => {
    const { container } = render(<ValueFlowsView payload={TWO_WAY} sankeyWidth={600} />)
    expect(screen.getByTestId('vf-conservation').getAttribute('data-status')).toBe('ok')
    const dev = screen.getByTestId('vf-row-developer')
    expect(within(dev).getByText(/-800\.00|−800\.00/)).toBeTruthy()
    expect(screen.getByTestId('vf-stream-developer-dr_availability').textContent)
      .toContain('300.00')
    const svg = screen.getByTestId('vf-sankey').querySelector('svg')
    expect(svg).toBeTruthy()
    // Five nodes drawn (the pair dso ↔ developer is two payer and two payee
    // nodes, never a loop) and three links.
    const labels = [...svg!.querySelectorAll('text')].map(t => t.textContent)
    expect(labels.filter(l => l === 'Netz AG')).toHaveLength(2)
    expect(labels.filter(l => l === 'Developer')).toHaveLength(2)
    expect(labels).toContain('retailer')
    expect(svg!.querySelectorAll('path.recharts-sankey-link, .recharts-sankey-link path, path')
      .length).toBeGreaterThanOrEqual(3)
    expectAllButtonsNamed(container)
  })

  it('does not draw an incomplete ledger and names its flags', () => {
    const payload = structuredClone(TWO_WAY)
    payload.conservation_ok = null
    payload.flags = ['ledger_incomplete:2']
    payload.periods!._.conservation = { ok: null, checks: [
      { name: 'reconciliation', ok: null, detail: '2 line(s) unknown' }] }
    payload.periods!._.by_participant.dso.net = null
    render(<ValueFlowsView payload={payload} sankeyWidth={600} />)
    expect(screen.getByTestId('vf-conservation').textContent)
      .toContain('not established (reconciliation)')
    expect(screen.queryByTestId('vf-sankey')).toBeNull()
    expect(screen.getByTestId('vf-sankey-unavailable').textContent).toContain('ledger_incomplete:2')
    expect(within(screen.getByTestId('vf-row-dso')).getAllByText('unavailable').length).toBe(1)
  })

  it('names a failed check', () => {
    const payload = structuredClone(TWO_WAY)
    payload.periods!._.conservation = { ok: false, checks: [
      { name: 'coverage', ok: false, detail: ['bill:energy: …'] }] }
    render(<ValueFlowsView payload={payload} sankeyWidth={600} />)
    expect(screen.getByTestId('vf-conservation').textContent).toBe('Conservation failed: coverage')
  })

  it('exports the table as CSV and picks a period', () => {
    const spy = vi.spyOn(shared, 'downloadCSV').mockImplementation(() => {})
    const payload = structuredClone(TWO_WAY)
    payload.periods = { 2030: TWO_WAY.periods!._, 2040: structuredClone(TWO_WAY.periods!._) }
    payload.periods[2040].by_participant.developer.net = -900
    render(<ValueFlowsView payload={payload} sankeyWidth={600} />)
    fireEvent.change(screen.getByLabelText(/Period/), { target: { value: '2040' } })
    expect(within(screen.getByTestId('vf-row-developer')).getByText(/900\.00/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    expect(spy).toHaveBeenCalledWith('value_flows_2040.csv', expect.any(Array), expect.any(Array))
    spy.mockRestore()
  })
})
