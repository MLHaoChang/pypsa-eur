import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import Kpi from './Kpi'
import { splitValueUnit } from '../utils/format'

afterEach(cleanup)

describe('splitValueUnit', () => {
  it.each([
    ['€2.21 M', '€2.21', 'M'],
    ['136.64 €/MWh', '136.64', '€/MWh'],
    ['0.000 %', '0.000', '%'],
    ['134,902.1 tCO₂', '134,902.1', 'tCO₂'],
    ['-€1.50 k', '-€1.50', 'k'],
    ['1.09 TWh', '1.09', 'TWh'],
  ])('splits %s into number and unit', (s, value, unit) => {
    expect(splitValueUnit(s)).toEqual({ value, unit })
  })

  it.each(['—', 'Site transformer', 'n/a', '3 of 5'])('leaves %s whole', s => {
    expect(splitValueUnit(s)).toEqual({ value: s, unit: undefined })
  })
})

describe('Kpi', () => {
  it('renders the label in sentence case, not forced capitals', () => {
    render(<Kpi label="Total system cost" value="€39.75 M" />)
    const label = screen.getByText('Total system cost')
    expect(label.className).not.toMatch(/uppercase/)
  })

  it('keeps the unit on the value line, never wrapping', () => {
    render(<Kpi label="System mean" value="136.64 €/MWh" />)
    const value = screen.getByText('136.64')
    const unit = screen.getByText('€/MWh')
    expect(value.parentElement).toBe(unit.parentElement)
    expect(value.parentElement!.className).toMatch(/whitespace-nowrap/)
  })

  it('uses an explicit unit as given and does not split the value', () => {
    render(<Kpi label="Peak load" value="40.00" unit="MW" />)
    expect(screen.getByText('40.00')).toBeTruthy()
    expect(screen.getByText('MW')).toBeTruthy()
  })

  it('shows the basis line and the hover hint', () => {
    render(<Kpi label="New investment" value="€2.21 M" basis="annualised" hint="Δ capacity × annualised capital cost" testId="k" />)
    expect(screen.getByText('annualised')).toBeTruthy()
    expect(screen.getByTestId('k').getAttribute('title')).toBe('Δ capacity × annualised capital cost')
  })

  it('marks a headline by fill and weight, without a coloured rail', () => {
    const { container } = render(<Kpi label="Total system cost" value="€39.75 M" headline testId="k" />)
    expect(screen.getByTestId('k').getAttribute('data-headline')).toBe('true')
    expect(container.querySelector('.bg-gradient-to-b')).toBeNull()
  })

  it('colours a delta from the semantic tone, never the accent', () => {
    render(<Kpi label="Cost" value="€1 M" delta="+5 %" deltaTone="bad" />)
    const delta = screen.getByText('+5 %')
    expect(delta.className).toMatch(/text-danger/)
    expect(delta.className).not.toMatch(/accent/)
  })
})

describe('Kpi figure lookup', () => {
  it('exposes the whole figure on the value line', () => {
    render(<Kpi label="Total emissions" value="424.2 tCO₂" />)
    expect(document.querySelector('[data-kpi-value="424.2 tCO₂"]')).not.toBeNull()
  })
})
