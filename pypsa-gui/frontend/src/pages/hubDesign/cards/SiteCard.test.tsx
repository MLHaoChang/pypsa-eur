// Guided-mode spec §5.3 (Site): readiness in words — grid connection, the
// load that must stay on, grid strength, outage data — each gap with a fix
// that hands a §5.7 request to the assistant; the one choice is the site type.
// P24-BE gate N4: on an off-grid site the (normally open) tie is not
// presented as a grid connection gap or as missing outage data.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../../api/simulation'
import { useChatStore } from '../../../store/chatStore'
import { useUIStore } from '../../../store/uiStore'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { siteFixText } from '../delegate'
import { DC_TEMPLATE, MG_TEMPLATE, readiness } from '../testFixtures'
import { SiteCard } from './SiteCard'
import { nk } from '../../../utils/queryKeys'

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReadiness: vi.fn(), getEhTemplate: vi.fn(),
      getFmeaModes: vi.fn(),
    },
  }
})

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: false })
  useChatStore.setState({ composerSeed: null, requestQueue: [], lastRequest: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true,
    step: 'site', archetype: 'weak_flexible' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
  vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness())
  vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({ per_mode: [], sweep_status: null } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><SiteCard /></QueryClientProvider>)
  return userEvent.setup()
}

const text = (id: string) => screen.getByTestId(id).textContent ?? ''

describe('SiteCard rows', () => {
  it('four rows from the readiness body, no fix buttons when nothing is missing', async () => {
    mount()
    await screen.findByTestId('hub-site-grid')
    expect(text('hub-site-grid')).toContain('grid_import (40 MW)')
    expect(text('hub-site-critical')).toContain('it_bus')
    expect(text('hub-site-strength')).toContain('data present')
    expect(text('hub-site-outage')).toContain('9 units')
    for (const k of ['grid', 'critical', 'strength', 'outage']) {
      expect(screen.queryByTestId(`hub-site-fix-${k}`)).toBeNull()
    }
    // the readiness call carries the template's recommended overrides
    expect(resultsApi.getEhReadiness).toHaveBeenCalledWith('weak_flexible', undefined, undefined,
      { stages: undefined, pack_overrides: { import_p_nom_mw: 40 } })
  })

  it('each gap has a fix button with its §5.7 request', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      import: { rule: 'eh_role', links: [], applied: false },
      import_p_nom_mw: null,
      critical_buses: [],
      scr: { status: 'not_established', note: 'no eh_sk_mva on the PoC bus', min_scr: null },
      outage_units: { count: 5, by_class: { Generator: 3, Link: 0, StorageUnit: 2 },
        missing: [{ class: 'Generator', name: 'g1' }, { class: 'Link', name: 'grid_import' }] },
    }))
    const user = mount()
    await screen.findByTestId('hub-site-fix-grid')
    expect(text('hub-site-strength')).toContain('no eh_sk_mva on the PoC bus')
    expect(text('hub-site-outage')).toContain('2 without')
    const cases: [string, string][] = [
      ['grid', siteFixText('grid')], ['critical', siteFixText('critical')],
      ['strength', siteFixText('strength')], ['outage', siteFixText('outage', 2)],
    ]
    // P25 (§5.7): a fix button SENDS its request (queued for ChatPanel).
    for (const [k, want] of cases) {
      useChatStore.setState({ composerSeed: null, requestQueue: [] })
      useUIStore.setState({ assistantDockOpen: false })
      await user.click(screen.getByTestId(`hub-site-fix-${k}`))
      expect(useChatStore.getState().requestQueue.map(r => r.text)).toEqual([want])
      expect(useChatStore.getState().composerSeed).toBeNull()
      expect(useUIStore.getState().assistantDockOpen).toBe(true)
    }
  })

  it('a strong-grid site does not need grid-strength data', async () => {
    useHubDesignStore.setState({ archetype: 'strong_grid' })
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      archetype: 'strong_grid', scr: { status: 'not_required', note: null, min_scr: null } }))
    mount()
    await screen.findByTestId('hub-site-strength')
    await waitFor(() => expect(text('hub-site-strength')).toContain('not needed'))
    expect(screen.queryByTestId('hub-site-fix-strength')).toBeNull()
  })

  it('off-grid: the open tie is not a grid connection and not missing outage data (N4)', async () => {
    useHubDesignStore.setState({ archetype: 'off_grid' })
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(MG_TEMPLATE)
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      archetype: 'off_grid',
      import: { rule: 'eh_role', links: ['subsea_tie'], applied: true },
      import_p_nom_mw: 20,
      critical_buses: ['hospital'],
      scr: { status: 'not_required', note: null, min_scr: null },
      outage_units: { count: 7, by_class: { Generator: 4, Link: 1, StorageUnit: 2 },
        missing: [{ class: 'Link', name: 'subsea_tie' }] },
    }))
    mount()
    await screen.findByTestId('hub-site-grid')
    await waitFor(() => expect(text('hub-site-grid')).toContain('island'))
    expect(text('hub-site-grid')).not.toContain('20 MW')
    expect(text('hub-site-outage')).toContain('7 units')
    expect(text('hub-site-outage')).not.toMatch(/without/)
    expect(screen.queryByTestId('hub-site-fix-grid')).toBeNull()
    expect(screen.queryByTestId('hub-site-fix-outage')).toBeNull()
  })

  it('site type select updates the store and refetches readiness for the new type', async () => {
    const user = mount()
    await screen.findByTestId('hub-site-grid')
    await user.selectOptions(screen.getByTestId('hub-site-type'), 'strong_grid')
    expect(useHubDesignStore.getState().archetype).toBe('strong_grid')
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalledWith(
      'strong_grid', undefined, undefined, { stages: undefined, pack_overrides: undefined }))
  })

  it('readiness is paused while a study runs', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running' })
    mount()
    await screen.findByTestId('hub-site-paused')
    expect(screen.getByTestId('hub-site-readiness')).toBeTruthy()
    expect(resultsApi.getEhReadiness).not.toHaveBeenCalled()
  })
})

// P26 walkthrough: after reading the Site card a first-time user had no
// forward button — only the numbered rail. "Next: Goal" moves the rail there,
// as a manual move (like a rail click).
describe('SiteCard next step', () => {
  it('"Next: Goal" moves the rail to Goal as a user move', async () => {
    const user = mount()
    await screen.findByTestId('hub-site-grid')
    const next = screen.getByTestId('hub-next-site')
    expect(next.textContent).toContain('Next: Goal')
    await user.click(next)
    expect(useHubDesignStore.getState().step).toBe('goal')
    expect(useHubDesignStore.getState().userMovedRail).toBe(true)
  })
})

// A1-FE (deferred spec 2026-09-28 §2.4): while an FMEA sweep re-solves the
// live network, P27a's backend refuses every edit (409 `study_in_flight`). The
// Site card's "Fix with the assistant" buttons — and its footer's "Let the
// assistant do this", which fixes the gaps — are disabled with one plain
// sentence instead of sending requests the backend will refuse.
describe('SiteCard while a risk check runs (A1-FE)', () => {
  const LIVE = 'A risk check is running — wait for it to finish or abort it before changing the network.'
  const gaps = () => vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
    import: { rule: 'eh_role', links: [], applied: false }, import_p_nom_mw: null,
  }))

  it('fix buttons disabled with the sentence while the sweep runs; enabled after', async () => {
    gaps()
    vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({ per_mode: [], sweep_status: 'running' } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><SiteCard /></QueryClientProvider>)
    const fix = await screen.findByTestId('hub-site-fix-grid') as HTMLButtonElement
    await waitFor(() => expect(fix.disabled).toBe(true))
    expect(fix.title).toBe(LIVE)
    const footer = screen.getByTestId('hub-delegate-site') as HTMLButtonElement
    expect(footer.disabled).toBe(true)
    expect(screen.getByTestId('hub-live-study-note').textContent).toBe(LIVE)
    await act(async () => {
      client.setQueryData(nk('Demo', 'results', 'fmea_modes'),
        { per_mode: [], sweep_status: 'done' })
    })
    await waitFor(() => expect(fix.disabled).toBe(false))
    expect(fix.title).not.toBe(LIVE)
    expect(footer.disabled).toBe(false)
    expect(screen.queryByTestId('hub-live-study-note')).toBeNull()
  })

  it('no sweep → the fix buttons are enabled and there is no note', async () => {
    gaps()
    mount()
    const fix = await screen.findByTestId('hub-site-fix-grid') as HTMLButtonElement
    await waitFor(() => expect(resultsApi.getFmeaModes).toHaveBeenCalled())
    expect(fix.disabled).toBe(false)
    expect(screen.queryByTestId('hub-live-study-note')).toBeNull()
  })
})
