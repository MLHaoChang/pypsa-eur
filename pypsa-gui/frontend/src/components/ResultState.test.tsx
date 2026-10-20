import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import Kpi from './Kpi'
import ResultState, { STATE_PILL } from './ResultState'

afterEach(cleanup)

describe('ResultState', () => {
  it.each([
    ['not_run', 'Not run yet'],
    ['running', 'Running'],
    ['not_modelled', 'Not modelled'],
    ['not_applicable', 'Not applicable'],
    ['stale', 'Out of date'],
    ['unavailable', 'Not available for this run'],
    ['study_only', 'From the study'],
  ] as const)('labels %s as "%s"', (state, label) => {
    expect(STATE_PILL[state].label).toBe(label)
    render(<ResultState state={state} reason="Why." />)
    expect(screen.getByText(label)).toBeTruthy()
    expect(screen.getByText('Why.')).toBeTruthy()
  })

  it('runs its action', () => {
    const onClick = vi.fn()
    render(<ResultState state="not_modelled" reason="Losses were off in this run."
      action={{ label: 'Turn on in Solver settings', onClick }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Turn on in Solver settings' }))
    expect(onClick).toHaveBeenCalledOnce()
  })
})

describe('Kpi with a result state', () => {
  it.each(['not_run', 'running', 'not_modelled', 'not_applicable', 'unavailable', 'study_only'] as const)(
    'never shows a number or a dash for %s', state => {
      render(<Kpi label="Total energy lost" value="0 MWh" state={state} reason="Losses were off." testId="k" />)
      const tile = screen.getByTestId('k')
      expect(tile.querySelector('[data-kpi-value]')).toBeNull()
      expect(tile.textContent).not.toMatch(/\b0\b|—/)
      expect(tile.getAttribute('data-state')).toBe(state)
    })

  it('fromStudy keeps the figure and adds the "From the study" pill (G5)', () => {
    render(<Kpi label="Expected shortfall" value="0.6 h/yr" fromStudy testId="k" />)
    const tile = screen.getByTestId('k')
    expect(tile.querySelector('[data-kpi-value="0.6 h/yr"]')).not.toBeNull()
    expect(screen.getByText('From the study')).toBeTruthy()
  })

  it('shows a computed zero as a number, with what makes it zero', () => {
    render(<Kpi label="Curtailed energy" value="0 MWh" state="zero" basis="all available renewable energy used" testId="k" />)
    expect(screen.getByTestId('k').querySelector('[data-kpi-value="0 MWh"]')).not.toBeNull()
    expect(screen.getByText('all available renewable energy used')).toBeTruthy()
  })

  it('dims a stale value and says it is out of date', () => {
    render(<Kpi label="Total system cost" value="€39.75 M" state="stale" reason="Inputs changed since this run." testId="k" />)
    const figure = screen.getByTestId('k').querySelector('[data-kpi-value="€39.8 M"]')!
    expect(figure.className).toMatch(/opacity-60/)
    expect(screen.getByText('Out of date')).toBeTruthy()
  })

  it('renders as before when ready', () => {
    render(<Kpi label="Total system cost" value="€39.75 M" testId="k" />)
    expect(screen.getByTestId('k').getAttribute('data-state')).toBe('ready')
    expect(screen.queryByText('Not run yet')).toBeNull()
  })
})
