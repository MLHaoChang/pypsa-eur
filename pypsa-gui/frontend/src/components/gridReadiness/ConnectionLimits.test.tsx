import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { campusApi } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'
import { ConnectionLimits } from './ConnectionLimits'

vi.mock('../../api/campusElectrical', async (orig) => {
  const actual = await orig<typeof import('../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, gridReadiness: vi.fn(), setGridLimit: vi.fn(), resetGridLimit: vi.fn() } }
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

const setting = (item: string, over: Record<string, unknown> = {}) => ({
  item, user: null, default: { value: 5, source: 'assumed', origin: 'placeholder' }, ...over })

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
    expect(screen.queryByTestId('limit-editor-fault_level')).toBeNull()
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

  it('saves a ramp limit with its unit, source and note, and reloads the readiness read', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('ramp_rate', { setting: setting('connection_ramp') })] } as never)
    vi.mocked(campusApi.setGridLimit).mockResolvedValue({})
    mount()
    const input = await screen.findByLabelText('Connection ramp limit value')
    expect(screen.getByText('MW/min', { selector: 'span' })).toBeTruthy()
    fireEvent.change(input, { target: { value: '12' } })
    fireEvent.change(screen.getByLabelText('Connection ramp limit source'), { target: { value: 'code' } })
    fireEvent.change(screen.getByLabelText('Connection ramp limit note'), { target: { value: 'TSO letter' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save connection ramp limit' }))
    await waitFor(() => expect(campusApi.setGridLimit).toHaveBeenCalledWith('Demo', 'connection_ramp',
      { value: 12, source: 'code', note: 'TSO letter' }))
    await waitFor(() => expect(vi.mocked(campusApi.gridReadiness).mock.calls.length).toBeGreaterThan(1))
  })

  it('refuses an out-of-range oscillation value before sending, and shows the backend 422', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('oscillation', { setting: setting('oscillation') })] } as never)
    mount()
    const input = await screen.findByLabelText('Oscillation band value')
    fireEvent.change(input, { target: { value: '150' } })
    expect((screen.getByRole('button', { name: 'Save oscillation band' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(input, { target: { value: '2' } })
    vi.mocked(campusApi.setGridLimit).mockRejectedValue({ response: { data: { detail: { error_kind: 'x', message: 'the project is locked' } } } })
    fireEvent.click(screen.getByRole('button', { name: 'Save oscillation band' }))
    expect((await screen.findByTestId('limit-error-oscillation')).textContent).toMatch(/locked/)
  })

  it('the fault level sends a source and note only, never a value', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('scr', { setting: setting('fault_level', { default: { value: null, source: 'assumed', origin: 'placeholder' } }) })] } as never)
    vi.mocked(campusApi.setGridLimit).mockResolvedValue({})
    mount()
    fireEvent.change(await screen.findByLabelText('Fault level source source'), { target: { value: 'datasheet' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save fault level source' }))
    await waitFor(() => expect(campusApi.setGridLimit).toHaveBeenCalledWith('Demo', 'fault_level', { source: 'datasheet', note: '' }))
  })

  it('Reset names what it resets to and deletes the limit', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('ramp_rate', { setting: setting('connection_ramp', {
      user: { value: 9, source: 'code', note: '' },
      default: { value: 5, source: 'code', origin: 'template' } }) })] } as never)
    vi.mocked(campusApi.resetGridLimit).mockResolvedValue({})
    mount()
    fireEvent.click(await screen.findByRole('button', { name: "Reset to the template's 5 MW/min, grid code" }))
    await waitFor(() => expect(campusApi.resetGridLimit).toHaveBeenCalledWith('Demo', 'connection_ramp'))
  })

  it('with no limit set there is no Reset, and an unset line offers the placeholder as its start', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('ramp_rate', { setting: setting('connection_ramp') })] } as never)
    mount()
    expect(await screen.findByLabelText('Connection ramp limit value')).toHaveProperty('value', '5')
    expect(screen.queryByRole('button', { name: /^Reset/ })).toBeNull()
  })

  it('`only` narrows the list to the line a Guided to-check item points at', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [
      row('ramp_rate', { setting: setting('connection_ramp') }),
      row('oscillation', { setting: setting('oscillation') })] } as never)
    useUIStore.setState({ currentProject: 'Demo' })
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectionLimits only={['oscillation']} /></QueryClientProvider>)
    expect(await screen.findByLabelText('Oscillation band value')).toBeTruthy()
    expect(screen.queryByLabelText('Connection ramp limit value')).toBeNull()
  })
})
