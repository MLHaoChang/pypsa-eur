// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PowerFactoryBundle, PowerFactoryBundles } from '../api/campusElectrical'
import { CampusPowerFactorySection, powerFactoryGaps } from './CampusElectricalPanel'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: Object.assign((sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }), { subscribe: () => () => {} }),
}))
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
afterEach(() => cleanup())

describe('what a PowerFactory case leaves out', () => {
  it('reads nothing for a complete case', () => {
    expect(powerFactoryGaps(bundle())).toEqual([])
  })

  it('names what was not written, folded after five', () => {
    expect(powerFactoryGaps(bundle({ not_written: 7, not_written_items: items(7) }))).toEqual([
      '7 values not written: PV 1 (q_mvar), PV 2 (q_mvar), PV 3 (q_mvar), PV 4 (q_mvar), PV 5 (q_mvar) and 2 more. See manifest.json in the zip.',
    ])
    expect(powerFactoryGaps(bundle({ not_written: 1, not_written_items: [{ element: 'BESS', attribute: '', reason: '' }] }))).toEqual([
      '1 value not written: BESS. See manifest.json in the zip.',
    ])
  })

  it('says what waits for verification', () => {
    expect(powerFactoryGaps(bundle({ pending_verification: 1, pending_items: ['attribute names pending verification on first PowerFactory import'] }))).toEqual([
      'To check on first import in PowerFactory: attribute names pending verification on first PowerFactory import.',
    ])
  })

  it('never reads complete when the manifest cannot be read', () => {
    const [line] = powerFactoryGaps(bundle({ not_written: null, pending_verification: null }))
    expect(line).toMatch(/manifest cannot be read/)
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
    await userEvent.click(await screen.findByRole('button', { name: 'Download PowerFactory case, period 2030, hour 12' }))
    await waitFor(() => expect(api.powerfactoryZip).toHaveBeenCalledWith('Hub A', 'hub_p2030_h12'))
    expect(click).toHaveBeenCalledOnce()
    expect(screen.getByText('2 files, 4 kB')).toBeTruthy()
    expect(screen.queryByTestId('pf-gap-hub_p2030_h12')).toBeNull()
    click.mockRestore()
  })

  it('shows what a case leaves out beside its download', async () => {
    api.powerfactory.mockResolvedValue(listing({ bundles: [bundle({ not_written: 2, not_written_items: items(2) })] }))
    renderSection()
    expect((await screen.findByTestId('pf-gap-hub_p2030_h12')).textContent).toBe(
      '2 values not written: PV 1 (q_mvar), PV 2 (q_mvar). See manifest.json in the zip.')
  })

  it.each<[Partial<PowerFactoryBundles>, string]>([
    [{ has_run: false, bundles: [] }, 'pf-none'],
    [{ has_powerfactory: false, bundles: [] }, 'pf-older-run'],
    [{ error: 'boom', bundles: [] }, 'pf-write-error'],
    [{ skipped: 'no island hours', bundles: [] }, 'pf-skipped'],
    [{ stale: true }, 'pf-stale'],
  ])('says why: %o', async (over, testid) => {
    api.powerfactory.mockResolvedValue(listing(over))
    renderSection()
    expect(await screen.findByTestId(testid)).toBeTruthy()
  })

  it('says when the listing or a download fails', async () => {
    api.powerfactory.mockRejectedValueOnce(new Error('x'))
    renderSection()
    expect(await screen.findByTestId('pf-error')).toBeTruthy()
    cleanup()
    api.powerfactory.mockResolvedValue(listing())
    api.powerfactoryZip.mockRejectedValue(new Error('x'))
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /^Download PowerFactory case/ }))
    expect((await screen.findByTestId('pf-download-error')).textContent).toBe('The PowerFactory case could not be downloaded. Try again.')
  })
})
