// Guided-mode spec §5.3 (Start): the three energy-hub templates from the
// wizard's own table, created through the lifted wizard mutation; the open
// template project's provenance; "Use my network" goes to the Site card.
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
import { StartCard } from './StartCard'

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
  useUIStore.setState({ currentProject: 'Data Center Energy Hub' })
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
  it('offers exactly the three energy-hub templates of the wizard table', () => {
    mount()
    const ids = TEMPLATES.filter(t => t.id.startsWith('eh_')).map(t => t.id)
    expect(ids).toEqual(['eh_datacenter', 'eh_h2_hub', 'eh_microgrid'])
    const list = screen.getByTestId('hub-start-templates')
    expect(list.querySelectorAll('[data-testid^="hub-start-template-"]').length).toBe(3)
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
})
