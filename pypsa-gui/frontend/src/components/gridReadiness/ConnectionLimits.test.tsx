import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { campusApi } from '../../api/campusElectrical'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { ConnectionLimits } from './ConnectionLimits'

vi.mock('../../api/network', () => ({ networkApi: { getBuses: vi.fn().mockResolvedValue([
  { name: 'grid', eh_poc: true, eh_sk_mva: 250 }, { name: 'it', eh_poc: false, eh_sk_mva: 9 }]), updateBus: vi.fn() } }))
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
  it('lists energization, ramp, oscillation and fault level with their source and assumed tag', async () => {
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
    fireEvent.change(await screen.findByLabelText('Fault level source'), { target: { value: 'datasheet' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save fault level' }))
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

  it('the fault level shows its figure from the PoC bus and routes to that bus field; ramp says what else sets a ramp', async () => {
    const bus = { section: 'connection', panel: null, component: 'Bus', entity: 'grid', field: 'eh_sk_mva' }
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [
      row('ramp_rate', { setting: setting('connection_ramp') }),
      row('scr', { target: bus, setting: setting('fault_level') })] } as never)
    useUIStore.setState({ uiMode: 'expert' })
    mount()
    expect((await screen.findByTestId('connection-limit-scr')).textContent).toMatch(/Fault level: 250 MVA/)
    expect(screen.getByTestId('connection-limit-ramp_rate').textContent).toMatch(/optimisation's ramp is set on the connection link/)
    // The value is edited on this line; no hop to the bus.
    expect(screen.queryByRole('button', { name: 'Edit the fault level ›' })).toBeNull()
  })

  it('the fault level value is edited beside its source and written to the PoC bus', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('scr', { setting: setting('fault_level') })] } as never)
    vi.mocked(campusApi.setGridLimit).mockResolvedValue({})
    vi.mocked(networkApi.updateBus).mockResolvedValue({ name: 'grid' } as never)
    mount()
    const input = await screen.findByLabelText('Fault level value')
    expect(input).toHaveProperty('value', '250')
    fireEvent.change(input, { target: { value: '400' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save fault level' }))
    await waitFor(() => expect(networkApi.updateBus).toHaveBeenCalledWith('grid', expect.objectContaining({ name: 'grid', eh_sk_mva: 400 })))
    // A first save also records the limit's source and note.
    expect(campusApi.setGridLimit).toHaveBeenCalledWith('Demo', 'fault_level', { source: 'assumed', note: '' })
  })

  it('a fault level of 0 or less is refused and nothing is written', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue({ rows: [row('scr', { setting: setting('fault_level') })] } as never)
    mount()
    fireEvent.change(await screen.findByLabelText('Fault level value'), { target: { value: '0' } })
    expect(screen.getByText(/must be above 0 MVA/)).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Save fault level' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
