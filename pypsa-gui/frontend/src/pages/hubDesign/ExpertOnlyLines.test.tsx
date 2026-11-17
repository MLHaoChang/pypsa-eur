// Guided names each field only Expert edits, with its value, and one
// "Open in Expert ›" per Expert place that leaves a way back (design §3.5).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowSectionId } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { ExpertOnlyLines, hasGaps } from './ExpertOnlyLines'

const api = vi.hoisted(() => ({
  snapshots: vi.fn(), periods: vi.fn(), gens: vi.fn(), links: vi.fn(), solver: vi.fn(), commercial: vi.fn(),
  finance: vi.fn(), campus: vi.fn(), codes: vi.fn(), projects: vi.fn(), schema: vi.fn(),
}))
const withReturnFrom = vi.fn()
const openFlowTarget = vi.fn()

vi.mock('../../api/network', async orig => {
  const m = await orig<typeof import('../../api/network')>()
  return { ...m, networkApi: { ...m.networkApi, getSnapshots: api.snapshots, getInvestmentPeriods: api.periods,
                               getGenerators: api.gens, getLinks: api.links } }
})
vi.mock('../../api/simulation', async orig => {
  const m = await orig<typeof import('../../api/simulation')>()
  return { ...m, simulationApi: { ...m.simulationApi, getSolverConfig: api.solver } }
})
vi.mock('../../api/commercial', async orig => {
  const m = await orig<typeof import('../../api/commercial')>()
  return { ...m, commercialApi: { ...m.commercialApi, getCommercial: api.commercial } }
})
vi.mock('../../api/businessCase', async orig => {
  const m = await orig<typeof import('../../api/businessCase')>()
  return { ...m, businessCaseApi: { ...m.businessCaseApi, getInputs: api.finance } }
})
vi.mock('../../api/campusElectrical', async orig => {
  const m = await orig<typeof import('../../api/campusElectrical')>()
  return { ...m, campusApi: { ...m.campusApi, state: api.campus, gridCodes: api.codes } }
})
vi.mock('../../api/projects', async orig => {
  const m = await orig<typeof import('../../api/projects')>()
  return { ...m, projectsApi: { ...m.projectsApi, list: api.projects } }
})
vi.mock('../../api/assetSchema', async orig => {
  const m = await orig<typeof import('../../api/assetSchema')>()
  return { ...m, assetSchemaApi: { ...m.assetSchemaApi, get: api.schema } }
})
vi.mock('../../layout/flowNavigation', async orig => {
  const m = await orig<typeof import('../../layout/flowNavigation')>()
  return {
    ...m,
    withReturnFrom: (jump: () => void) => { withReturnFrom(jump); m.withReturnFrom(jump) },
    openFlowTarget: (...a: Parameters<typeof m.openFlowTarget>) => { openFlowTarget(...a); m.openFlowTarget(...a) },
  }
})

const UC_SCHEMA = (cls: string) => ({ component: cls, groups: [{ id: 'performance', label: 'Performance', fields: [
  { key: 'committable', label: 'Unit commitment' },
  { key: 'p_min_pu', label: cls === 'Link' ? 'Minimum flow' : 'Minimum output' },
  { key: 'min_up_time', label: 'Minimum up time' },
  { key: 'min_down_time', label: 'Minimum down time' },
] }] })

/** Every reader filled in. */
function setValues() {
  api.periods.mockResolvedValue({ periods: [2030, 2040], weightings: [] })
  api.snapshots.mockResolvedValue({ count: 3, weightings: [], snapshots: [
    '2030-01-01T00:00:00', '2030-01-01T01:00:00', '2030-01-08T00:00:00'] })
  api.campus.mockResolvedValue({ settings: { profile: 'eu_rfg' }, profiles: { eu_rfg: 'EU RfG DCC' } })
  api.codes.mockResolvedValue({ published: [], drafts: [], documents: [{ id: 'd1', filename: 'code.pdf' }],
                                extraction_available: true })
  api.commercial.mockResolvedValue({
    poc_link: 'grid_import', export_link: 'grid_export', timezone: 'Europe/Berlin',
    contracts: [{ id: 'ppa1', type: 'ppa' }, { id: 'cfd1', type: 'cfd' }],
    import_tariff: { id: 't', name: 'DE C&I', jurisdiction: 'DE', valid_from: '2025', items: [] },
    value_flows: { participants: [{ id: 'a', name: 'Owner', role: 'site_owner' }, { id: 'b', name: 'Bank', role: 'lender' }] },
  })
  api.solver.mockResolvedValue({ dsr_share_of_load: 0.2, dsr_price_eur_per_mwh: 150, dsr_buses: ['b1', 'b2'] })
  api.gens.mockResolvedValue([{ name: 'gas', committable: false }])
  api.links.mockResolvedValue([{ name: 'Haber-Bosch', committable: true, p_min_pu: 0.3, min_up_time: 4,
                                 min_down_time: 2 }])
  api.schema.mockImplementation((cls: string) => Promise.resolve(UC_SCHEMA(cls)))
  api.projects.mockResolvedValue([{ name: 'Demo' }, { name: 'Demo high', parent_project: 'Demo' },
                                  { name: 'Demo low', parent_project: 'Demo' }, { name: 'Other' }])
  api.finance.mockResolvedValue({ business_case: { inputs: { lines: [
    { name: 'Compute revenue', kind: 'revenue' }, { name: 'Staff', kind: 'cost' }] },
    break_even_line: 'Compute revenue' }, digest: 'd', status: 'ok' })
}

/** Every reader empty. */
function setEmpty() {
  api.periods.mockResolvedValue({ periods: [], weightings: [] })
  api.snapshots.mockResolvedValue({ count: 0, weightings: [], snapshots: [] })
  api.campus.mockResolvedValue({ settings: null, profiles: {} })
  api.codes.mockResolvedValue({ published: [], drafts: [], documents: [], extraction_available: true })
  api.commercial.mockResolvedValue(null)
  api.solver.mockResolvedValue({ dsr_share_of_load: 0, dsr_price_eur_per_mwh: 150, dsr_buses: ['b1'] })
  api.gens.mockResolvedValue([])
  api.links.mockResolvedValue([])
  api.schema.mockImplementation((cls: string) => Promise.resolve(UC_SCHEMA(cls)))
  api.projects.mockResolvedValue([{ name: 'Demo' }, { name: 'Other' }])
  api.finance.mockResolvedValue({ business_case: null, digest: 'd', status: 'not_set' })
}

function mount(section: FlowSectionId) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ExpertOnlyLines section={section} />
  </QueryClientProvider>)
}

const lineText = (key: string) => screen.getByTestId(`expert-only-line-${key}`).textContent

async function settled(section: FlowSectionId) {
  const box = await screen.findByTestId(`expert-only-${section}`)
  await waitFor(() => expect(box.textContent).not.toContain('loading…'))
  return box
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'guided', activeSection: null, activeSlidePanel: 'hubDesign',
                        fieldDrawer: null, flowReturn: null, selectedComponent: null })
})
afterEach(cleanup)

const FILLED: Record<string, Record<string, string>> = {
  project: { investment_years: 'Investment years: 2030, 2040', representative_weeks: 'Representative weeks: sampled' },
  connection: {
    grid_code_profile: 'Grid code: EU RfG DCC', grid_code_document: 'Grid-code document: code.pdf',
    timezone: 'Site time zone: Europe/Berlin',
  },
  loads: { dsr_share_of_load: 'DSR volume: 20% of bus peak · b1, b2',
           dsr_price_eur_per_mwh: 'Demand-response price: 150 €/MWh' },
  assets: { committable: 'Unit commitment: Haber-Bosch · Minimum flow 30% · Minimum up time 4 h · Minimum down time 2 h' },
  scenarios: { variants: 'Project variants: Demo high, Demo low' },
  results: { business_case: 'Business case: Compute revenue, Staff · break-even on Compute revenue' },
}

describe('ExpertOnlyLines', () => {
  it('section 10 names only the business case: the inputs Guided section 7 edits are not named twice', async () => {
    setValues()
    mount('results')
    const box = await settled('results')
    for (const word of ['Participants', 'Contracts', 'Import tariff', 'Finance inputs']) {
      expect(box.textContent).not.toContain(word)
    }
  })

  for (const [section, lines] of Object.entries(FILLED)) {
    it(`section ${section}: each line names its field and value, one link per Expert place`, async () => {
      setValues()
      mount(section as FlowSectionId)
      const box = await settled(section as FlowSectionId)
      expect(box.querySelector('h3')?.textContent).toBe('Set in Expert')
      for (const [key, text] of Object.entries(lines)) expect(lineText(key)).toContain(text)
      // Every line here shares one Expert place: the link is on the first line only.
      const links = box.querySelectorAll('button')
      expect(links).toHaveLength(1)
      expect(links[0].getAttribute('data-testid')).toBe(`expert-only-open-${Object.keys(lines)[0]}`)
      expect(links[0].textContent).toBe('Open in Expert ›')
      expect(links[0].textContent).not.toContain('>') // the Guided link arrow is U+203A, never ASCII
      expect(box.textContent).not.toContain('—')
    })
  }

  it('says "not set" for every empty field and omits unit commitment when no unit is committable', async () => {
    setEmpty()
    const expected: Record<string, string[]> = {
      project: ['investment_years', 'representative_weeks'],
      connection: ['grid_code_profile', 'grid_code_document', 'timezone'],
      loads: ['dsr_share_of_load', 'dsr_price_eur_per_mwh'],
      scenarios: ['variants'],
      results: ['business_case'],
    }
    for (const [section, keys] of Object.entries(expected)) {
      const { unmount } = mount(section as FlowSectionId)
      const box = await settled(section as FlowSectionId)
      for (const k of keys) expect(lineText(k)?.replace('Open in Expert ›', '')).toMatch(/: not set$/)
      expect(box.textContent).not.toContain('—')
      unmount()
    }
    const { container } = mount('assets')
    await waitFor(() => expect(api.links).toHaveBeenCalled())
    await waitFor(() => expect(container.textContent).toBe(''))
  })

  it('shows "loading…" while a read runs and "could not be read" when it fails', async () => {
    setValues()
    api.solver.mockReturnValue(new Promise(() => {}))
    mount('loads')
    expect((await screen.findByTestId('expert-only-line-dsr_share_of_load')).textContent).toContain('loading…')
    cleanup()
    api.solver.mockRejectedValue(new Error('down'))
    mount('loads')
    await waitFor(() => expect(lineText('dsr_share_of_load')).toContain('DSR volume: could not be read'))
  })

  it('does not name the point of connection or export link: the section 3 card shows them', async () => {
    setValues()
    mount('connection')
    const box = await settled('connection')
    expect(box.textContent).not.toMatch(/Point of connection|Export link|Grid import/)
  })

  it('says the grid-code study does not apply, with no link, on a project that has none (409)', async () => {
    setValues()
    const conflict = Object.assign(new Error('409'), { response: { status: 409 } })
    api.campus.mockRejectedValue(conflict)
    api.codes.mockRejectedValue(conflict)
    mount('connection')
    await waitFor(() => expect(lineText('grid_code_profile')).toContain('not used by this project'))
    expect(lineText('grid_code_document')).toContain('not used by this project')
    expect(screen.queryByTestId('expert-only-open-grid_code_profile')).toBeNull()
    expect(screen.queryByText(/could not be read/)).toBeNull()
    // the site time zone still links to its Expert place, once
    expect(screen.getByTestId('expert-only-open-timezone')).toBeTruthy()
  })

  it('a chronological year reads as such', async () => {
    setValues()
    api.snapshots.mockResolvedValue({ count: 3, weightings: [], snapshots: [
      '2030-01-01T00:00:00', '2030-01-01T01:00:00', '2030-01-01T02:00:00'] })
    mount('project')
    await settled('project')
    expect(lineText('representative_weeks')).toBe('Representative weeks: no, a chronological year')
  })

  it('"Open in Expert ›" opens the place in Expert and leaves a way back to the section', async () => {
    setValues()
    useUIStore.setState({ activeSection: 'results' })
    mount('results')
    await settled('results')
    fireEvent.click(screen.getByTestId('expert-only-open-business_case'))
    expect(withReturnFrom).toHaveBeenCalledTimes(1)
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('expert')
    expect(ui.activeSlidePanel).toBe('results')
    expect(ui.resultsTabRequest).toBe('investment')
    expect(ui.flowReturn).toMatchObject({ mode: 'guided', section: 'results' })
  })

  it('opens the horizon panel and the Scenarios panel, each with a way back', async () => {
    setValues()
    for (const [section, key, panel] of [['project', 'investment_years', 'horizon'],
                                         ['scenarios', 'variants', 'scenarios'],
                                         ['loads', 'dsr_share_of_load', 'simparams']] as const) {
      useUIStore.setState({ uiMode: 'guided', activeSection: section, activeSlidePanel: 'hubDesign', flowReturn: null })
      const { unmount } = mount(section)
      await settled(section)
      fireEvent.click(screen.getByTestId(`expert-only-open-${key}`))
      expect(useUIStore.getState().activeSlidePanel).toBe(panel)
      expect(useUIStore.getState().flowReturn).toMatchObject({ mode: 'guided', section })
      unmount()
    }
  })

  it('unit commitment opens the unit in Properties', async () => {
    setValues()
    useUIStore.setState({ activeSection: 'assets' })
    mount('assets')
    await settled('assets')
    fireEvent.click(screen.getByTestId('expert-only-open-committable'))
    expect(openFlowTarget).toHaveBeenCalledWith(expect.objectContaining({
      section: 'assets', component: 'Link', entity: 'Haber-Bosch', field: 'committable' }))
    expect(useUIStore.getState().selectedComponent).toMatchObject({ type: 'Link', name: 'Haber-Bosch' })
    expect(useUIStore.getState().flowReturn).toMatchObject({ mode: 'guided', section: 'assets' })
  })

  it('renders nothing in Expert, and nothing for a section without Expert-only fields', () => {
    setValues()
    useUIStore.setState({ uiMode: 'expert' })
    const { container, unmount } = mount('connection')
    expect(container.textContent).toBe('')
    unmount()
    useUIStore.setState({ uiMode: 'guided' })
    expect(mount('carriers').container.textContent).toBe('')
  })
})

describe('hasGaps', () => {
  it('finds a skip in time within a period, not across periods', () => {
    expect(hasGaps(['2030-01-01T00:00', '2030-01-01T01:00', '2030-01-01T02:00'])).toBe(false)
    expect(hasGaps(['2030-01-01T00:00', '2030-01-01T01:00', '2030-01-08T00:00'])).toBe(true)
    expect(hasGaps(['2030-01-01T00:00', '2030-01-01T01:00', '2040-01-01T00:00', '2040-01-01T01:00'],
                   [2030, 2030, 2040, 2040])).toBe(false)
  })
})
