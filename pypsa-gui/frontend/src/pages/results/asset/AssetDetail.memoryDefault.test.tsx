// Assistant memory 02b (plan §2.1): "Save as default" on the Asset Detail chart
// saves a chart_spec@1 for the carrier; Asset Detail then opens that way. A chat
// or link request that names settings wins over the saved default.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast, { Toaster } from 'react-hot-toast'
import AssetDetail from './AssetDetail'
import { assetResultsApi } from './api'
import { useUIStore } from '../../../store/uiStore'
import type { AssetResultsResponse } from './types'
import type { MemoryItem } from '../../../api/memory'
import { assetChartSpec, pickDefault, savableSeries } from './assetDefault'

vi.mock('./api')
const list = vi.fn()
const create = vi.fn()
const edit = vi.fn()
const act = vi.fn()
const versions = vi.fn()
vi.mock('../../../api/memory', async (orig) => {
  const real = await orig<typeof import('../../../api/memory')>()
  return { ...real, memoryApi: { ...real.memoryApi,
    list: (...a: unknown[]) => list(...a), create: (...a: unknown[]) => create(...a),
    edit: (...a: unknown[]) => edit(...a), act: (...a: unknown[]) => act(...a),
    versions: (...a: unknown[]) => versions(...a) } }
})
vi.mock('../../../api/projects', () => ({ projectsApi: {
  list: () => Promise.resolve([{ id: 'p1', name: 'P1', project_kind: 'connection' }]),
} }))

const GAS = { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1' }
const METRICS = [
  { id: 'p', label: 'Active power', unit: 'MW', kind: 'series' as const, origin: 'output' as const, status: 'ok' as const },
  { id: 'p_max_pu', label: 'Availability', unit: 'pu', kind: 'series' as const, origin: 'input' as const, status: 'ok' as const },
  { id: 'cost', label: 'Cost', unit: 'EUR/a', kind: 'series' as const, origin: 'derived' as const, status: 'ok' as const },
  { id: 'energy_mwh', label: 'Energy', unit: 'MWh', kind: 'scalar' as const, origin: 'derived' as const, status: 'ok' as const },
]
const RESPONSE: AssetResultsResponse = {
  asset: { ...GAS, params: {} },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological',
  categories: [{ id: 'summary', label: 'Summary', status: 'ok' }, { id: 'dispatch', label: 'Dispatch', status: 'ok' }],
  metrics: METRICS, scalars: { energy_mwh: 1 }, headline: [],
  index: ['2026-01-01T00:00:00'], periods: null, pct_of_hours: null,
  columns: [{ id: 'p', label: 'Active power', unit: 'MW', metric_id: 'p', agg: null }],
  series: { p: [1] },
}

function saved(over: Partial<MemoryItem> = {}, spec: Record<string, unknown> = {}): MemoryItem {
  return {
    id: 'mem_d', kind: 'chart_spec', scope: 'user', applies_to: 'connection',
    concept_keys: ['view:asset', 'carrier:gas'], status: 'confirmed', origin: 'button', version: 1,
    summary: 'Asset Detail · gas', evidence: [], nomination: null, promoted_from: null,
    recall_count: 0, apply_count: 0, last_used_at: null, created_at: '', updated_at: '',
    body: { spec: { schema: 'chart_spec@1', view_id: 'asset',
      series: [{ component_class: 'Generator', metric: 'p_max_pu', unit: '1' }],
      chart: { type: 'duration' }, ...spec } },
    ...over,
  }
}

const renderIt = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><AssetDetail /><Toaster /></QueryClientProvider>)
}

beforeEach(() => {
  localStorage.clear()
  list.mockReset(); create.mockReset(); edit.mockReset(); act.mockReset(); versions.mockReset()
  list.mockResolvedValue({ items: [], can_promote: false })
  useUIStore.setState({ assetDetailRequest: null, resultSource: 'lopf', currentProject: 'p1' })
  vi.mocked(assetResultsApi.listAssets).mockResolvedValue([GAS])
  vi.mocked(assetResultsApi.get).mockResolvedValue(RESPONSE)
  vi.mocked(assetResultsApi.exportXlsxUrl).mockReturnValue('http://x/export.xlsx')
})
afterEach(() => { cleanup(); toast.remove() })

describe('Save as default', () => {
  it('sits in chart view only', async () => {
    renderIt()
    await screen.findByText(/Gas 1/)
    await waitFor(() => expect(assetResultsApi.get).toHaveBeenCalled())
    expect(screen.queryByTestId('asset-save-default')).toBeNull()
    await userEvent.click(await screen.findByRole('button', { name: 'Chart' }))
    expect(await screen.findByTestId('asset-save-default')).toBeTruthy()
  })

  it('saves the ticked series and mode as a chart spec for this carrier and project kind', async () => {
    create.mockResolvedValue(saved())
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Chart' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Duration' }))
    await userEvent.click(await screen.findByTestId('asset-save-default'))
    await waitFor(() => expect(create).toHaveBeenCalled())
    const sent = create.mock.calls[0][0]
    expect(sent.kind).toBe('chart_spec')
    expect(sent.concept_keys).toEqual(['view:asset', 'carrier:gas'])
    expect(sent.project_kind).toBe('connection')
    expect(sent.body.spec.chart).toEqual({ type: 'duration' })
    // EUR/a has no chart_spec@1 unit, and a scalar is not a series: both left out.
    expect(sent.body.spec.series.map((s: { metric: string }) => s.metric)).toEqual(['p', 'p_max_pu'])
    expect(sent.body.spec.series[1].unit).toBe('1')
    const toastEl = await screen.findByTestId('asset-default-toast')
    expect(toastEl.textContent).toBe('Saved as your default for Asset Detail · gas · Edit › · Undo')
    expect(toastEl.closest('[role="status"]')).toBeTruthy()
  })

  it('Undo on a first save retires it', async () => {
    create.mockResolvedValue(saved())
    act.mockResolvedValue(saved({ status: 'retired' }))
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Chart' }))
    await userEvent.click(await screen.findByTestId('asset-save-default'))
    await userEvent.click(await screen.findByTestId('asset-default-undo'))
    await waitFor(() => expect(act).toHaveBeenCalledWith('mem_d', 'retire'))
    expect(edit).not.toHaveBeenCalled()
  })

  it('Undo on a re-save restores the previous default, not none', async () => {
    create.mockResolvedValue(saved({ version: 3 }))
    versions.mockResolvedValue({ id: 'mem_d', versions: [
      { version: 3, summary: 'new', body: { spec: 'new' }, evidence: [] },
      { version: 2, summary: 'before', body: { spec: 'before' }, evidence: [] },
    ] })
    edit.mockResolvedValue(saved({ version: 4 }))
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Chart' }))
    await userEvent.click(await screen.findByTestId('asset-save-default'))
    await userEvent.click(await screen.findByTestId('asset-default-undo'))
    await waitFor(() => expect(edit).toHaveBeenCalledWith('mem_d', { body: { spec: 'before' }, summary: 'before' }))
    expect(act).not.toHaveBeenCalled()
  })
})

describe('A saved default', () => {
  it('opens the asset in chart view with its metrics and mode', async () => {
    list.mockResolvedValue({ items: [saved()], can_promote: false })
    renderIt()
    await waitFor(() => expect(assetResultsApi.get).toHaveBeenLastCalledWith(
      expect.objectContaining({ metrics: ['p_max_pu'], mode: 'duration' })))
    // UX M5/M7: the chart shows the default, so Save is off and says why.
    expect((await screen.findByTestId('asset-default-line')).textContent).toBe('Your default · Edit ›')
    expect((screen.getByTestId('asset-save-default') as HTMLButtonElement).disabled).toBe(true)
  })

  it('says "Already your default" when the user ticks their way back to it', async () => {
    list.mockResolvedValue({ items: [saved()], can_promote: false })
    useUIStore.setState({ assetDetailRequest: {
      componentClass: 'Generator', name: 'Gas 1', metrics: ['p_max_pu'], mode: 'duration', chart: true,
    } })
    renderIt()
    expect((await screen.findByTestId('asset-default-line')).textContent).toBe('Already your default · Edit ›')
  })

  it('gives way to a request that names settings', async () => {
    list.mockResolvedValue({ items: [saved()], can_promote: false })
    useUIStore.setState({ assetDetailRequest: {
      componentClass: 'Generator', name: 'Gas 1', mode: 'monthly', chart: false,
    } })
    renderIt()
    await waitFor(() => expect(assetResultsApi.get).toHaveBeenLastCalledWith(
      expect.objectContaining({ mode: 'monthly' })))
    expect(screen.queryByTestId('asset-save-default')).toBeNull()
  })
})

describe('pickDefault', () => {
  it('prefers this project kind, then the user over the workspace, and skips other classes', () => {
    const any = saved({ id: 'any', applies_to: null })
    const org = saved({ id: 'org', scope: 'org' })
    const mine = saved({ id: 'mine' })
    const store = saved({ id: 'other-class' }, {
      series: [{ component_class: 'StorageUnit', metric: 'p', unit: 'MW' }] })
    expect(pickDefault([any, org, mine], GAS, 'connection')?.item.id).toBe('mine')
    expect(pickDefault([any, org], GAS, 'connection')?.item.id).toBe('org')
    expect(pickDefault([any], GAS, 'connection')?.item.id).toBe('any')
    expect(pickDefault([store], GAS, 'connection')).toBeNull()
    expect(pickDefault([saved({ applies_to: 'planning_dynamics' })], GAS, 'connection')).toBeNull()
  })

  it('builds a closed chart_spec@1 with no carrier or formula in the series', () => {
    const spec = assetChartSpec(GAS, savableSeries(METRICS, ['p', 'cost', 'energy_mwh']), 'monthly')
    expect(spec).toEqual({ schema: 'chart_spec@1', view_id: 'asset',
      series: [{ component_class: 'Generator', metric: 'p', unit: 'MW' }], chart: { type: 'bar' } })
  })
})
