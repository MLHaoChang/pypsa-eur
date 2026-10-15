// Guided-mode spec §5.3 (Start): the energy-hub templates from the
// wizard's own table, created through the lifted wizard mutation; the open
// template project's provenance; "Use my network" goes to the Site card.
// U3 (owner decision 5): beside them, "Is this investment worth it?" — the
// decision-study questions, started through the wizard's own entry hook.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { TEMPLATES } from '../../../layout/NewProjectWizard'
import { useCreateFromTemplate } from '../../../hooks/useCreateFromTemplate'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { DC_TEMPLATE } from '../testFixtures'
import { useDecisionStore } from '../../decision/decisionStore'
import { ERROR_COPY } from '../../../utils/decisionVocabulary'
import { StartCard } from './StartCard'

const authMode = vi.hoisted(() => ({ authEnabled: false }))
vi.mock('../../../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
const availability = vi.hoisted(() => vi.fn())
vi.mock('../../../api/decisionStudies', async (orig) => {
  const actual = await orig<typeof import('../../../api/decisionStudies')>()
  return { ...actual, decisionStudiesApi: { ...actual.decisionStudiesApi, availability } }
})

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getEhTemplate: vi.fn(), getEhStudy: vi.fn() } }
})
const create = { mutate: vi.fn(), isPending: false, variables: undefined as string | undefined }
vi.mock('../../../hooks/useCreateFromTemplate', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../hooks/useCreateFromTemplate')>()),
  useCreateFromTemplate: vi.fn(() => create),
}))

beforeEach(() => {
  authMode.authEnabled = false
  availability.mockReset().mockResolvedValue({ available: true })
  useDecisionStore.setState({ active: null, draft: null, view: null, watch: null })
  useUIStore.setState({ currentProject: 'Data Center Energy Hub', activeSlidePanel: 'hubDesign' })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Data Center Energy Hub',
    ready: true, step: 'start' })
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
  create.mutate.mockReset(); create.isPending = false; create.variables = undefined
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><StartCard /></QueryClientProvider>)
  return userEvent.setup()
}

describe('StartCard', () => {
  it('offers exactly the energy-hub templates of the wizard table', () => {
    mount()
    const ids = TEMPLATES.filter(t => t.id.startsWith('eh_')).map(t => t.id)
    expect(ids).toEqual(['eh_datacenter', 'eh_h2_hub', 'eh_microgrid', 'eh_datacenter_staged'])
    const list = screen.getByTestId('hub-start-templates')
    expect(list.querySelectorAll('[data-testid^="hub-start-template-"]').length).toBe(4)
    for (const t of TEMPLATES.filter(t => t.id.startsWith('eh_'))) {
      const el = screen.getByTestId(`hub-start-template-${t.id}`)
      expect(el.textContent).toContain(t.name)
      // one line of purpose — not the engine's archetype id
      expect(el.textContent).not.toMatch(/weak_flexible|strong_grid|off-grid Energy Hub study/)
    }
  })

  it('each template has its own plain one-line purpose (not the wizard blurb)', () => {
    mount()
    expect(screen.getByTestId('hub-start-template-eh_datacenter').textContent)
      .toContain('A data center that must keep running through grid outages.')
    for (const t of TEMPLATES.filter(t => t.id.startsWith('eh_'))) {
      const text = screen.getByTestId(`hub-start-template-${t.id}`).textContent ?? ''
      expect(text).not.toContain(t.description.slice(0, 30))
      for (const w of ['UPS', 'genset', 'PV', 'H2', 'offtake', 'subsea', 'MW', 'feeder']) {
        expect({ id: t.id, w, found: text.includes(w) }).toEqual({ id: t.id, w, found: false })
      }
    }
  })

  it('creating calls the lifted wizard hook with the template id', async () => {
    const user = mount()
    await user.click(screen.getByTestId('hub-start-template-eh_microgrid'))
    expect(useCreateFromTemplate).toHaveBeenCalled()
    expect(create.mutate).toHaveBeenCalledWith('eh_microgrid')
  })

  it('shows the open template project\'s provenance and first note', async () => {
    mount()
    const p = await screen.findByTestId('hub-start-provenance')
    expect(p.textContent).toContain('Data Center Energy Hub')
    expect(p.textContent).toContain('Synthetic example data — not a real site.')
    expect(p.textContent).toContain('The grid connection is capped at 40 MW.')
    expect(p.textContent).not.toContain('Second note.')
  })

  it('the provenance line carries no internal phase id', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({ ...DC_TEMPLATE,
      provenance: 'synthetic illustrative data (P19 template) — not a real site.' })
    mount()
    const p = await screen.findByTestId('hub-start-provenance')
    expect(p.textContent).toContain('synthetic illustrative data — not a real site.')
    expect(p.textContent).not.toMatch(/P\d+/)
  })

  it('while a study runs the templates are disabled, with the reason', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running' })
    mount()
    const b = screen.getByTestId('hub-start-template-eh_h2_hub') as HTMLButtonElement
    await vi.waitFor(() => expect(b.disabled).toBe(true))
    expect(b.getAttribute('title')).toBe(
      'A study is still running — wait for it to finish or abort it before switching project.')
  })

  it('"Use my network" moves the rail to Site', async () => {
    const user = mount()
    await user.click(screen.getByTestId('hub-start-own-network'))
    expect(useHubDesignStore.getState().step).toBe('site')
  })

  it('without a project, "Use my network" is not available', () => {
    useUIStore.setState({ currentProject: null })
    mount()
    expect((screen.getByTestId('hub-start-own-network') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByTestId('hub-start-provenance')).toBeNull()
  })

  it('a creation in progress marks its template and disables the others', () => {
    create.isPending = true; create.variables = 'eh_h2_hub'
    mount()
    expect(screen.getByTestId('hub-start-template-eh_h2_hub').textContent).toMatch(/creating/i)
    expect((screen.getByTestId('hub-start-template-eh_datacenter') as HTMLButtonElement).disabled).toBe(true)
  })

  it('offers both questions side by side: reliability and investment', () => {
    mount()
    expect(screen.getByTestId('hub-start-reliable').textContent).toContain('Is my site reliable?')
    expect(screen.getByTestId('hub-start-invest').textContent).toContain('Is this investment worth it?')
    expect(screen.getByTestId('hub-start-invest').contains(screen.getByTestId('hub-start-templates'))).toBe(false)
  })

  it('a decision question opens its intake in the decision panel, on the open project', async () => {
    const user = mount()
    const b = screen.getByTestId('hub-start-question-bess_at_site') as HTMLButtonElement
    expect(b.textContent).toContain('Do I need a battery at my site?')
    await vi.waitFor(() => expect(b.disabled).toBe(false))
    await user.click(b)
    expect(availability).toHaveBeenCalledWith('Data Center Energy Hub')
    expect(useDecisionStore.getState().draft).toMatchObject({ pathProject: 'Data Center Energy Hub',
      name: 'Battery at my site' })
    expect(useUIStore.getState().activeSlidePanel).toBe('decision')
  })

  it('in multi-user mode the decision question is disabled with the reason, and nothing is called', () => {
    authMode.authEnabled = true
    mount()
    expect((screen.getByTestId('hub-start-question-bess_at_site') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('hub-start-invest-reason').textContent)
      .toContain(ERROR_COPY.decision_studies_unavailable.title)
    expect(availability).not.toHaveBeenCalled()
  })

  it('without a project the decision question says what to do first', () => {
    useUIStore.setState({ currentProject: null })
    mount()
    expect((screen.getByTestId('hub-start-question-bess_at_site') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('hub-start-invest-reason').textContent).toMatch(/Create or open a project first/)
  })
})

describe('StartCard — your own site (G2)', () => {
  it("the tile opens the generic site form; the wizard's request opens it too, once", async () => {
    const { projectsApi } = await import('../../../api/projects')
    const formJson = (await import('../siteFormFixture.json')).default
    vi.spyOn(projectsApi, 'getSiteTemplateForm').mockResolvedValue(formJson as never)
    const user = mount()
    expect(screen.queryByTestId('site-form')).toBeNull()
    await user.click(screen.getByTestId('hub-start-own-site'))
    expect(await screen.findByTestId('site-form')).toBeTruthy()
    // the tile is not one of the example templates
    expect(screen.getByTestId('hub-start-templates')
      .querySelectorAll('[data-testid^="hub-start-template-"]').length).toBe(4)
    cleanup()
    useHubDesignStore.setState({ siteFormRequested: true })
    mount()
    expect(await screen.findByTestId('site-form')).toBeTruthy()
    expect(useHubDesignStore.getState().siteFormRequested).toBe(false)
  })
})
