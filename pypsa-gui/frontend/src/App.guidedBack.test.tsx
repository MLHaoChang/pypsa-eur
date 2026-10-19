// UX template audit N5 (regression of M10): Campus electrical opened from
// Guided's Results step names that step and offers the way back.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

vi.mock('./pages/CampusElectricalPanel', () => ({ default: () => <div data-testid="campus-body">campus</div> }))

import { FullPageTab } from './App'
import { useUIStore } from './store/uiStore'
import { useHubDesignStore } from './pages/hubDesign/hubDesignStore'

function renderTab(panel: 'campusElectrical' | 'issues') {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><FullPageTab panel={panel} onClose={vi.fn()} /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Guided way back from the grid checks', () => {
  beforeEach(() => { useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'campusElectrical' }) })

  it('shows the hub-design crumb and a Back to Results that returns to the Results step', () => {
    renderTab('campusElectrical')
    expect(screen.getByText('HUB DESIGN · GRID PLAN')).toBeTruthy()
    expect(screen.queryByText('STUDIES')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Back to Results/ }))
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(useHubDesignStore.getState().step).toBe('results')
  })

  it('Expert keeps the STUDIES crumb and has no Back control', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderTab('campusElectrical')
    expect(screen.getByText('STUDIES')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Back to Results/ })).toBeNull()
  })

  it('a panel Guided does not reach from Results is unchanged', () => {
    renderTab('issues')
    expect(screen.queryByRole('button', { name: /Back to Results/ })).toBeNull()
  })
})
