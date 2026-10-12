// The import match report (plan M3) renders what the backend answered and
// writes nothing until Apply is pressed.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ImportGeometryDialog from './ImportGeometryDialog'
import type { ImportReport } from '../api/mapLayout'

const REPORT: ImportReport = {
  applied: false,
  routes: [
    { feature: 0, label: 'L1', key: 'line:L1', how: 'name', points: [[6.5, 53.5]], reversed: false },
    { feature: 1, label: 'feature 1', key: 'link:K1', how: 'endpoints', points: [], reversed: true },
  ],
  sites: [{ feature: 2, id: 'imp_1', name: 'Plot', boundary: [[1, 1], [2, 1], [2, 2]], buses: ['A'], origin: { lng: 1.5, lat: 1.5 } }],
  unmatched: [{ feature: 3, label: 'Pin', reason: 'unsupported geometry Point' }],
}

describe('ImportGeometryDialog', () => {
  it('shows every match with how it matched, the sites, and the unmatched list', () => {
    render(<ImportGeometryDialog report={REPORT} fileName="survey.geojson" applying={false} onApply={() => {}} onClose={() => {}} />)
    expect(screen.getByText('line:L1')).toBeTruthy()
    expect(screen.getByText('link:K1')).toBeTruthy()
    const hows = screen.getAllByTestId('how').map(e => e.textContent)
    expect(hows).toEqual(['by name', 'by endpoints (reversed)'])
    expect(screen.getByText('1 bend')).toBeTruthy()
    expect(screen.getByText('chord')).toBeTruthy()
    expect(screen.getByText('Plot')).toBeTruthy()
    expect(screen.getByText(/buses: A/)).toBeTruthy()
    expect(screen.getByTestId('unmatched').textContent).toContain('Pin — unsupported geometry Point')
    expect(screen.getByText(/survey\.geojson · 2 routes, 1 site, 1 unmatched/)).toBeTruthy()
  })

  it('Apply is the only write and names what it applies; Cancel closes', async () => {
    const onApply = vi.fn(), onClose = vi.fn()
    render(<ImportGeometryDialog report={REPORT} fileName="s.kml" applying={false} onApply={onApply} onClose={onClose} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply 3 changes' }))
    expect(onApply).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('an empty report disables Apply and says why', () => {
    render(<ImportGeometryDialog report={{ applied: false, routes: [], sites: [], unmatched: [] }} fileName="x" applying={false} onApply={() => {}} onClose={() => {}} />)
    expect((screen.getByRole('button', { name: /Apply/ }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Nothing in this file matched/)).toBeTruthy()
  })
})
