import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import MetricChecklist from './MetricChecklist'
import type { MetricRow } from './types'

const METRICS: MetricRow[] = [
  { id: 'p', label: 'Active power', unit: 'MW', kind: 'series', origin: 'output', status: 'ok' },
  { id: 'energy_mwh', label: 'Energy', unit: 'MWh', kind: 'scalar', origin: 'derived',
    status: 'ok', formula: 'Σ p × w' },
  { id: 'status', label: 'Committed', unit: '', kind: 'series', origin: 'output',
    status: 'blocked', reason: 'unit commitment is not enabled on Gas 1',
    remedy: { action: 'open_properties', label: 'Enable committable' } },
  { id: 'loading', label: 'Loading', unit: '%', kind: 'series', origin: 'derived',
    status: 'na', reason: 'Generator is not a branch component', formula: '|p0| ÷ s_nom_opt' },
]

const setup = (over = {}) => {
  const onToggle = vi.fn(); const onRemedy = vi.fn()
  render(<MetricChecklist metrics={METRICS} selected={['p']}
    onToggle={onToggle} onRemedy={onRemedy} {...over} />)
  return { onToggle, onRemedy }
}

describe('MetricChecklist', () => {
  it('splits scalars and series into two labelled zones', () => {
    setup()
    expect(screen.getByText(/summary values/i)).toBeTruthy()
    expect(screen.getByText(/time series/i)).toBeTruthy()
  })

  it('ticks an ok metric and reports the toggle', async () => {
    const { onToggle } = setup()
    await userEvent.click(screen.getByRole('checkbox', { name: /Energy/ }))
    expect(onToggle).toHaveBeenCalledWith('energy_mwh')
  })

  it('disables blocked and na metrics', () => {
    setup()
    expect(screen.getByRole('checkbox', { name: /Committed/ })).toHaveProperty('disabled', true)
    expect(screen.getByRole('checkbox', { name: /Loading/ })).toHaveProperty('disabled', true)
  })

  it('ignores a click on a blocked metric', async () => {
    const { onToggle } = setup()
    await userEvent.click(screen.getByRole('checkbox', { name: /Committed/ }))
    expect(onToggle).not.toHaveBeenCalled()
  })

  it('shows the reason for both blocked and na', () => {
    setup()
    expect(screen.getByText(/unit commitment is not enabled/i)).toBeTruthy()
    expect(screen.getByText(/not a branch component/i)).toBeTruthy()
  })

  it('offers a remedy for blocked but never for na', () => {
    const { } = setup()
    expect(screen.getByRole('button', { name: /Enable committable/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /branch/ })).toBeNull()
  })

  it('fires the remedy handler with the action', async () => {
    const { onRemedy } = setup()
    await userEvent.click(screen.getByRole('button', { name: /Enable committable/ }))
    expect(onRemedy).toHaveBeenCalledWith(
      { action: 'open_properties', label: 'Enable committable' })
  })

  it('marks input- and derived-origin metrics so they are not mistaken for results', () => {
    setup()
    expect(screen.getByTitle(/Σ p × w/)).toBeTruthy()
  })
})
