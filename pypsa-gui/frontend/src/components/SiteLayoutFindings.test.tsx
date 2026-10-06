// S2: the overlay's layout readout — findings as text, each a deep link.
import { beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import SiteLayoutFindings from './SiteLayoutFindings'
import { useUIStore } from '../store/uiStore'
import type { PlacementFinding } from '../site3d/placementCheck'

const findings: PlacementFinding[] = [
  { key: 'Generator:GEN', other: 'Load:HALL', kind: 'keepOut', severity: 'warn', distanceM: 10, message: 'GEN is 10 m from HALL; it should keep 30 m away' },
  { key: 'Transformer:TR1', kind: 'notBetweenBuses', severity: 'info', distanceM: 140, message: 'TR1 is 140 m from the line between the HV and MV yards; it should sit between its two buses' },
]

beforeEach(() => useUIStore.setState({ selectedComponent: null }))

describe('SiteLayoutFindings', () => {
  it('is collapsed and says so when there is nothing to report', () => {
    render(<SiteLayoutFindings findings={[]} />)
    const details = screen.getByTestId('site-layout-findings') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('Layout · arranged, no findings')
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('lists each finding with its message, open by default, and selects the object on click', () => {
    render(<SiteLayoutFindings findings={findings} />)
    const details = screen.getByTestId('site-layout-findings') as HTMLDetailsElement
    expect(details.open).toBe(true)
    expect(screen.getByRole('heading').textContent).toBe('Layout · 2 findings')
    const items = screen.getAllByRole('listitem').map(li => li.textContent)
    expect(items).toEqual([findings[0].message, findings[1].message])
    fireEvent.click(screen.getByRole('button', { name: findings[1].message }))
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Transformer', name: 'TR1' })
  })

  it('names the objects the packer could not arrange', () => {
    render(<SiteLayoutFindings findings={[]} unresolved={['Store:H2']} />)
    expect(screen.getByTestId('site-layout-unresolved').textContent).toMatch(/could not satisfy every rule for H2/)
  })
})
