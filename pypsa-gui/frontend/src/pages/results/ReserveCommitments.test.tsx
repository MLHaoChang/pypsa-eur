// DC 06: the reserve section of Storage cycling. Up and down products draw
// one hatched band each (shared colour, different hatch) and a summary row; a
// missing solve reads "not solved", never 0; changed inputs read "out of
// date"; a 204 renders nothing.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RESERVE_SECTION, type ReservesPayload } from '../../api/reserves'
import { useUIStore } from '../../store/uiStore'
import ReserveCommitments from './ReserveCommitments'

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../../api/reserves', async () => {
  const real = await vi.importActual<typeof import('../../api/reserves')>('../../api/reserves')
  return { ...real, reservesApi: api }
})

const NOT_MODELLED = 'activation energy and its price, cycling wear and non-delivery penalties are not modelled: capacity payments only'

function reservePayload(over: Partial<ReservesPayload> = {}): ReservesPayload {
  const snapshots = ['2030-01-01T00:00:00', '2030-01-01T01:00:00', '2030-01-01T02:00:00']
  return {
    products: [
      { id: 'afrr_up', kind: 'secondary', direction: 'up', sustain_h: 1, counterparty: 'tso', price_source: 'demo' },
      { id: 'afrr_down', kind: 'secondary', direction: 'down', sustain_h: 1, counterparty: 'tso', price_source: 'user' },
    ],
    snapshots,
    storages: [{
      name: 'bess_1', string: true, floor_mwh: 4, e_cap_mwh: 10, soc_mwh: [6, 7, 5],
      committed: { afrr_up: [1, 2, 3], afrr_down: [2, 2, 2] },
    }],
    lines: [
      { period: null, contract_id: 'reserve:afrr_up', payer: 'tso', payee: 'site', value_stream: 'reserve_capacity',
        quantity_mwh: null, amount: 1200, flags: ['activation_not_valued'] },
      { period: null, contract_id: 'reserve:afrr_down', payer: 'tso', payee: 'site', value_stream: 'reserve_capacity',
        quantity_mwh: null, amount: 800, flags: ['activation_not_valued'] },
    ],
    flags: [],
    headroom_h_per_string: 1,
    currency: 'EUR',
    money_year: 2030,
    not_modelled: NOT_MODELLED,
    ...over,
  }
}

function notSolvedPayload(): ReservesPayload {
  const p = reservePayload()
  return {
    ...p,
    storages: p.storages.map(s => ({ ...s, soc_mwh: null, committed: null })),
    lines: p.lines.map(l => ({ ...l, amount: null, flags: [...l.flags, 'reserve_not_solved'] })),
    flags: ['reserve_not_solved'],
  }
}

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/ /g, ' ')

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ReserveCommitments /></QueryClientProvider>)
}

describe('ReserveCommitments', () => {
  beforeEach(() => { vi.resetAllMocks() })
  afterEach(cleanup)

  it('draws one band per up and down product, sharing a colour, and a summary row each', async () => {
    api.get.mockResolvedValue(reservePayload())
    renderIt()
    await screen.findByTestId('reserve-section')
    const up = screen.getByTestId('reserve-band-afrr_up')
    const down = screen.getByTestId('reserve-band-afrr_down')
    expect(up.getAttribute('data-direction')).toBe('up')
    expect(down.getAttribute('data-direction')).toBe('down')
    const stroke = (el: HTMLElement) => el.querySelector('rect')?.getAttribute('stroke')
    expect(stroke(up)).toBe(stroke(down))
    expect(text(screen.getByTestId('reserve-floor-legend'))).toBe('Backup floor (kept for outages): 4 MWh')
    expect(text(screen.getByTestId('reserve-row-afrr_up'))).toContain('2 · 3')
    expect(text(screen.getByTestId('reserve-revenue-afrr_up'))).toBe('1.2 k')
    expect(text(screen.getByTestId('reserve-revenue-afrr_down'))).toBe('800')
    expect(text(screen.getByTestId('reserve-source-afrr_up'))).toBe('user price seriesdemo')
    expect(text(screen.getByTestId('reserve-source-afrr_down'))).toBe('user price series')
    expect(text(screen.getByTestId('reserve-not-modelled'))).toContain(
      'Activation energy and its price, cycling wear and non-delivery penalties are not modelled')
    expect(text(screen.getByTestId('reserve-revenue-header'))).toBe('Revenue (EUR, 2030 money)')
  })

  it('shows the energy each commitment ties up between the floor and the capacity', async () => {
    const p = reservePayload()
    api.get.mockResolvedValue({ ...p, storages: p.storages.map(s => ({
      ...s, reserved_up_mwh: [1, 2, 3], reserved_down_mwh: [2, 2, 2] })) })
    renderIt()
    await screen.findByTestId('reserve-section')
    expect(text(screen.getByTestId('reserve-held-up-legend'))).toBe('Held for up-reserve')
    expect(text(screen.getByTestId('reserve-held-down-legend'))).toBe('Kept free for down-reserve')
  })

  it('draws no held-energy band without a solve', async () => {
    api.get.mockResolvedValue(notSolvedPayload())
    renderIt()
    await screen.findByTestId('reserve-section')
    expect(screen.queryByTestId('reserve-held-up-legend')).toBeNull()
    expect(screen.queryByTestId('reserve-held-down-legend')).toBeNull()
  })

  it('scrolls to the card when a link asked for it, then clears the request', async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    useUIStore.setState({ resultsSectionRequest: RESERVE_SECTION })
    api.get.mockResolvedValue(reservePayload())
    renderIt()
    await screen.findByTestId('reserve-section')
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: 'start' }))
    expect(useUIStore.getState().resultsSectionRequest).toBeNull()
  })

  it('leaves the scroll request for the final view while its host is still loading', async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    useUIStore.setState({ resultsSectionRequest: RESERVE_SECTION })
    api.get.mockResolvedValue(reservePayload())
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReserveCommitments settled={false} /></QueryClientProvider>)
    await screen.findByTestId('reserve-section')
    expect(scroll).not.toHaveBeenCalled()
    expect(useUIStore.getState().resultsSectionRequest).toBe(RESERVE_SECTION)
  })

  it('names an unknown currency or money year in the revenue header, never guesses one', async () => {
    api.get.mockResolvedValue({ ...reservePayload(), currency: null, money_year: null })
    renderIt()
    await screen.findByTestId('reserve-section')
    expect(text(screen.getByTestId('reserve-revenue-header')))
      .toBe('Revenue (currency not stated, money year not known)')
  })

  it('reads "not solved", never 0, without a solve', async () => {
    api.get.mockResolvedValue(notSolvedPayload())
    renderIt()
    await screen.findByTestId('reserve-section')
    for (const id of ['afrr_up', 'afrr_down']) {
      expect(text(screen.getByTestId(`reserve-revenue-${id}`))).toBe('not solved')
      expect(text(screen.getByTestId(`reserve-row-${id}`))).not.toMatch(/\b0\b/)
    }
    expect(screen.queryByTestId('reserve-mw-chart')).toBeNull()
    expect(document.querySelector('[data-state="not_run"]')).toBeTruthy()
  })

  it('says "out of date" when the inputs changed since the solve', async () => {
    const p = notSolvedPayload()
    api.get.mockResolvedValue({ ...p, flags: ['reserve_inputs_changed_since_solve', 'reserve_not_solved'] })
    renderIt()
    await screen.findByTestId('reserve-section')
    expect(document.querySelector('[data-state="stale"]')?.textContent).toContain('Out of date')
  })

  it('folds products beyond the palette into "Other reserve"', async () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f']
    const p = reservePayload({
      products: ids.map(id => ({ id, kind: 'tertiary' as const, direction: 'symmetric' as const, sustain_h: 1,
        counterparty: 'tso', price_source: 'user' })),
      lines: [],
    })
    p.storages[0].committed = Object.fromEntries(ids.map(id => [id, [1, 1, 1]]))
    api.get.mockResolvedValue(p)
    renderIt()
    await screen.findByTestId('reserve-section')
    expect(screen.getByTestId('reserve-band-Other reserve').textContent).toContain('e, f')
    expect(screen.queryByTestId('reserve-band-e')).toBeNull()
  })

  it('renders nothing without reserve products (204)', async () => {
    api.get.mockResolvedValue(null)
    const { container } = renderIt()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(container.innerHTML).toBe('')
  })
})
