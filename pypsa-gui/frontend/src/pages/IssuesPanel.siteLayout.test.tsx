// S2 (visual-layers plan 3): the Issues panel shows the open 3D site's
// layout findings as their own group, each with the same View deep link the
// preflight rows have. Advisory — they never change the preflight counts.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { usePlacementFindings } from '../site3d/placementFindingsStore'
import IssuesPanel from './IssuesPanel'

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, preflight: vi.fn() } }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn(), updateCarrier: vi.fn() } }
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><IssuesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(simulationApi.preflight).mockReset().mockResolvedValue({ ok: true, errors: 0, warnings: 0, issues: [] })
  vi.mocked(networkApi.getCarriers).mockReset().mockResolvedValue([])
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: null, highlightedComponent: null, activeSlidePanel: 'issues' })
  usePlacementFindings.getState().clear()
})
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, activeSlidePanel: null })
  usePlacementFindings.getState().clear()
})

describe('IssuesPanel — Site layout group (S2)', () => {
  it('shows no group while no site view has published findings', async () => {
    renderPanel()
    await screen.findByText('All checks passed')
    expect(screen.queryByText('Site layout')).toBeNull()
  })

  it('lists the findings under "Site layout" and deep-links each to its component without touching the preflight result', async () => {
    usePlacementFindings.getState().publish({ id: 'a', name: 'Campus' }, [
      { key: 'Generator:GEN', other: 'Load:HALL', kind: 'keepOut', severity: 'warn', distanceM: 10, message: 'GEN is 10 m from HALL; it should keep 30 m away' },
      { key: 'Transformer:TR1', kind: 'notBetweenBuses', severity: 'info', distanceM: 140, message: 'TR1 is 140 m from the line between the HV and MV yards; it should sit between its two buses' },
    ])
    renderPanel()
    await screen.findByText('All checks passed')   // the preflight stays clean: layout findings are advisory
    expect(screen.getByText('Site layout')).toBeTruthy()
    expect(screen.getByText(/Campus · from the 3D site view/)).toBeTruthy()
    const group = screen.getByTestId('site-layout-group')
    expect(group.textContent).toContain('GEN is 10 m from HALL; it should keep 30 m away')
    expect(group.textContent).toContain('Inside a keep-out distance')
    expect(group.textContent).toContain('Not between its two buses')
    const views = screen.getAllByRole('button', { name: /View/ })
    expect(views).toHaveLength(2)
    await userEvent.click(views[1])
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Transformer', name: 'TR1' })
    expect(useUIStore.getState().highlightedComponent).toMatchObject({ type: 'Transformer', name: 'TR1' })
    // jumpTo closes the slide panel so the properties editor is visible.
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
  })

  it('follows the store: the group disappears when the site view clears it', async () => {
    usePlacementFindings.getState().publish({ id: 'a', name: 'Campus' }, [
      { key: 'Load:HALL', kind: 'outsideBoundary', severity: 'warn', message: 'HALL sticks out of the site boundary' },
    ])
    renderPanel()
    await screen.findByText('Site layout')
    usePlacementFindings.getState().clear()
    expect(await screen.findByText('All checks passed')).toBeTruthy()
    expect(screen.queryByText('Site layout')).toBeNull()
  })
})
