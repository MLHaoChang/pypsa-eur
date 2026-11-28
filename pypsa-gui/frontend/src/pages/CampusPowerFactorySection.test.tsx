// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PowerFactoryBundle, PowerFactoryBundles } from '../api/campusElectrical'
import { CampusPowerFactorySection, powerFactoryGaps, powerFactoryValueName, powerFactoryWhen } from './CampusElectricalPanel'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: Object.assign((sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }), { subscribe: () => () => {} }),
}))
const nav = vi.hoisted(() => ({ withReturnFrom: vi.fn((jump: () => void) => jump()), openPanel: vi.fn() }))
vi.mock('../layout/flowNavigation', () => ({ withReturnFrom: nav.withReturnFrom }))
vi.mock('./results/openPanel', () => ({ openPanel: nav.openPanel }))
const api = vi.hoisted(() => ({ powerfactory: vi.fn(), powerfactoryZip: vi.fn() }))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const bundle = (over: Partial<PowerFactoryBundle> = {}): PowerFactoryBundle => ({
  name: 'hub_p2030_h12', period: 2030, hour: 12, size: 4096,
  files: [{ name: 'hub_p2030_h12.dgs', size: 2048 }, { name: 'manifest.json', size: 2048 }],
  not_written: 0, pending_verification: 0, not_written_items: [], pending_items: [],
  ...over,
})

const listing = (over: Partial<PowerFactoryBundles> = {}): PowerFactoryBundles => ({
  has_run: true, has_powerfactory: true, stale: false, run_at: '2026-11-24T08:00:00+00:00',
  skipped: null, error: null, bundles: [bundle()], ...over,
})

const items = (n: number) => Array.from({ length: n }, (_, i) => ({ element: `PV ${i + 1}`, attribute: 'q_mvar', reason: 'no verified attribute' }))

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusPowerFactorySection name="Hub A" /></QueryClientProvider>)
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => { cleanup(); vi.useRealTimers() })

const PENDING = 'attribute names pending verification on first PowerFactory import'

describe('what a PowerFactory case leaves out', () => {
  it('reads nothing for a complete case', () => {
    expect(powerFactoryGaps(bundle())).toEqual([])
  })

  it('names each value by its element and quantity, never the engine key', () => {
    expect(powerFactoryValueName({ element: 'PV 1', attribute: 'q_mvar', reason: '' })).toBe('PV 1 reactive power')
    expect(powerFactoryValueName({ element: 'BESS', attribute: 'p_mw', reason: '' })).toBe('BESS active power')
    expect(powerFactoryValueName({ element: 'Shunt 2', attribute: 'x_ohm', reason: '' })).toBe('Shunt 2')
  })

  it('puts each check on first import on its own line, in plain words', () => {
    expect(powerFactoryGaps(bundle({ pending_verification: 2, pending_items: [PENDING, 'the tap changer limits'] }))).toEqual([
      'Check on first import into PowerFactory: the attribute names are not yet verified.',
      'Check on first import into PowerFactory: the tap changer limits.',
    ])
  })

  it('never reads complete when the manifest cannot be read', () => {
    const [line] = powerFactoryGaps(bundle({ not_written: null, pending_verification: null }))
    expect(line).toMatch(/manifest cannot be read/)
  })

  it('names the hour by its period and place in the year, never the bundle id', () => {
    expect(powerFactoryWhen(bundle({ period: 2030, hour: 14 }))).toBe('Period 2030 · time step 14')
  })
})

describe('the PowerFactory case section of the grid-code results', () => {
  it('offers Download PowerFactory case per bundle and saves the zip under the bundle name', async () => {
    api.powerfactory.mockResolvedValue(listing())
    api.powerfactoryZip.mockResolvedValue({ blob: new Blob(['PK']), stale: false })
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('hub_p2030_h12.zip')
    })
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Download PowerFactory case, period 2030, time step 12' }))
    await waitFor(() => expect(api.powerfactoryZip).toHaveBeenCalledWith('Hub A', 'hub_p2030_h12'))
    expect(click).toHaveBeenCalledOnce()
    expect(screen.getByTestId('pf-row-hub_p2030_h12').textContent).toBe('Period 2030 · time step 12 · 2 files · 4 kB')
    expect(screen.queryByTestId('pf-gap-hub_p2030_h12')).toBeNull()
    expect(screen.queryByTestId('pf-not-written-hub_p2030_h12')).toBeNull()
    click.mockRestore()
  })

  it('folds the values not written after five, and Show all lists every one the count names', async () => {
    api.powerfactory.mockResolvedValue(listing({ bundles: [bundle({ not_written: 7, not_written_items: items(7), pending_verification: 1, pending_items: [PENDING] })] }))
    renderSection()
    const line = await screen.findByTestId('pf-not-written-hub_p2030_h12')
    expect(line.textContent).toBe('7 values not written: PV 1 reactive power, PV 2 reactive power, PV 3 reactive power, '
      + 'PV 4 reactive power, PV 5 reactive power (2 more) Show all. See manifest.json in the zip.')
    expect(line.textContent).not.toMatch(/q_mvar|and \d+ more/)
    await userEvent.click(screen.getByTestId('pf-not-written-toggle-hub_p2030_h12'))
    const names = screen.getByTestId('pf-not-written-names-hub_p2030_h12').textContent!.split(', ')
    expect(names).toHaveLength(7)
    expect(names[6]).toBe('PV 7 reactive power')
    expect(screen.getByTestId('pf-not-written-toggle-hub_p2030_h12').textContent).toBe('Show fewer')
    expect(screen.getAllByTestId('pf-gap-hub_p2030_h12').map(p => p.textContent)).toEqual([
      'Check on first import into PowerFactory: the attribute names are not yet verified.',
    ])
  })

  it('lists up to five values with no fold', async () => {
    api.powerfactory.mockResolvedValue(listing({ bundles: [bundle({ not_written: 2, not_written_items: items(2) })] }))
    renderSection()
    expect((await screen.findByTestId('pf-not-written-hub_p2030_h12')).textContent).toBe(
      '2 values not written: PV 1 reactive power, PV 2 reactive power. See manifest.json in the zip.')
    expect(screen.queryByTestId('pf-not-written-toggle-hub_p2030_h12')).toBeNull()
  })

  it.each<[Partial<PowerFactoryBundles>, string, string | null]>([
    [{ has_run: false, bundles: [] }, 'pf-none', 'not_run'],
    [{ has_powerfactory: false, bundles: [] }, 'pf-older-run', 'not_run'],
    [{ error: 'boom', bundles: [] }, 'pf-write-error', null],
    [{ skipped: 'no island hours', bundles: [] }, 'pf-skipped', null],
    [{ stale: true }, 'pf-stale', null],
  ])('says why: %o', async (over, testid, state) => {
    api.powerfactory.mockResolvedValue(listing(over))
    renderSection()
    const el = await screen.findByTestId(testid)
    if (state) expect(el.querySelector(`[data-state="${state}"]`)).toBeTruthy()
  })

  it('links a missing run and missing island hours to the Grid study panel, mode-aware', async () => {
    api.powerfactory.mockResolvedValue(listing({ has_run: false, bundles: [] }))
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Grid study ›' }))
    expect(nav.openPanel).toHaveBeenCalledWith('scenarios', 'campusStudy')
    cleanup()
    nav.openPanel.mockClear()
    api.powerfactory.mockResolvedValue(listing({ skipped: 'no island hours', bundles: [] }))
    renderSection()
    expect((await screen.findByTestId('pf-skipped')).textContent).toMatch(/Island section of the Grid study panel/)
    await userEvent.click(screen.getByTestId('pf-grid-study-link'))
    expect(nav.openPanel).toHaveBeenCalledWith('scenarios', 'campusStudy')
  })

  it('reads an unreadable listing as not available, with no raw error', async () => {
    api.powerfactory.mockRejectedValueOnce(new Error('x'))
    renderSection()
    const el = await screen.findByTestId('pf-error')
    expect(el.querySelector('[data-state="unavailable"]')).toBeTruthy()
    expect(el.textContent).toContain('The PowerFactory cases could not be read. Try again in a moment.')
  })

  it('says a failed download in a status notice that clears', async () => {
    api.powerfactory.mockResolvedValue(listing())
    api.powerfactoryZip.mockRejectedValue(new Error('x'))
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /^Download PowerFactory case/ }))
    const notice = await screen.findByTestId('pf-download-error')
    expect(notice.textContent).toBe('The PowerFactory case could not be downloaded. Try again.')
    expect(notice.closest('[role="status"]')).toBeTruthy()
    await waitFor(() => expect(screen.queryByTestId('pf-download-error')).toBeNull(), { timeout: 9000 })
  }, 12000)
})

describe('the Grid study link', () => {
  it('opens the Grid study panel with a Back pill to Grid code & connection', async () => {
    api.powerfactory.mockResolvedValue(listing({ has_run: false, bundles: [] }))
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Grid study ›' }))
    expect(nav.withReturnFrom).toHaveBeenCalledWith(expect.any(Function), 'Grid code & connection')
    expect(nav.openPanel).toHaveBeenCalledWith('scenarios', 'campusStudy')
  })
})
