// FmeaTab's MUTATING behaviour — the part the pure-logic tests in fmea.test.ts
// deliberately don't reach. Each block pins one hazard of the editable
// worksheet: an edit that must reach the sidecar as an OVERLAY (not a row
// rewrite), an emptied cell that must DELETE its overlay rather than persist
// "", an expert row's edit that must rewrite that row instead, the add/delete
// round-trip, and the sweep button's running/error states.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import FmeaTab from './FmeaTab'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getFmeaModes: vi.fn(),
      getWorksheet: vi.fn(),
      putWorksheet: vi.fn(),
      postFmeaSweep: vi.fn(),
      getStressScenarios: vi.fn(),
    },
  }
})
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}))

const COMPUTED = {
  mode_id: 'generator:g1:forced_outage', component_class: 'Generator',
  name: 'g1', failure_class: 'A', occurrence_per_year: 8,
  occurrence_basis: 'EFORd', severity_eur: 100,
  criticality_eur_per_year: 800, in_metric_scope: true,
  engine: 'copt', fidelity: 'analytic_convolution',
}
const EXPERT = {
  mode_id: 'manual:cyber', component_class: 'Network', name: 'cyber',
  failure_class: 'D', occurrence_per_year: 0.5, occurrence_basis: 'expert',
  severity_eur: 200, criticality_eur_per_year: 100, in_metric_scope: false,
  mitigability: 'segmentation', engine: 'expert', fidelity: 'expert_judgement',
}

function sidecar(overrides: Record<string, unknown> = {}) {
  return { version: 1, manual_rows: [EXPERT], overlays: {}, ...overrides }
}

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getFmeaModes).mockReset()
    .mockResolvedValue({ per_mode: [COMPUTED], sweep_status: null, sweep_error: null })
  vi.mocked(resultsApi.getWorksheet).mockReset().mockResolvedValue(sidecar())
  vi.mocked(resultsApi.putWorksheet).mockReset().mockResolvedValue({ version: 2 })
  vi.mocked(resultsApi.postFmeaSweep).mockReset().mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.getStressScenarios).mockReset()
    .mockResolvedValue({ scenarios: [{ id: 'cold_snap' }] })
})

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><FmeaTab /></QueryClientProvider>)
}

async function mitigabilityInputFor(name: string) {
  const row = (await screen.findByText(name)).closest('tr')!
  return within_(row).getByPlaceholderText('—') as HTMLInputElement
}
// Local `within` to avoid importing the whole helper surface.
function within_(el: HTMLElement) {
  return {
    getByPlaceholderText: (p: string) =>
      Array.from(el.querySelectorAll('input')).find(
        i => (i as HTMLInputElement).placeholder === p)! as HTMLInputElement,
  }
}

it('renders computed and expert rows together with provenance badges', async () => {
  renderTab()
  expect(await screen.findByText('g1')).toBeTruthy()
  expect(screen.getByText('cyber')).toBeTruthy()
  expect(screen.getByText('copt')).toBeTruthy()
  // "expert" appears twice on the expert row — the provenance badge AND the
  // occurrence basis cell. Assert the BADGE specifically (it carries the
  // fidelity tooltip; the basis cell does not).
  const expertBadges = screen.getAllByText('expert').filter(
    el => el.getAttribute('title')?.includes('Expert-entered'))
  expect(expertBadges).toHaveLength(1)
})

it('persists a computed row edit as an OVERLAY, leaving manual rows intact', async () => {
  const user = userEvent.setup()
  renderTab()
  const input = await mitigabilityInputFor('g1')
  await user.click(input)
  await user.type(input, 'N-1 reserve')
  await user.tab()
  await waitFor(() => expect(resultsApi.putWorksheet).toHaveBeenCalled())
  const [, body] = vi.mocked(resultsApi.putWorksheet).mock.calls[0]
  expect(body.overlays['generator:g1:forced_outage'])
    .toEqual({ mitigability: 'N-1 reserve' })
  // The expert row must survive untouched — an overlay edit is not a rewrite.
  expect(body.manual_rows).toHaveLength(1)
  expect((body.manual_rows[0] as { name: string }).name).toBe('cyber')
})

it('DELETES the overlay when a computed row cell is emptied', async () => {
  const user = userEvent.setup()
  vi.mocked(resultsApi.getWorksheet).mockResolvedValue(
    sidecar({ overlays: { 'generator:g1:forced_outage': { mitigability: 'old note' } } }))
  renderTab()
  const input = await waitFor(async () => {
    const i = await mitigabilityInputFor('g1')
    expect(i.value).toBe('old note')
    return i
  })
  await user.clear(input)
  await user.tab()
  await waitFor(() => expect(resultsApi.putWorksheet).toHaveBeenCalled())
  const [, body] = vi.mocked(resultsApi.putWorksheet).mock.calls[0]
  expect(body.overlays).toEqual({})
})

it('edits an EXPERT row in place rather than creating an overlay', async () => {
  const user = userEvent.setup()
  renderTab()
  const input = await mitigabilityInputFor('cyber')
  await user.clear(input)
  await user.type(input, 'zero trust')
  await user.tab()
  await waitFor(() => expect(resultsApi.putWorksheet).toHaveBeenCalled())
  const [, body] = vi.mocked(resultsApi.putWorksheet).mock.calls[0]
  expect(body.overlays).toEqual({})
  expect((body.manual_rows[0] as { mitigability: string }).mitigability)
    .toBe('zero trust')
})

it('does not save when a cell is blurred unchanged', async () => {
  const user = userEvent.setup()
  renderTab()
  const input = await mitigabilityInputFor('g1')
  await user.click(input)
  await user.tab()
  expect(resultsApi.putWorksheet).not.toHaveBeenCalled()
})

it('adds an expert row with criticality computed, never typed', async () => {
  const user = userEvent.setup()
  renderTab()
  await screen.findByText('g1')
  await user.type(screen.getByPlaceholderText(/Name \(e\.g\./), 'Fuel supply loss')
  await user.type(screen.getByPlaceholderText('events/yr'), '0.2')
  await user.type(screen.getByPlaceholderText('severity €'), '5000')
  await user.click(screen.getByRole('button', { name: /Add/ }))
  await waitFor(() => expect(resultsApi.putWorksheet).toHaveBeenCalled())
  const [, body] = vi.mocked(resultsApi.putWorksheet).mock.calls[0]
  const added = body.manual_rows[1] as Record<string, unknown>
  expect(added.criticality_eur_per_year).toBe(1000)
  expect(added.engine).toBe('expert')
  expect(added.failure_class).toBe('D')
})

it('deletes only the targeted expert row', async () => {
  const user = userEvent.setup()
  renderTab()
  await screen.findByText('cyber')
  await user.click(screen.getByTitle('Delete expert row'))
  await waitFor(() => expect(resultsApi.putWorksheet).toHaveBeenCalled())
  const [, body] = vi.mocked(resultsApi.putWorksheet).mock.calls[0]
  expect(body.manual_rows).toHaveLength(0)
})

it('offers no delete affordance on computed rows', async () => {
  vi.mocked(resultsApi.getWorksheet).mockResolvedValue(
    { version: 1, manual_rows: [], overlays: {} })
  renderTab()
  await screen.findByText('g1')
  expect(screen.queryByTitle('Delete expert row')).toBeNull()
})

it('sends the registry scenarios when starting a sweep', async () => {
  const user = userEvent.setup()
  renderTab()
  await screen.findByText('g1')
  await user.click(screen.getByRole('button', { name: /Run B\/C sweep/ }))
  await waitFor(() => expect(resultsApi.postFmeaSweep).toHaveBeenCalledWith(
    [{ id: 'cold_snap' }]))
})

it('shows the sweep as running and surfaces its error', async () => {
  vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({
    per_mode: [COMPUTED], sweep_status: 'running', sweep_error: null })
  renderTab()
  expect(await screen.findByText(/Sweeping…/)).toBeTruthy()
  cleanup()
  vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({
    per_mode: [COMPUTED], sweep_status: 'failed',
    sweep_error: 'exceeds the sweep budget of 20' })
  renderTab()
  expect(await screen.findByText(/exceeds the sweep budget of 20/)).toBeTruthy()
})

it('renders the empty state when every source is empty', async () => {
  vi.mocked(resultsApi.getFmeaModes).mockResolvedValue(null)
  vi.mocked(resultsApi.getWorksheet).mockResolvedValue(
    { version: 0, manual_rows: [], overlays: {} })
  renderTab()
  expect(await screen.findByText(/No failure modes yet/)).toBeTruthy()
})
