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
import { DC_TEMPLATE, MG_TEMPLATE, SITE_TEMPLATE, readiness } from '../testFixtures'
import { SiteCard } from './SiteCard'
import { nk } from '../../../utils/queryKeys'
import { campusApi, type GridReadiness } from '../../../api/campusElectrical'
import { useScorecardOpen } from '../../../components/gridReadiness/GridReadinessScorecard'

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

vi.mock('../../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, gridReadiness: vi.fn() } }
})

beforeEach(() => {
  // No scorecard unless a test gives one: the rows above stay as they were.
  vi.mocked(campusApi.gridReadiness).mockRejectedValue(new Error('no network'))
  useScorecardOpen.setState({ open: {} })
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
    // Plain names, never the raw ids (UX S6, G1).
    expect(text('hub-site-grid')).toContain('Grid import (40 MW)')
    expect(text('hub-site-critical')).toContain('IT bus')
    expect(text('hub-site-grid')).not.toContain('grid_import')
    expect(text('hub-site-critical')).not.toContain('it_bus')
    expect(text('hub-site-strength')).toContain('data present')
    expect(text('hub-site-outage')).toContain('9 units')
    for (const k of ['grid', 'critical', 'strength', 'outage']) {
      expect(screen.queryByTestId(`hub-site-fix-${k}`)).toBeNull()
    }
    // the readiness call carries the template's recommended overrides
    expect(resultsApi.getEhReadiness).toHaveBeenCalledWith('weak_flexible', undefined, undefined,
      { stages: undefined, pack_overrides: { import_p_nom_mw: 40 } })
  })

  it('a connection energized in steps shows what is firm until it is', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      import: { rule: 'eh_role', links: ['grid_import'], applied: true },
      import_p_nom_mw: 40, import_firm_mw: 20,
    }))
    mount()
    await waitFor(() => expect(text('hub-site-grid')).toContain('20 MW until fully energized'))
    expect(text('hub-site-grid')).toContain('40 MW')
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

describe('SiteCard for a generic site (G2)', () => {
  beforeEach(() => { vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(SITE_TEMPLATE) })

  it('shows the load and connection schedule read-only, never asking for them again', async () => {
    mount()
    await screen.findByTestId('hub-site-schedule')
    expect(text('hub-site-kind')).toBe('Site type: Hospital')
    expect(text('hub-site-stage-2030')).toBe('203058')         // year | peak | available
    expect(text('hub-site-stage-2035')).toBe('2035610')
    expect(text('hub-site-connection')).toContain('Connection limit')
    expect(text('hub-site-connection')).toContain('10')
    expect(screen.getByTestId('hub-site-schedule').querySelectorAll('input')).toHaveLength(0)
    expect(text('hub-site-schedule')).toContain('power factor 0.98')
  })

  it('says the must-stay-on load as entered, not a bus name', async () => {
    mount()
    await screen.findByTestId('hub-site-critical')
    expect(text('hub-site-critical')).toContain('3 MW in 2035, 50 % of peak')
  })

  it('"Start a new project from these values" says so, and seeds the Start form', async () => {
    const user = mount()
    const btn = await screen.findByTestId('hub-site-new-from-values')
    expect(btn.textContent).toBe('Start a new project from these values')
    await (await user).click(btn)
    const st = useHubDesignStore.getState()
    expect(st.step).toBe('start')
    expect(st.siteSeed?.params.connection_mw).toBe(10)
    expect(st.siteSeed?.entered).toEqual(SITE_TEMPLATE.entered)
  })

  it('an example template shows no schedule', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
    mount()
    await screen.findByTestId('hub-site-grid')
    expect(screen.queryByTestId('hub-site-schedule')).toBeNull()
  })
})

// Owner decision D1 (UX template audit): "Site type" is the generic template's
// category, so this control is "Grid connection"; the read-out row it would
// collide with is the "Connection point". Labels only — ids unchanged.
describe('SiteCard labels', () => {
  it('names the control Grid connection, with strong / weak or limited / none', async () => {
    mount()
    await screen.findByTestId('hub-site-grid')
    const select = screen.getByTestId('hub-site-type') as HTMLSelectElement
    expect(screen.getByRole('combobox', { name: /Grid connection/ })).toBe(select)
    expect(Array.from(select.options).map(o => [o.value, o.textContent])).toEqual([
      ['strong_grid', 'Strong — import whenever needed'],
      ['weak_flexible', 'Weak or limited'],
      ['off_grid', 'None — the site runs as an island'],
    ])
    expect(screen.queryByText('Site type')).toBeNull()
  })

  it('calls the read-out row the connection point, so no two labels say Grid connection', async () => {
    mount()
    await screen.findByTestId('hub-site-grid')
    expect(text('hub-site-grid')).toMatch(/Connection point:/)
    expect(screen.getAllByText('Grid connection')).toHaveLength(1)
  })
})

// DC grid readiness 02, PR B (UX review): the scorecard in Guided, collapsed
// under the Site rows, in plain words, with no jump out of Guided.
const SCORECARD: GridReadiness = {
  schema: 1, profile: { id: 'eu_rfg_dcc_ce', title: 'EU RfG and DCC', error: null },
  template_id: 'eh_datacenter', envelope: false, basis: 'advisory',
  summary: { pass: 1, warn: 0, fail: 0, unknown: 1, assumed: 2, red_flags: 0 },
  rows: [
    { id: 'scr', requirement: 'Short-circuit ratio at the PoC', limit: 3, unit: 'SCR', source: 'rule',
      project_value: 8.3, verdict: 'pass', assumed: true, note: 'min SCR at grid', section: 'connection',
      target: { section: 'connection', panel: null, component: 'Bus', entity: 'grid', field: 'eh_sk_mva' } },
    { id: 'ramp_rate', requirement: 'Load ramp rate at the PoC', limit: 4, unit: 'MW/min', source: 'assumed',
      project_value: null, verdict: 'unknown', assumed: true, note: 'no AI-training load envelope',
      section: 'connection',
      target: { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'connection_ramp' } },
  ],
}

describe('SiteCard grid readiness', () => {
  it('a collapsed plain-words summary that opens to the scorecard, with Ask and no jump', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue(SCORECARD)
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({ ...DC_TEMPLATE, ledger: [
      { item: 'fault_level', label: 'Fault level at the PoC', value: 250, unit: 'MVA', source: 'assumed',
        field: 'eh_sk_mva', entity: 'grid', component: 'Bus', section: 'connection', panel: null }] })
    const user = mount()
    const toggle = await screen.findByTestId('hub-site-grid-readiness-toggle')
    expect(toggle.textContent).toBe('Grid readiness: 0 things to look at, 1 not yet known')
    expect(screen.queryByTestId('grid-readiness-scorecard')).toBeNull()
    await user.click(toggle)
    expect(screen.getByTestId('grid-readiness-scorecard')).toBeTruthy()
    expect(screen.queryByTestId('gr-jump-scr')).toBeNull()
    expect(screen.getByTestId('gr-ask-scr')).toBeTruthy()
    await user.click(screen.getByTestId('gr-more-scr'))
    expect(screen.getByTestId('gr-editable-scr').textContent)
      .toBe('Editable in Expert: Connection & grid › grid › Fault level at the PoC')
  })

  it('is paused while the study runs, like the Site rows', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue(SCORECARD)
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running' })
    mount()
    await screen.findByTestId('hub-site-paused')
    expect(screen.queryByTestId('hub-site-grid-readiness')).toBeNull()
  })
})
