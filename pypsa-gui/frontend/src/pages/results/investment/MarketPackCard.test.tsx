// The market-pack card (SCU06 K8a) and its "Confirm no backup obligation" list (K8b),
// on UX 3's terms: Incomplete only for a held product with no price, named in words
// with "Open here"; "Using version" / "Update available"; every cell old → new with
// Apply above the list and a fold from 100 cells; the receipt shows the cells kept as
// yours from the apply's answer; refusals as plain sentences, never a code or key.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { marketPacksApi, type MarketPackList, type MarketPackPlan, type MarketPackRow } from '../../../api/marketPacks'
import { commercialApi, libraryApi } from '../../../api/commercial'
import { networkApi } from '../../../api/network'
import type { CommercialConfig, ReserveProduct } from '../../../api/types'
import MarketPackCard, { FOLD_AT } from './MarketPackCard'
import NoBackupChecklist from './NoBackupChecklist'
import { productWords, refusalText, valueWords } from './marketPackWords'
import { AxiosError } from 'axios'
import { expectAllButtonsNamed } from '../../../test-utils/accessibleName'

vi.mock('../../../api/marketPacks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/marketPacks')>()
  return { ...actual, marketPacksApi: { list: vi.fn(), preview: vi.fn(), apply: vi.fn() } }
})
vi.mock('../../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/commercial')>()
  return {
    ...actual,
    commercialApi: { ...actual.commercialApi, saveCommercial: vi.fn(), getCommercial: vi.fn() },
    libraryApi: { ...actual.libraryApi, listSeries: vi.fn() },
  }
})
vi.mock('../../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getStorageUnits: vi.fn() } }
})
const api = vi.mocked(marketPacksApi)
const commercial = vi.mocked(commercialApi)
const library = vi.mocked(libraryApi)
const net = vi.mocked(networkApi)

const PRICE = { id: 'fcr-no1', version: 2, hash: 'hash-fcr-0000', source: 'user' }
const prod = (id: string, kind: ReserveProduct['kind'], direction: ReserveProduct['direction'],
  extra: Partial<ReserveProduct> = {}): ReserveProduct => ({
  id, kind, direction, sustain_h: 1, capacity_price: null, max_commit_mw: null, storages: [], counterparty: 'tso', ...extra,
})
const PACK = (over: Partial<MarketPackRow> = {}): MarketPackRow => ({
  id: 'no-1-2026', label: 'NO-1 2026', region: 'NO-1', currency: 'EUR', valid_year: 2026,
  pack_hash: 'abcdef0123456789', flags: [], day_ahead: null,
  products: [prod('secondary_up', 'secondary', 'up')], refusal: null, pinned: false, update_available: false, ...over,
})
const LIST = (over: Partial<MarketPackList> = {}): MarketPackList => ({
  zone: 'NO-1', currency: 'EUR', saved: true, pin_error: null, packs: [PACK()], ...over,
})
const CFG = (over: Partial<CommercialConfig> = {}): CommercialConfig => ({ poc_link: 'import', ...over }) as CommercialConfig
const PLAN = (over: Partial<MarketPackPlan> = {}): MarketPackPlan => ({
  pack_id: 'no-1-2026', pack_hash: 'fedcba9876543210', plan_digest: 'd'.repeat(64),
  changes: [{ cell: 'reserve_products/secondary_up/sustain_h', from: 1, to: 0.5 }],
  conflicts: [{ cell: 'reserve_products/secondary_up/max_commit_mw', yours: 4, pack: 2, last_applied: 2 }],
  kept: [{ cell: 'export_price_ref', reason: 'empty_ref' }],
  ...over,
})

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const out = render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
  return { ...out, rerenderWith: (n: React.ReactNode) => out.rerender(<QueryClientProvider client={qc}>{n}</QueryClientProvider>) }
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'guided', activeSlidePanel: null } as never)
  api.list.mockResolvedValue(LIST())
  commercial.getCommercial.mockResolvedValue(CFG() as never)
  api.preview.mockResolvedValue(PLAN())
  library.listSeries.mockResolvedValue([PRICE] as never)
  net.getStorageUnits.mockResolvedValue([{ name: 'bess' }, { name: 'string_a' }] as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

const noKeys = (el: HTMLElement) => {
  expect(el.textContent).not.toMatch(/reserve_products\/|reserve_no_backup_storages|market_pack_|_symmetric|secondary_up|\btso\b|Pinned/)
}

describe('MarketPackCard: the pack list', () => {
  it('badges the pack in use and an update in words, never "Pinned"', async () => {
    api.list.mockResolvedValue(LIST({ packs: [
      PACK({ pinned: true }),
      PACK({ id: 'no-2-2026', label: 'NO-2 2026', region: 'NO-2', pinned: true, update_available: true }),
      PACK({ id: 'no-3-2026', label: 'NO-3 2026', region: 'NO-3' }),
    ] }))
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    expect((await screen.findByTestId('mp-badge-no-1-2026')).textContent).toBe('Using version 2026')
    expect(screen.getByTestId('mp-badge-no-2-2026').textContent).toBe('Update available')
    expect(screen.queryByTestId('mp-badge-no-3-2026')).toBeNull()
    expect(screen.getByTestId('mp-preview-no-2-2026').textContent).toBe('See the update')
    noKeys(container)
    expectAllButtonsNamed(container)
  })

  it('shows a zone or currency refusal as its plain sentence, with no preview', async () => {
    api.list.mockResolvedValue(LIST({ packs: [PACK({ id: 'no-2-2026', label: 'NO-2 2026', region: 'NO-2',
      refusal: { code: 'market_pack_zone_mismatch', message: 'The pack is for NO-2; the project is in NO-1. Use the NO-1 pack, or change the project\'s bidding zone.' } })] }))
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    expect((await screen.findByTestId('mp-refusal-no-2-2026')).textContent).toContain('Use the NO-1 pack')
    expect(screen.queryByTestId('mp-preview-no-2-2026')).toBeNull()
    noKeys(container)
  })

  it('says what to do for an unsaved project and an unreadable pin', async () => {
    api.list.mockResolvedValue(LIST({ saved: false, pin_error: { code: 'market_pack_pin_unreadable', message: 'x' } }))
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    expect((await screen.findByTestId('mp-unsaved')).textContent).toContain('Save the project first')
    const note = screen.getByTestId('mp-pin-unreadable').textContent
    expect(note).toMatch(/Restore the project from a backup.*and reload/)
    expect(note).not.toContain('market_pack.json')
    noKeys(container)
  })

  it('shows the same card in Expert, adding the pack id and hash', async () => {
    useUIStore.setState({ uiMode: 'expert' } as never)
    api.list.mockResolvedValue(LIST({ packs: [PACK({ pinned: true })] }))
    wrap(<MarketPackCard cfg={CFG()} />)
    expect((await screen.findByTestId('mp-badge-no-1-2026')).textContent).toBe('Using version 2026')
    expect(screen.getByTestId('mp-pack-no-1-2026').textContent).toContain('no-1-2026')
    expect(screen.getByTestId('mp-card').textContent).not.toContain('abcdef01')
    expect(screen.getByTestId('mp-no-backup')).toBeTruthy()
  })
})

describe('MarketPackCard: a product with no price', () => {
  it('is Incomplete only for a product the project holds, never one a pack merely lists', async () => {
    wrap(<MarketPackCard cfg={CFG({ reserve_products: [prod('fcr', 'fast_symmetric', 'symmetric', { capacity_price: PRICE })] })} />)
    await screen.findByTestId('mp-pack-no-1-2026')
    // The listed pack's product has no price; the project's own product has one.
    expect(screen.queryByTestId('mp-incomplete')).toBeNull()
    expect(screen.queryByTestId('mp-unpriced')).toBeNull()
  })

  it('names the held product in words with "Open here" and a real Library link', async () => {
    const { container } = wrap(<MarketPackCard cfg={CFG({ reserve_products: [prod('fcr', 'fast_symmetric', 'symmetric')] })} />)
    expect((await screen.findByTestId('mp-incomplete')).textContent).toBe('Incomplete')
    const row = screen.getByTestId('mp-unpriced-fcr')
    expect(row.textContent).toContain('Fast frequency reserve (up and down) has no price')
    noKeys(container)
    fireEvent.click(within(row).getByRole('button', { name: 'Open here' }))
    fireEvent.click(within(row).getByRole('button', { name: 'Library' }))
    expect(useUIStore.getState().activeSlidePanel).toBe('library')
  })

  it('saves the picked price onto that product only', async () => {
    commercial.saveCommercial.mockResolvedValue({} as never)
    const other = prod('afrr', 'secondary', 'up', { capacity_price: PRICE })
    // Stored since the card last read the config: kept by the save.
    commercial.getCommercial.mockResolvedValue(CFG({ reserve_products: [prod('fcr', 'fast_symmetric', 'symmetric'), other] }) as never)
    wrap(<MarketPackCard cfg={CFG({ reserve_products: [prod('fcr', 'fast_symmetric', 'symmetric')] })} />)
    fireEvent.click(await screen.findByTestId('mp-unpriced-open-fcr'))
    const pick = screen.getByRole('combobox', { name: 'Capacity price for Fast frequency reserve (up and down)' })
    await waitFor(() => expect(within(pick).getAllByRole('option').length).toBeGreaterThan(1))
    fireEvent.change(pick, { target: { value: (within(pick).getAllByRole('option')[1] as HTMLOptionElement).value } })
    fireEvent.click(screen.getByTestId('mp-unpriced-save-fcr'))
    await waitFor(() => expect(commercial.saveCommercial).toHaveBeenCalledWith({ reserve_products: [
      expect.objectContaining({ id: 'fcr', capacity_price: expect.objectContaining({ id: 'fcr-no1', version: 2 }) }), other] }))
  })
})

describe('MarketPackCard: preview and apply', () => {
  it('lists every cell old → new, marks the ones kept as yours, with Apply above the list', async () => {
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    const box = await screen.findByTestId('mp-preview')
    expect((await within(box).findByTestId('mp-preview-totals')).textContent)
      .toBe('1 value to change · 1 kept as yours · 1 left as they are')
    const rows = within(box).getAllByTestId('mp-preview-row')
    expect(rows.map(r => r.textContent)).toEqual([
      'Secondary reserve (up): hours it must hold10.5',
      'Secondary reserve (up): most it may offer (MW)44Kept as yours (the pack has 2)',
    ])
    expect(rows[1].getAttribute('data-yours')).toBe('true')
    // Apply precedes the list in the document.
    const apply = within(box).getByTestId('mp-apply')
    expect(apply.compareDocumentPosition(within(box).getByTestId('mp-preview-table')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const kept = within(box).getByTestId('mp-preview-kept')
    expect(kept.textContent).toBe('Export price: the pack has no day-ahead price, so yours is left as it is.')
    noKeys(container)
    expectAllButtonsNamed(container)
  })

  it(`folds from ${FOLD_AT} cells behind "Show all", the totals and Apply still shown`, async () => {
    const many = Array.from({ length: FOLD_AT }, (_, i) => ({ cell: `reserve_products/p${i}/sustain_h`, from: 1, to: 2 }))
    api.preview.mockResolvedValue(PLAN({ changes: many, conflicts: [], kept: [] }))
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    const showAll = await screen.findByTestId('mp-preview-show-all')
    expect(screen.getAllByTestId('mp-preview-row').length).toBeLessThan(FOLD_AT)
    expect(screen.getByTestId('mp-preview-totals').textContent).toContain(`${FOLD_AT} values to change`)
    expect(screen.getByTestId('mp-apply')).toBeTruthy()
    expect(showAll.textContent).toBe(`▸ (${FOLD_AT - 20} more) Show all`)
    fireEvent.click(showAll)
    expect(screen.getAllByTestId('mp-preview-row').length).toBe(FOLD_AT)
    expect(screen.getByTestId('mp-preview-show-all').textContent).toBe('▾ Show fewer')
  })

  it('does not fold below the threshold', async () => {
    const few = Array.from({ length: FOLD_AT - 1 }, (_, i) => ({ cell: `reserve_products/p${i}/sustain_h`, from: 1, to: 2 }))
    api.preview.mockResolvedValue(PLAN({ changes: few, conflicts: [], kept: [] }))
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    await screen.findByTestId('mp-preview-table')
    expect(screen.getAllByTestId('mp-preview-row').length).toBe(FOLD_AT - 1)
    expect(screen.queryByTestId('mp-preview-show-all')).toBeNull()
  })

  it('applies with the previewed hash; the receipt keeps the conflicts and names where the results show', async () => {
    api.apply.mockResolvedValue({ ...PLAN(), changed: 1 })
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    fireEvent.click(await screen.findByTestId('mp-apply'))
    const receipt = await screen.findByTestId('mp-receipt')
    // The preview's hash and digest, not the list's hash (GK B4 P1, P2).
    expect(api.apply).toHaveBeenCalledWith('no-1-2026', 'fedcba9876543210', 'd'.repeat(64))
    expect(receipt.textContent).toContain('Applied NO-1 2026: 1 value changed.')
    expect(within(receipt).getAllByTestId('mp-preview-row').map(r => r.textContent)).toEqual([
      'Secondary reserve (up): hours it must hold10.5',
      'Secondary reserve (up): most it may offer (MW)44Kept as yours (the pack has 2)'])
    expect(receipt.textContent).toContain('Results → Storage cycling')
    expect(screen.queryByTestId('mp-preview')).toBeNull()
  })

  it('after a stale refusal fetches the plan again, keeps Apply off until it lands, then sends the new digest', async () => {
    const stale = { response: { status: 409, data: { detail: {
      error_kind: 'market_pack_stale', code: 'market_pack_stale',
      message: 'The pack or the commercial settings changed since the preview; preview it again.' } } } }
    api.apply.mockRejectedValueOnce(stale).mockResolvedValueOnce({ ...PLAN({ plan_digest: 'e'.repeat(64) }), changed: 1 })
    let land: (p: MarketPackPlan) => void = () => {}
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    await screen.findByTestId('mp-preview-table')
    api.preview.mockReturnValueOnce(new Promise<MarketPackPlan>(r => { land = r }))
    fireEvent.click(screen.getByTestId('mp-apply'))
    const note = await screen.findByTestId('mp-apply-stale')
    expect(note.textContent).toContain('Changed since you looked, so nothing was applied.')
    expect(screen.queryByTestId('mp-apply-refused')).toBeNull()
    await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(2))
    // The refused plan is still on screen while the new one loads: Apply stays off.
    expect((screen.getByTestId('mp-apply') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByTestId('mp-apply'))
    expect(api.apply).toHaveBeenCalledTimes(1)
    land(PLAN({ plan_digest: 'e'.repeat(64) }))
    await waitFor(() => expect((screen.getByTestId('mp-apply') as HTMLButtonElement).disabled).toBe(false))
    expect(screen.getByTestId('mp-apply-stale').textContent).toContain('The list below is up to date.')
    fireEvent.click(screen.getByTestId('mp-apply'))
    await screen.findByTestId('mp-receipt')
    expect(api.apply).toHaveBeenLastCalledWith('no-1-2026', 'fedcba9876543210', 'e'.repeat(64))
    noKeys(container)
  })

  it('keeps Apply off when the plan cannot be fetched again after a stale refusal', async () => {
    api.apply.mockRejectedValueOnce({ response: { status: 409, data: { detail: {
      error_kind: 'market_pack_stale', code: 'market_pack_stale', message: 'changed' } } } })
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    await screen.findByTestId('mp-preview-table')
    api.preview.mockRejectedValueOnce({ response: { status: 500, data: {} } })
    fireEvent.click(screen.getByTestId('mp-apply'))
    await screen.findByTestId('mp-preview-refused')
    expect((screen.getByTestId('mp-apply') as HTMLButtonElement).disabled).toBe(true)
    expect(api.apply).toHaveBeenCalledTimes(1)
  })

  it('shows a refused apply as a plain sentence and keeps the preview', async () => {
    api.apply.mockRejectedValue({ response: { status: 409, data: { detail: {
      error_kind: 'market_pack_needs_saved_project', code: 'market_pack_needs_saved_project',
      message: 'Save the project first: the pack pin lives beside the saved project.' } } } })
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    fireEvent.click(await screen.findByTestId('mp-apply'))
    expect((await screen.findByTestId('mp-apply-refused')).textContent)
      .toBe('Nothing was applied. Save the project first: the pack pin lives beside the saved project.')
    expect(screen.getByTestId('mp-preview')).toBeTruthy()
    noKeys(container)
  })

  it('offers no Apply when nothing would change, and says so', async () => {
    api.preview.mockResolvedValue(PLAN({ changes: [] }))
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    expect((await screen.findByTestId('mp-nothing')).textContent).toContain('Nothing to apply')
    expect(screen.queryByTestId('mp-apply')).toBeNull()
  })

  it('shows a product the pack adds as one row in words, never its fields from none to none', async () => {
    const fields = ['id', 'kind', 'direction', 'sustain_h', 'capacity_price', 'max_commit_mw', 'storages', 'counterparty']
    // The cached list says 1 h (PACK); the plan says 4 h, and the row follows the plan (P3).
    const to: Record<string, unknown> = { id: 'secondary_up', kind: 'secondary', direction: 'up', sustain_h: 4,
      capacity_price: null, max_commit_mw: null, storages: [], counterparty: 'tso' }
    const plan = PLAN({ changes: fields.map(f => ({ cell: `reserve_products/secondary_up/${f}`, from: null, to: to[f] })),
      conflicts: [], kept: [] })
    api.preview.mockResolvedValue(plan)
    api.apply.mockResolvedValue({ ...plan, changed: fields.length })
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    expect((await screen.findByTestId('mp-preview-totals')).textContent)
      .toBe('1 product to add · 0 values to change · 0 kept as yours · 0 left as they are')
    expect(screen.getAllByTestId('mp-preview-row').map(r => r.textContent)).toEqual([
      'Secondary reserve (up)not in the projectAdded: holds 4 h, any storage that may offer reserves, '
        + 'buyer the grid operator (TSO), no price yet'])
    noKeys(container)
    fireEvent.click(screen.getByTestId('mp-apply'))
    const receipt = await screen.findByTestId('mp-receipt')
    expect(receipt.textContent).toContain('Applied NO-1 2026: 1 product added, 0 values changed.')
    expect(within(receipt).getByTestId('mp-receipt-unpriced').textContent)
      .toContain('Secondary reserve (up) came in with no price')
    noKeys(container)
  })

  it('says the price is kept, not missing, when only the pack has none', async () => {
    const mine = prod('secondary_up', 'secondary', 'up', { capacity_price: PRICE })
    api.preview.mockResolvedValue(PLAN({ changes: [], conflicts: [],
      kept: [{ cell: 'reserve_products/secondary_up/capacity_price', reason: 'empty_ref' }] }))
    wrap(<MarketPackCard cfg={CFG({ reserve_products: [mine] })} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    const kept = await screen.findByTestId('mp-preview-kept')
    expect(kept.textContent).toBe('Secondary reserve (up): the pack has no capacity price, so yours is kept.')
  })

  it('names a product the user removed in words and keeps it removed', async () => {
    api.preview.mockResolvedValue(PLAN({ changes: [], kept: [], conflicts: [
      { cell: 'reserve_products/secondary_up/id', yours: null, pack: 'secondary_up', last_applied: 'secondary_up' }] }))
    const { container } = wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    const [row] = await screen.findAllByTestId('mp-preview-row')
    expect(row.textContent).toBe('Secondary reserve (up)not in the projectkept removed'
      + 'You removed it after the last apply, so it is not added again')
    noKeys(container)
  })
})

describe('NoBackupChecklist', () => {
  it('saves the toggles onto a fresh read, keeping a change saved elsewhere (GK B4 P5)', async () => {
    commercial.saveCommercial.mockResolvedValue({} as never)
    commercial.getCommercial.mockResolvedValue(CFG({ reserve_no_backup_storages: ['other_bess'] }) as never)
    wrap(<NoBackupChecklist cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-no-backup-bess'))
    fireEvent.click(screen.getByTestId('mp-no-backup-save'))
    await waitFor(() => expect(commercial.saveCommercial)
      .toHaveBeenCalledWith({ reserve_no_backup_storages: ['other_bess', 'bess'] }))
  })

  it('has a plain lead-in, shows save state, and saves the ticks', async () => {
    commercial.saveCommercial.mockResolvedValue({} as never)
    const { container } = wrap(<NoBackupChecklist cfg={CFG()} />)
    const box = screen.getByTestId('mp-no-backup')
    expect(box.textContent).toContain('Confirm no backup obligation')
    expect(box.textContent).toContain('Tick a storage only if it holds no backup energy for the site.')
    expect(screen.getByTestId('mp-no-backup-state').textContent).toBe('Saved')
    fireEvent.click(await screen.findByTestId('mp-no-backup-bess'))
    expect(screen.getByTestId('mp-no-backup-state').textContent).toBe('Unsaved changes')
    fireEvent.click(screen.getByTestId('mp-no-backup-save'))
    await waitFor(() => expect(commercial.saveCommercial).toHaveBeenCalledWith({ reserve_no_backup_storages: ['bess'] }))
    expect((await screen.findByTestId('mp-no-backup-message')).textContent).toContain('Results → Storage cycling')
    noKeys(container)
  })

  it('shows a refusal as its plain sentence and keeps the unsaved tick', async () => {
    commercial.saveCommercial.mockRejectedValue({ response: { status: 422, data: { detail: {
      code: 'reserve_attestation_refused', error_kind: 'reserve_attestation_refused',
      message: 'string_a holds the site\'s backup energy (a backup string, a spare or ride-through storage), so it cannot be marked as having no backup obligation.' } } } })
    const { container } = wrap(<NoBackupChecklist cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-no-backup-string_a'))
    fireEvent.click(screen.getByTestId('mp-no-backup-save'))
    const msg = await screen.findByTestId('mp-no-backup-message')
    expect(msg.textContent).toMatch(/^Not saved\. string_a holds the site's backup energy/)
    expect(msg.getAttribute('role')).toBe('alert')
    expect((screen.getByTestId('mp-no-backup-string_a') as HTMLInputElement).checked).toBe(true)
    expect(screen.getByTestId('mp-no-backup-state').textContent).toBe('Unsaved changes')
    noKeys(container)
  })

  it('keeps an unsaved tick when the config reloads after a pack apply', async () => {
    const view = wrap(<NoBackupChecklist cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-no-backup-bess'))
    view.rerenderWith(<NoBackupChecklist cfg={CFG({ reserve_products: [prod('fcr', 'fast_symmetric', 'symmetric')] })} />)
    expect((screen.getByTestId('mp-no-backup-bess') as HTMLInputElement).checked).toBe(true)
    expect(screen.getByTestId('mp-no-backup-state').textContent).toBe('Unsaved changes')
    fireEvent.click(screen.getByTestId('mp-no-backup-discard'))
    expect((screen.getByTestId('mp-no-backup-bess') as HTMLInputElement).checked).toBe(false)
    expect(screen.getByTestId('mp-no-backup-state').textContent).toBe('Saved')
  })
})

describe('MarketPackCard: links and plain words (UX 3 on #1938)', () => {
  it('links the receipt to Results → Storage cycling in Expert', async () => {
    useUIStore.setState({ uiMode: 'expert' } as never)
    api.apply.mockResolvedValue({ ...PLAN(), changed: 1 })
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    fireEvent.click(await screen.findByTestId('mp-apply'))
    const link = within(await screen.findByTestId('mp-receipt')).getByRole('button', { name: 'Results → Storage cycling' })
    fireEvent.click(link)
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('storage')
  })

  it('in Guided names the view as Expert and opens Results in Expert', async () => {
    api.apply.mockResolvedValue({ ...PLAN(), changed: 1 })
    wrap(<MarketPackCard cfg={CFG()} />)
    fireEvent.click(await screen.findByTestId('mp-preview-no-1-2026'))
    fireEvent.click(await screen.findByTestId('mp-apply'))
    const receipt = await screen.findByTestId('mp-receipt')
    expect(receipt.textContent).toContain('Results → Storage cycling, an Expert view')
    fireEvent.click(within(receipt).getByRole('button', { name: 'open Results in Expert' }))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('storage')
  })

  it('folds the packs for other zones under one toggle', async () => {
    api.list.mockResolvedValue(LIST({ packs: [PACK(),
      ...['NO-2', 'NO-3'].map(z => PACK({ id: z, label: z, region: z, refusal: { code: 'market_pack_zone_mismatch', message: `The pack is for ${z}.` } }))] }))
    wrap(<MarketPackCard cfg={CFG()} />)
    const other = await screen.findByTestId('mp-other-zones')
    expect(other.querySelector('summary')?.textContent).toBe('Packs for other zones or currencies (2)')
    expect(within(screen.getByTestId('mp-list')).queryByTestId('mp-pack-NO-2')).toBeNull()
  })

  it('never shows raw transport or exception text', () => {
    expect(refusalText(new AxiosError('Network Error', 'ERR_NETWORK')))
      .toBe('The server could not be reached. Check the connection, then try again.')
    expect(refusalText({ response: { status: 500, data: {} }, message: 'Request failed with status code 500' }))
      .toBe('The server could not do this. Try again; if it happens again, reload the page.')
    expect(refusalText(new Error('TypeError: x is undefined'))).not.toContain('TypeError')
    expect(refusalText({ response: { status: 422, data: { detail: [{ loc: ['body'], msg: 'bad' }] } } }))
      .not.toContain('loc')
  })

  it('reads an unknown code as a plain fallback, never the code', () => {
    expect(productWords({ kind: 'weird_kind' as never, direction: 'sideways' as never })).toBe('Reserve product')
    expect(valueWords('weird_kind', 'kind')).toBe('another kind')
    expect(valueWords('sideways', 'direction')).toBe('another direction')
    expect(valueWords('dso_x', 'counterparty')).toBe('another buyer')
    expect(valueWords('ghost_id', 'id', [])).toBe('a reserve product')
  })
})
