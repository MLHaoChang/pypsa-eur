import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { campusApi } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'
import { ConnectionLimits } from './ConnectionLimits'

vi.mock('../../api/campusElectrical', async (orig) => {
  const actual = await orig<typeof import('../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, gridReadiness: vi.fn() } }
})

const row = (id: string, over: Record<string, unknown> = {}) => ({
  id, requirement: 'r', limit: 5, unit: 'MW/min', source: 'the template', project_value: null, verdict: 'unknown',
  assumed: false, note: null, section: 'connection',
  target: { section: 'connection', panel: null, component: 'Link', entity: 'import', field: 'connection_ramp' }, ...over,
})

function mount() {
  useUIStore.setState({ currentProject: 'Demo' })
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ConnectionLimits /></QueryClientProvider>)
}
afterEach(() => { cleanup(); useUIStore.setState({ uiMode: 'expert' }) })

describe('ConnectionLimits', () => {
  it('lists energization, ramp and oscillation read-only with their source and assumed tag', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [
      row('energization_peak', { unit: 'MW', limit: null, target: { section: 'connection', panel: null, component: 'Link', entity: 'import', field: 'eh_energization_mw' } }),
      row('ramp_rate', { assumed: true }), row('oscillation', { unit: '% of connection, peak-to-peak', limit: 1 }),
      row('scr') ] } as never)
    mount()
    expect((await screen.findByTestId('connection-limit-ramp_rate')).textContent).toMatch(/5 MW\/min.*\(assumed\).*the template/)
    expect(screen.getByTestId('connection-limit-oscillation').textContent).toMatch(/Oscillation band/)
    expect(screen.getByTestId('connection-limit-energization_peak').textContent).toMatch(/not known/)
    expect(screen.queryByTestId('connection-limit-scr')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('a staged project lists every energization period, not just the first', async () => {
    const t = { section: 'connection', panel: null, component: 'Link', entity: 'import', field: 'eh_energization_mw' }
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [
      row('energization_peak:2030', { unit: 'MW', limit: 20, target: t }),
      row('energization_peak:2035', { unit: 'MW', limit: 40, target: t }),
      row('energization_peak:2040', { unit: 'MW', limit: 60, target: t })] } as never)
    mount()
    const line = (await screen.findByTestId('connection-limit-energization_peak')).textContent ?? ''
    expect(line).toMatch(/2030 20 MW · 2035 40 MW · 2040 60 MW/)
    expect(line).not.toMatch(/Energization: 20 MW\b(?! ·)/)
  })

  it('Expert offers the route to the schedule; it opens the link', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [
      row('energization_peak', { unit: 'MW', limit: 8, target: { section: 'connection', panel: null, component: 'Link', entity: 'import', field: 'eh_energization_mw' } })] } as never)
    useUIStore.setState({ uiMode: 'expert' })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit the energization schedule' }))
    expect(useUIStore.getState().selectedComponent).toMatchObject({ type: 'Link', name: 'import' })
  })

  it('Guided offers the same button; it opens the field drawer and stays in Guided', async () => {
    const target = { section: 'connection', panel: null, component: 'Link', entity: 'import', field: 'eh_energization_mw' }
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('energization_peak', { unit: 'MW', limit: 8, target })] } as never)
    useUIStore.setState({ uiMode: 'guided', fieldDrawer: null })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit the energization schedule' }))
    expect(useUIStore.getState().fieldDrawer).toMatchObject({ component: 'Link', entity: 'import', field: 'eh_energization_mw' })
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('a failed read says so, never an empty list', async () => {
    vi.mocked(campusApi.gridReadiness).mockRejectedValue(new Error('x'))
    mount()
    expect(await screen.findByTestId('connection-limits-error')).toBeTruthy()
  })
})
