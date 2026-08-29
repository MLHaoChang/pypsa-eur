// The firm-capacity (planning reserve margin) readout — Phase 8 spec §6.
//
// Every fixture below is shape-accurate to the WIRE payload, key names copied
// from `services/adequacy/report.py` (`reserve_margin_payload` +
// `sanitize_reserve_margin_payload`) rather than from the spec's sketch: the
// backend is the truth about what arrives.
//
// The tests that carry a ★ are the ones the panel exists for. Three of them
// defend distinctions the payload makes deliberately and a renderer can erase
// without any type error:
//   * `met` vs `binding` (amendment v1.2(5)) — folding one into the other
//     credits the margin for capacity that was always there;
//   * `horizon_wide` (spec §2.1) — the LP has ONE `Generator-p_nom` variable,
//     so calling a horizon-wide standard "per period" is a claim the
//     constraint does not support;
//   * `basis`/`source` on every derating row (plan §1.2) — the derates are
//     proxies, and a proxy nobody can inspect is a number nobody can check,
//     which is the whole reason this endpoint exists.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import type { ReserveMarginPayload } from '../../api/simulation'
import { ReserveMarginPanel, basisLabel, forOptimisticNote, maxAchievableText, peakHoursCell, scopeSentence } from './ReserveMarginPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: { ...actual.resultsApi, getReserveMargin: vi.fn() },
  }
})

const PAYLOAD: ReserveMarginPayload = {
  margin: 0.15,
  horizon_wide: true,
  by_period: [{
    period: 'ALL',
    peak_mw: 150,
    required_mw: 172.5,
    firm_mw: 180,
    margin_achieved: 0.2,
    // ★ met WITHOUT binding: the fixed fleet already carried the margin.
    met: true,
    binding: false,
    n_peak_hours: 1,
    peak_snapshots: ['2030-01-01 00:00:00', '2030-01-01 01:00:00'],
    max_achievable_mw: 220,
    max_achievable_unbounded: false,
  }],
  assets: [
    {
      name: 'coal_a', period: 'ALL', kind: 'generator', capacity_mw: 100,
      derate: 0.9, basis: 'EFORd', source: 'asset', extendable: false,
      firm_mw: 90, energy_limited: false,
    },
    {
      name: 'peaker', period: 'ALL', kind: 'generator', capacity_mw: 40,
      derate: 0.95, basis: 'FOR', source: 'carrier_default', extendable: true,
      firm_mw: 38, energy_limited: false,
    },
    {
      name: 'reservoir', period: 'ALL', kind: 'storage', capacity_mw: 20,
      derate: 0.98, basis: 'EFORd', source: 'carrier_default',
      extendable: false, firm_mw: 19.6, energy_limited: true,
    },
  ],
  derating_bases: { EFORd: 2, FOR: 1 },
}

/** Two periods, the second SHORT of its requirement and binding. */
const MULTI: ReserveMarginPayload = {
  ...PAYLOAD,
  horizon_wide: false,
  by_period: [
    { ...PAYLOAD.by_period[0], period: '2030' },
    {
      period: '2040', peak_mw: 200, required_mw: 230, firm_mw: 230,
      margin_achieved: 0.15, met: true, binding: true, n_peak_hours: 2,
      peak_snapshots: ['2040-01-01 00:00:00'],
      max_achievable_mw: null, max_achievable_unbounded: true,
    },
  ],
}

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // The 204 state is the DEFAULT: nothing has been solved with a margin.
  vi.mocked(resultsApi.getReserveMargin).mockReset().mockResolvedValue(null)
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><ReserveMarginPanel /></QueryClientProvider>)
}

const text = (id: string) => (screen.getByTestId(id).textContent ?? '').trim()

describe('ReserveMarginPanel — ★ it mounts in BOTH states', () => {
  it('mounts and states its empty case when the endpoint serves 204', async () => {
    renderPanel()
    expect(await screen.findByTestId('reserve-margin-panel')).toBeTruthy()
    const empty = await screen.findByTestId('reserve-margin-empty')
    // An ANSWER, not a stub: it must say what to do next.
    expect((empty.textContent ?? '').length).toBeGreaterThan(40)
    expect(empty.textContent).toMatch(/reserve_margin/)
    expect(screen.queryByTestId('reserve-margin-periods')).toBeNull()
  })

  it('mounts and drops the empty case when a margin result exists', async () => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(PAYLOAD)
    renderPanel()
    expect(await screen.findByTestId('reserve-margin-periods')).toBeTruthy()
    expect(screen.getByTestId('reserve-margin-panel')).toBeTruthy()
    expect(screen.queryByTestId('reserve-margin-empty')).toBeNull()
  })
})

describe('ReserveMarginPanel — achieved vs required', () => {
  beforeEach(() => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(PAYLOAD)
  })

  it('renders the peak, the requirement, the firm MW and the achieved margin', async () => {
    renderPanel()
    await screen.findByTestId('rm-row-ALL')
    expect(text('rm-peak-ALL')).toMatch(/150/)
    expect(text('rm-required-ALL')).toMatch(/172\.5/)
    expect(text('rm-firm-ALL')).toMatch(/180/)
    // 0.2 rendered as a margin, i.e. a percentage — the unit the user typed.
    expect(text('rm-achieved-ALL')).toMatch(/20(\.0)?\s*%/)
    // The standard itself, in the same unit.
    expect(text('reserve-margin-headline')).toMatch(/15(\.0)?\s*%/)
  })

  it('★ reports `met` and `binding` SEPARATELY — a margin the fixed fleet '
    + 'already satisfies is met and NOT binding', async () => {
    renderPanel()
    await screen.findByTestId('rm-row-ALL')
    expect(text('rm-met-ALL')).toBe('met')
    expect(text('rm-binding-ALL')).toBe('not binding')
    // And the panel says WHY the distinction is kept, in words.
    expect(text('reserve-margin-binding-note'))
      .toMatch(/capacity that was always there/i)
  })

  it('★ still says "binding" for a row that IS on the bound', async () => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(MULTI)
    renderPanel()
    await screen.findByTestId('rm-row-2040')
    expect(text('rm-met-2040')).toBe('met')
    expect(text('rm-binding-2040')).toBe('binding')
    expect(text('rm-binding-2030')).toBe('not binding')
  })

  it('renders the peak-hour timestamps and N, so the coincidence proxy is '
    + 'checkable', async () => {
    renderPanel()
    await screen.findByTestId('rm-row-ALL')
    const hours = text('rm-peak-hours-ALL')
    expect(hours).toMatch(/2030-01-01 00:00:00/)
    expect(hours).toMatch(/2030-01-01 01:00:00/)
    // N itself, not only the list: the tie rule can make the list longer.
    expect(text('rm-peak-n-ALL')).toMatch(/\b1\b/)
  })

  it('renders `max_achievable_unbounded` as "unbounded", never a blank or NaN',
    async () => {
      vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(MULTI)
      renderPanel()
      await screen.findByTestId('rm-row-2040')
      expect(text('rm-max-2040')).toMatch(/unbounded/i)
      expect(text('rm-max-2040')).not.toMatch(/NaN|null|undefined/)
      expect(text('rm-max-2030')).toMatch(/220/)
    })

  it('maxAchievableText: unbounded beats the null, and a missing number is a dash', () => {
    expect(maxAchievableText({ max_achievable_mw: null, max_achievable_unbounded: true }))
      .toMatch(/unbounded/i)
    expect(maxAchievableText({ max_achievable_mw: null, max_achievable_unbounded: false }))
      .toBe('—')
    expect(maxAchievableText({ max_achievable_mw: 220, max_achievable_unbounded: false }))
      .toMatch(/220/)
  })
})

describe('ReserveMarginPanel — ★ the horizon-wide label', () => {
  it('says plainly that a horizon_wide standard is ONE standard at the maximum '
    + 'peak, not a per-period one', async () => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(PAYLOAD)
    renderPanel()
    const scope = await screen.findByTestId('reserve-margin-scope')
    const s = scope.textContent ?? ''
    expect(s).toMatch(/horizon-wide/i)
    expect(s).toMatch(/maximum peak/i)
    expect(s).toMatch(/not a per-period/i)
    // The mechanism, so the label is checkable rather than a slogan.
    expect(s).toMatch(/single|one/i)
    expect(s).toMatch(/p_nom/)
    // It must NOT claim the per-period enforcement it does not have.
    expect(s).not.toMatch(/enforced per investment period/i)
  })

  it('claims per-period enforcement only when the payload says horizon_wide is false',
    async () => {
      vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(MULTI)
      renderPanel()
      const scope = await screen.findByTestId('reserve-margin-scope')
      const s = scope.textContent ?? ''
      expect(s).toMatch(/enforced per investment period/i)
      expect(s).not.toMatch(/horizon-wide/i)
    })

  it('scopeSentence is the pure helper both branches come from', () => {
    expect(scopeSentence(true)).toMatch(/horizon-wide/i)
    expect(scopeSentence(false)).toMatch(/per investment period/i)
    expect(scopeSentence(true)).not.toBe(scopeSentence(false))
  })
})

describe('ReserveMarginPanel — ★ the derating table is inspectable', () => {
  beforeEach(() => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(PAYLOAD)
  })

  it('renders name, kind, capacity, derate, basis, source and energy_limited '
    + 'for every credited asset', async () => {
    renderPanel()
    await screen.findByTestId('rm-asset-peaker-ALL')
    expect(text('rm-asset-kind-peaker-ALL')).toMatch(/generator/)
    expect(text('rm-asset-capacity-peaker-ALL')).toMatch(/40/)
    expect(text('rm-asset-derate-peaker-ALL')).toMatch(/0\.95/)
    // ★ The two columns a renderer can drop without any type error, and
    // without which the derate is an unaccountable number.
    expect(text('rm-asset-basis-peaker-ALL')).toBe('FOR')
    expect(text('rm-asset-source-peaker-ALL')).toMatch(/carrier_default/)
    expect(text('rm-asset-basis-coal_a-ALL')).toBe('EFORd')
    expect(text('rm-asset-source-coal_a-ALL')).toMatch(/asset/)
    // The column HEADERS too — a table whose cells exist but whose headers
    // do not is not inspectable by a reader who did not write it.
    const head = text('rm-derating-head')
    expect(head).toMatch(/basis/i)
    expect(head).toMatch(/source/i)
  })

  it('flags the energy-limited row (a reservoir takes full POWER credit while '
    + 'its energy limit is what binds it)', async () => {
    renderPanel()
    await screen.findByTestId('rm-asset-reservoir-ALL')
    expect(text('rm-asset-energy-limited-reservoir-ALL')).toMatch(/energy.?limited/i)
    expect(text('rm-asset-energy-limited-coal_a-ALL')).toBe('—')
  })

  it('names the capacity column as the BUILT capacity (amendment v1.2(7))',
    async () => {
      renderPanel()
      await screen.findByTestId('rm-derating-head')
      expect(text('rm-derating-head')).toMatch(/built/i)
    })
})

describe('ReserveMarginPanel — ★ the derating_bases roll-up', () => {
  it('renders each basis with its count and the FOR-is-optimistic note', async () => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(PAYLOAD)
    renderPanel()
    await screen.findByTestId('rm-bases')
    expect(text('rm-basis-EFORd')).toMatch(/EFORd/)
    expect(text('rm-basis-EFORd')).toMatch(/2/)
    expect(text('rm-basis-FOR')).toMatch(/FOR/)
    expect(text('rm-basis-FOR')).toMatch(/1/)
    const note = text('rm-bases-note')
    expect(note).toMatch(/optimistic/i)
    expect(note).toMatch(/reserve.?shutdown/i)
    expect(note).toMatch(/peaker/i)
  })

  it('does NOT cry FOR over an EFORd-only fleet (EFORd CONTAINS "FOR" — a '
    + 'substring test would fire on every default fleet)', async () => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue({
      ...PAYLOAD, derating_bases: { EFORd: 3 },
    })
    renderPanel()
    await screen.findByTestId('rm-bases')
    expect(screen.queryByTestId('rm-bases-note')).toBeNull()
  })

  it('forOptimisticNote is the pure predicate behind that', () => {
    expect(forOptimisticNote({ EFORd: 3 })).toBeNull()
    expect(forOptimisticNote({ FOR: 1, EFORd: 2 })).toMatch(/optimistic/i)
    expect(forOptimisticNote({ for: 1 })).toMatch(/optimistic/i)
    expect(forOptimisticNote({})).toBeNull()
  })
})

describe('ReserveMarginPanel — what the standard is NOT', () => {
  it('★ says at the point of display that a met margin is not a met '
    + 'reliability target', async () => {
    vi.mocked(resultsApi.getReserveMargin).mockResolvedValue(PAYLOAD)
    renderPanel()
    const caveat = await screen.findByTestId('reserve-margin-caveat')
    const s = caveat.textContent ?? ''
    expect(s).toMatch(/not a met reliability target/i)
    expect(s).toMatch(/proxy/i)
    expect(s).toMatch(/convention/i)
    expect(s).toMatch(/derating/i)
    expect(s).toMatch(/sampler|Monte.?Carlo/i)
  })
})

describe('ReserveMarginPanel — findings from the browser round', () => {
  it('★ summarises a long peak-hour list instead of dumping every timestamp', () => {
    // Found by rendering the panel: on a FLAT-demand network the tie rule
    // (correctly) pulls in every snapshot, so "Peak hours used" became 48
    // timestamps inline in one table cell — and on an 8760-hour horizon it
    // would be 8760. The list exists so the peak-coincidence proxy is
    // CHECKABLE; a wall of text is not checkable, it is noise. Summarise,
    // and keep the full list reachable in the title.
    //
    // Bite (verified): render `peak_snapshots.join(', ')` again.
    const many = Array.from({ length: 48 }, (_, k) => `2030-01-0${1 + Math.floor(k / 24)} ${String(k % 24).padStart(2, '0')}:00:00`)
    const text = peakHoursCell(many, 48)
    expect(text).toMatch(/48/)
    expect(text.length).toBeLessThan(120)
    expect(text).toContain(many[0])
    expect(text).toContain(many[many.length - 1])
  })

  it('keeps a short peak-hour list verbatim', () => {
    const few = ['2030-01-01 17:00:00', '2030-01-01 18:00:00']
    expect(peakHoursCell(few, 2)).toContain('17:00:00')
    expect(peakHoursCell(few, 2)).toContain('18:00:00')
  })

  it('★ labels a must-take asset\'s missing basis, never as blank data', () => {
    // Also found by rendering: a must-take unit has NO outage basis — its
    // derate is a peak-coincidence availability, not 1 - q — so the panel
    // showed "—" in the Basis column and "<blank> x 1" in the roll-up. That
    // reads as data the user forgot to enter, and would send them looking for
    // an outage rate that should not exist. Name what it actually is.
    //
    // Bite (verified): return `basis || '<blank>'`.
    expect(basisLabel('', 'missing')).toMatch(/must-take/i)
    expect(basisLabel('', 'missing')).not.toContain('blank')
    expect(basisLabel('EFORd', 'asset')).toBe('EFORd')
    expect(basisLabel('FOR', 'carrier_default')).toBe('FOR')
  })
})
