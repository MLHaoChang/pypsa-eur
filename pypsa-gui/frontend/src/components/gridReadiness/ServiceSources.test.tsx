import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ServiceSources } from './ServiceSources'

const many = Array.from({ length: 11 }, (_, i) => `Backup BESS ${i + 1}`).concat('Spare BESS')

describe('ServiceSources', () => {
  it('folds a service with more than five units after five, and shows all on click', () => {
    render(<ServiceSources testId="s" rest="fast frequency response (assumed, MV UPS)" sources={[
      { service: 'grid_forming', label: 'grid forming', units: many },
      { service: 'voltage_control', label: 'voltage control', units: ['Rooftop PV'] },
    ]} />)
    const cell = screen.getByTestId('s')
    expect(cell.textContent).toContain('Backup BESS 5')
    expect(cell.textContent).not.toContain('Backup BESS 6')
    expect(cell.textContent).toContain('voltage control (Rooftop PV); fast frequency response (assumed, MV UPS)')
    const toggle = screen.getByTestId('s-toggle-grid_forming')
    expect(toggle.textContent).toBe('(7 more) Show all')
    expect(screen.queryByTestId('s-toggle-voltage_control')).toBeNull()
    fireEvent.click(toggle)
    expect(cell.textContent).toContain('Backup BESS 11, Spare BESS')
    expect(toggle.textContent).toBe('Show fewer')
  })

  it('reads as the plain value with five units or fewer', () => {
    render(<ServiceSources testId="s" sources={[
      { service: 'grid_forming', label: 'grid forming', units: ['A', 'B', 'C', 'D', 'E'] },
    ]} />)
    expect(screen.getByTestId('s').textContent).toBe('grid forming (A, B, C, D, E)')
    expect(screen.queryByRole('button')).toBeNull()
  })
})
