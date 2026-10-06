// The tariff builder (IC P3 WP3.7b): H3 round-trips exactly; a windowed tier
// item is built through the form; the bill preview renders; a server 422 is
// shown at the item it names.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { commercialApi, libraryApi } from '../../../api/commercial'
import TariffBuilder from './TariffBuilder'
import { H3 } from './tariffModel.test'
import { blankTariff } from './tariffModel'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return { ...actual,
    commercialApi: { ...actual.commercialApi, previewBilling: vi.fn(), saveCommercial: vi.fn() },
    libraryApi: { ...actual.libraryApi, putItem: vi.fn() } }
})
const api = vi.mocked(commercialApi)
const lib = vi.mocked(libraryApi)

function renderBuilder(initial = structuredClone(H3)) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><TariffBuilder initial={initial} /></QueryClientProvider>)
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('TariffBuilder', () => {
  it('saves H3 unchanged as exactly H3, inline and to the Library', async () => {
    api.saveCommercial.mockResolvedValue({} as never)
    lib.putItem.mockResolvedValue({ kind: 'tariff', id: 'h3_us_ci', version: 1, hash: 'h' })
    const { container } = renderBuilder()
    fireEvent.click(screen.getByRole('button', { name: 'Save as the project tariff' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledTimes(1))
    expect(api.saveCommercial.mock.calls[0][0]).toEqual({ import_tariff: H3, import_tariff_ref: null })
    fireEvent.click(screen.getByRole('button', { name: 'Save to Library' }))
    await waitFor(() => expect(lib.putItem).toHaveBeenCalledWith('tariff', 'h3_us_ci', H3))
    await screen.findByText(/Saved to the Library as h3_us_ci v1/)
    expectAllButtonsNamed(container)
  })

  it('builds a windowed tiered item through the form', async () => {
    api.saveCommercial.mockResolvedValue({} as never)
    renderBuilder(blankTariff())
    const item = screen.getByTestId('tb-item-0')
    fireEvent.click(within(item).getByRole('button', { name: 'Add tier to Item energy' }))
    fireEvent.click(within(item).getByRole('button', { name: 'Add tier to Item energy' }))
    fireEvent.change(within(item).getByLabelText('Item energy tier 2 threshold'), { target: { value: '100000' } })
    fireEvent.click(within(item).getByLabelText('Item energy tier rates per period'))
    const rates = within(item).getByLabelText('Item energy period 1 tier rates')
    fireEvent.change(rates, { target: { value: '0.2, 0.23' } })
    fireEvent.blur(rates)
    fireEvent.click(screen.getByRole('button', { name: 'Save as the project tariff' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalledTimes(1))
    const saved = (api.saveCommercial.mock.calls[0][0] as { import_tariff: typeof H3 }).import_tariff
    expect(saved.items[0].tiers).toEqual([{ threshold: 0, rate: 0 }, { threshold: 100000, rate: 0 }])
    expect(saved.items[0].periods).toEqual([{ name: 'all', rate: 0, tier_rates: [0.2, 0.23] }])
  })

  it('previews the bill of the draft', async () => {
    api.previewBilling.mockResolvedValue({ summary: { _: { total: 123.456, total_supported: 123.456,
      per_item: { energy: 100, facility: 23.456 } } }, flags: ['preview_dispatch_not_optimised_for_draft'],
      contracts: { lines: [], flags: [], retail: {} }, gap_summary: { gates: [], periods: {} },
      per_period: {}, gap: {}, provenance: {} })
    renderBuilder()
    fireEvent.click(screen.getByRole('button', { name: 'Preview the bill' }))
    const preview = await screen.findByTestId('tb-preview')
    expect(api.previewBilling).toHaveBeenCalledWith(H3)
    expect(preview.textContent).toContain('123.46')
    expect(preview.textContent).toContain('optimised for the attached tariff')
  })

  it('shows a 422 at the item it names', async () => {
    api.previewBilling.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: { code: 'tariff_invalid', message: 'x',
                        errors: [{ loc: ['items', 1, 'ratchet'], msg: 'exactly one of lookback_months / months' }] } } } }))
    renderBuilder()
    fireEvent.click(screen.getByRole('button', { name: 'Preview the bill' }))
    const item = screen.getByTestId('tb-item-1')
    expect((await within(item).findByRole('alert')).textContent).toContain('exactly one of')
    expect(screen.queryByTestId('tb-preview')).toBeNull()
  })
})

describe('TariffBuilder — review round 1', () => {
  const two = () => ({ ...blankTariff(), items: [{ id: 'e', kind: 'energy' as const, unit: 'per_kwh' as const,
    periods: [{ name: 'summer', rate: 0.2, months: [6, 7, 8] }, { name: 'winter', rate: 0.1, months: [1, 2] }] }] })

  it('a list field follows its row after a removal (#1)', async () => {
    api.saveCommercial.mockResolvedValue({} as never)
    renderBuilder(two())
    fireEvent.click(screen.getByRole('button', { name: 'Remove Item e period 1' }))
    const months = screen.getByLabelText('Item e period 1 months') as HTMLInputElement
    expect(months.value).toBe('1, 2')
    fireEvent.focus(months); fireEvent.blur(months)
    fireEvent.click(screen.getByRole('button', { name: 'Save as the project tariff' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    const saved = (api.saveCommercial.mock.calls[0][0] as { import_tariff: typeof H3 }).import_tariff
    expect(saved.items[0].periods).toEqual([{ name: 'winter', rate: 0.1, months: [1, 2] }])
  })

  it('names a list that does not parse and blocks preview and save (#2)', () => {
    renderBuilder(two())
    const months = screen.getByLabelText('Item e period 1 months')
    fireEvent.focus(months)
    fireEvent.change(months, { target: { value: '6, 7, x' } })
    fireEvent.blur(months)
    expect(months.getAttribute('aria-describedby')).toBeTruthy()
    expect(screen.getByTestId('tb-blocked').textContent).toContain('Item e period 1 months')
    for (const name of ['Preview the bill', 'Save to Library', 'Save as the project tariff']) {
      expect(screen.getByRole('button', { name }).hasAttribute('disabled')).toBe(true)
    }
  })

  it('shows a tariff-level 422 in the header (#3)', async () => {
    api.previewBilling.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: { code: 'tariff_invalid', message: 'x',
                        errors: [{ loc: ['import_tariff'], msg: 'tariff item ids must be unique' }] } } } }))
    renderBuilder()
    fireEvent.click(screen.getByRole('button', { name: 'Preview the bill' }))
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some(a => a.textContent?.includes('tariff: tariff item ids must be unique'))).toBe(true)
  })

  it('shows the Library 422 reason (#4)', async () => {
    lib.putItem.mockRejectedValue(Object.assign(new Error('422'), { response: { status: 422,
      data: { detail: 'invalid tariff: Value error, tariff item ids must be unique' } } }))
    renderBuilder()
    fireEvent.click(screen.getByRole('button', { name: 'Save to Library' }))
    expect((await screen.findByRole('status')).textContent).toContain('tariff item ids must be unique')
  })

  it('turning per-period tier rates off keeps the rates (#5)', async () => {
    api.saveCommercial.mockResolvedValue({} as never)
    renderBuilder({ ...blankTariff(), items: [{ id: 'e', kind: 'energy', unit: 'per_kwh',
      periods: [{ name: 'all', rate: 0 }], tiers: [{ threshold: 0, rate: 0.1 }, { threshold: 1000, rate: 0.2 }] }] })
    const box = screen.getByLabelText('Item e tier rates per period')
    fireEvent.click(box)
    fireEvent.click(box)
    fireEvent.click(screen.getByRole('button', { name: 'Save as the project tariff' }))
    await waitFor(() => expect(api.saveCommercial).toHaveBeenCalled())
    const saved = (api.saveCommercial.mock.calls[0][0] as { import_tariff: typeof H3 }).import_tariff
    expect(saved.items[0].tiers).toEqual([{ threshold: 0, rate: 0.1 }, { threshold: 1000, rate: 0.2 }])
    expect(saved.items[0].periods).toEqual([{ name: 'all', rate: 0 }])
  })

  it('a bad list stays blocking when its item is renamed (round 2 #2)', () => {
    renderBuilder(two())
    const months = screen.getByLabelText('Item e period 1 months')
    fireEvent.focus(months)
    fireEvent.change(months, { target: { value: '6, x' } })
    fireEvent.blur(months)
    fireEvent.change(screen.getByLabelText('Item e id'), { target: { value: 'e2' } })
    expect(screen.getByTestId('tb-blocked').textContent).toContain('Item e2 period 1 months')
    expect(screen.getByRole('button', { name: 'Save as the project tariff' }).hasAttribute('disabled'))
      .toBe(true)
  })

  it('one set of tier rates cannot silently replace different ones (round 2 #3)', () => {
    renderBuilder()
    const box = screen.getByLabelText('Item energy tier rates per period')
    expect(box.hasAttribute('disabled')).toBe(true)      // H3's peak and off-peak rates differ
  })
})
