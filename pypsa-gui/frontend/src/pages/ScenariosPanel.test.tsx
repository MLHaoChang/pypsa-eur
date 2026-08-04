// The scenario tree's mutating affordances. Each block below pins one defect
// found in the July audit — a row that could not be branched, a branch that
// forked from stale files, a confirm prompt that read "[object Object]", a
// dead project that still looked clickable, and a lock that disabled buttons
// on projects it does not cover.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import ScenariosPanel from './ScenariosPanel'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { saveProjectQuietly, switchToProject } from '../utils/projectActions'
import { confirmToast } from '../utils/toasts'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { resetNetwork: vi.fn() } }))
vi.mock('../utils/toasts', () => ({ confirmToast: vi.fn() }))
// The panel reads the solve queue to know which projects already have a job.
// Unmocked it hits the network, and the resulting error is the FIRST toast —
// which silently displaced the assertions that read `toast.error.mock.calls[0]`.
// Mutable so a test can put a RUNNING job in the queue. The previous version
// hardcoded `{ jobs: [] }`, so `busy` was never non-empty and the
// already-queued filter had no component-level coverage at all.
let queueJobs: Array<{ id: number; project_id: string; status: string }> = []
const enqueueMock = vi.fn()
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: queueJobs } }),
  useEnqueueSolve: () => ({ mutateAsync: enqueueMock }),
}))
vi.mock('react-hot-toast', () => {
  const t = Object.assign(vi.fn(), {
    error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'tid'), dismiss: vi.fn(),
  })
  return { default: t }
})
// Partial: `evaluateMutation` and the pure helpers stay real so the read-only
// rule is exercised rather than stubbed; only the network-touching calls go.
vi.mock('../utils/projectActions', async (orig) => ({
  ...(await orig<typeof import('../utils/projectActions')>()),
  switchToProject: vi.fn(),
  saveProjectQuietly: vi.fn().mockResolvedValue(true),
  invalidateNetworkQueries: vi.fn(),
}))

const project = (over: Partial<Parameters<typeof Object>[0]> & { name: string } & Record<string, unknown>) => ({
  created_at: '2026-01-01T00:00:00',
  has_solver_config: true,
  bus_count: 5,
  snapshot_count: 24,
  objective: null,
  parent_project: null,
  scenario_description: null,
  ...over,
})

// base ─ variant          (variant is a child scenario, neither is active)
// active                  (the loaded project)
// ghost                   (registry row, files deleted in Finder)
const PROJECTS = [
  project({ name: 'loaded', id: 'id-loaded' }),
  project({ name: 'base', id: 'id-base' }),
  project({ name: 'variant', id: 'id-variant', parent_project: 'base' }),
  project({ name: 'ghost', id: 'id-ghost', missing: true }),
]

const renderPanel = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ScenariosPanel /></QueryClientProvider>)
}

/**
 * The tree row for a project, so button lookups can't cross rows.
 *
 * Matches on the ROW specifically: a selected project's name also appears in
 * the compare bar above the tree, so a bare `findByText` finds two elements
 * as soon as anything is ticked.
 */
const rowFor = async (name: string): Promise<HTMLElement> => {
  await screen.findAllByText(name)
  const row = screen.getAllByText(name)
    .map(el => el.closest('.group'))
    .find(Boolean)
  if (!row) throw new Error(`no tree row for '${name}'`)
  return row as HTMLElement
}

const branchFrom = async (name: string) => {
  const row = await rowFor(name)
  const btn = within(row).getByTitle(/^Branch a child scenario/)
  await userEvent.click(btn)
  return screen.findByRole('dialog')
}

const fillAndCreate = async (dlg: HTMLElement, newName: string) => {
  await userEvent.type(within(dlg).getByPlaceholderText('e.g. high-renewables'), newName)
  await userEvent.click(within(dlg).getByRole('button', { name: 'Create' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(projectsApi.list).mockResolvedValue(PROJECTS as never)
  vi.mocked(saveProjectQuietly).mockResolvedValue(true)
  vi.mocked(projectsApi.createScenario).mockResolvedValue({ name: 'child' } as never)
  vi.mocked(projectsApi.delete).mockResolvedValue({ deleted: [], failed: [] } as never)
  vi.mocked(switchToProject).mockResolvedValue({ status: 'switched' } as never)
  useUIStore.setState({ currentProject: 'loaded', readOnly: false })
  queueJobs = []
  enqueueMock.mockReset().mockResolvedValue({})
})

describe('branching from a row that is not the active project', () => {
  it('offers the branch action on every row, not only the active one', async () => {
    // The regression. The button was disabled everywhere except the active
    // row, on the strength of a comment saying the backend 409s a non-active
    // base. It does not — `_create_scenario_db` copies the base's own on-disk
    // bundle precisely so it need not be loaded first.
    renderPanel()
    const row = await rowFor('base')
    expect(within(row).getByTitle(/^Branch a child scenario/)).toHaveProperty('disabled', false)
  })

  it('creates the scenario against the row that was clicked', async () => {
    renderPanel()
    const dlg = await branchFrom('base')
    await fillAndCreate(dlg, 'from_base')
    await waitFor(() => expect(vi.mocked(projectsApi.createScenario))
      // No description typed -> null, not ''. The category is its own arg.
      .toHaveBeenCalledWith('id-base', 'from_base', null, 'scenario'))
  })

  it('does not save a project the backend is not holding in memory', async () => {
    // Only the ACTIVE project can be ahead of its files. Saving any other row
    // would be an unasked-for write to a project the user is not editing.
    renderPanel()
    const dlg = await branchFrom('base')
    await fillAndCreate(dlg, 'from_base')
    await waitFor(() => expect(vi.mocked(projectsApi.createScenario)).toHaveBeenCalled())
    expect(vi.mocked(saveProjectQuietly)).not.toHaveBeenCalled()
  })
})

describe('branching the active project captures what is on screen', () => {
  it('saves the base before copying it', async () => {
    // `POST /{base}/scenarios` copies the base's on-DISK bundle. Without this
    // flush the child forks from the last autosave — up to five minutes of
    // edits missing, silently, until the branch is opened much later.
    renderPanel()
    const dlg = await branchFrom('loaded')
    await fillAndCreate(dlg, 'from_loaded')
    await waitFor(() => expect(vi.mocked(projectsApi.createScenario)).toHaveBeenCalled())
    expect(vi.mocked(saveProjectQuietly)).toHaveBeenCalledWith('loaded')
    // Order matters: a copy taken before the flush is the bug itself.
    const savedAt = vi.mocked(saveProjectQuietly).mock.invocationCallOrder[0]
    const createdAt = vi.mocked(projectsApi.createScenario).mock.invocationCallOrder[0]
    expect(savedAt).toBeLessThan(createdAt)
  })

  it('refuses to branch at all when that save fails', async () => {
    // saveProjectQuietly returns false rather than throwing — notably while a
    // solve is running. Proceeding anyway would silently produce the stale
    // fork this flush exists to prevent.
    vi.mocked(saveProjectQuietly).mockResolvedValue(false)
    renderPanel()
    const dlg = await branchFrom('loaded')
    await fillAndCreate(dlg, 'doomed')
    await waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalled())
    expect(vi.mocked(projectsApi.createScenario)).not.toHaveBeenCalled()
    expect(String(vi.mocked(toast.error).mock.calls[0][0])).toMatch(/last saved state/i)
  })
})

describe('deleting a project that still has children', () => {
  it('asks in words, naming the children', async () => {
    // The backend refuses with a STRUCTURED detail; the panel interpolated
    // that object into a template literal and asked the user to confirm
    // "[object Object] — delete it and all its child scenarios?".
    vi.mocked(projectsApi.delete).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { error_kind: 'descendants_exist', message: 'Pass ?cascade=true …', descendants: ['variant'] } },
      },
    })
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle('Delete this scenario'))
    await waitFor(() => expect(vi.mocked(confirmToast)).toHaveBeenCalled())
    const prompt = vi.mocked(confirmToast).mock.calls[0][0]
    expect(prompt).not.toContain('[object Object]')
    expect(prompt).toContain('variant')
    // The backend's own message ends with an API instruction. Never shown.
    expect(prompt).not.toContain('cascade=true')
  })

  it('retries with cascade when the user confirms', async () => {
    vi.mocked(projectsApi.delete).mockRejectedValueOnce({
      response: { status: 409, data: { detail: { descendants: ['variant'] } } },
    })
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle('Delete this scenario'))
    await waitFor(() => expect(vi.mocked(confirmToast)).toHaveBeenCalled())
    vi.mocked(projectsApi.delete).mockResolvedValue({ deleted: ['base', 'variant'], failed: [] } as never)
    await vi.mocked(confirmToast).mock.calls[0][1]()
    await waitFor(() => expect(vi.mocked(projectsApi.delete)).toHaveBeenCalledWith('id-base', true))
  })
})

describe('a project whose files are gone', () => {
  it('cannot be opened or branched, and says so', async () => {
    renderPanel()
    const row = await rowFor('ghost')
    expect(within(row).getByText('files missing')).toBeTruthy()
    expect(within(row).getByTitle(/no longer on disk — it cannot be opened/))
      .toHaveProperty('disabled', true)
    expect(within(row).getByTitle(/no longer on disk — there is nothing to branch/))
      .toHaveProperty('disabled', true)
  })

  it('can still be deleted — clearing the stale row is the point', async () => {
    renderPanel()
    const row = await rowFor('ghost')
    expect(within(row).getByTitle('Delete this scenario')).toHaveProperty('disabled', false)
  })
})

describe('the scenario category is a real field', () => {
  it('badges the row from scenario_type, not from the description', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({
        name: 'typed', id: 'id-typed',
        scenario_type: 'stress', scenario_description: 'cold winter',
      }),
    ] as never)
    renderPanel()
    const row = await rowFor('typed')
    expect(within(row).getByText('stress')).toBeTruthy()
    expect(within(row).getByText('cold winter')).toBeTruthy()
  })

  it('still decodes a legacy tag for a bundle the backend has not split', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({ name: 'old', id: 'id-old', scenario_description: '[baseline] ref run' }),
    ] as never)
    renderPanel()
    const row = await rowFor('old')
    expect(within(row).getByText('baseline')).toBeTruthy()
    // And the marker itself is never shown as prose.
    expect(within(row).getByText('ref run')).toBeTruthy()
    expect(row.textContent).not.toContain('[baseline]')
  })

  it('sends the category as its own field when branching', async () => {
    renderPanel()
    const dlg = await branchFrom('base')
    await userEvent.selectOptions(within(dlg).getByRole('combobox'), 'stress')
    await userEvent.type(
      within(dlg).getByPlaceholderText("What's different from the base?"), 'no imports',
    )
    await fillAndCreate(dlg, 'variant_a')
    await waitFor(() => expect(vi.mocked(projectsApi.createScenario))
      .toHaveBeenCalledWith('id-base', 'variant_a', 'no imports', 'stress'))
  })
})

describe('editing a scenario after it was created', () => {
  it('saves the new category and description', async () => {
    // Both were write-once: the description was set in the branch dialog and
    // never again, and the category lived as a `[type]` prefix inside it.
    vi.mocked(projectsApi.updateScenario).mockResolvedValue({ name: 'base' } as never)
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    await userEvent.selectOptions(within(dlg).getByRole('combobox'), 'baseline')
    await userEvent.type(within(dlg).getByRole('textbox'), 'the reference run')
    await userEvent.click(within(dlg).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(vi.mocked(projectsApi.updateScenario))
      .toHaveBeenCalledWith('id-base', {
        description: 'the reference run', scenario_type: 'baseline',
      }))
  })

  it('opens with the project’s current values', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({
        name: 'typed', id: 'id-typed',
        scenario_type: 'stress', scenario_description: 'cold winter',
      }),
    ] as never)
    renderPanel()
    const row = await rowFor('typed')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    expect((within(dlg).getByRole('combobox') as HTMLSelectElement).value).toBe('stress')
    expect((within(dlg).getByRole('textbox') as HTMLTextAreaElement).value).toBe('cold winter')
  })

  it('keeps Save disabled until something actually changes', async () => {
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    expect(within(dlg).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true)
    await userEvent.type(within(dlg).getByRole('textbox'), 'x')
    expect(within(dlg).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', false)
  })

  it('clears the description with null rather than an empty string', async () => {
    // The route treats an absent key as "leave alone" and null as "clear".
    // Sending '' would store an empty string that every reader then has to
    // treat as if it were missing.
    vi.mocked(projectsApi.updateScenario).mockResolvedValue({ name: 'typed' } as never)
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({
        name: 'typed', id: 'id-typed',
        scenario_type: 'stress', scenario_description: 'cold winter',
      }),
    ] as never)
    renderPanel()
    const row = await rowFor('typed')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    await userEvent.clear(within(dlg).getByRole('textbox'))
    await userEvent.click(within(dlg).getByRole('button', { name: 'Save' }))
    // Only the cleared field — the category was not touched, so its key is
    // absent and the route leaves it alone.
    await waitFor(() => expect(vi.mocked(projectsApi.updateScenario))
      .toHaveBeenCalledWith('id-typed', { description: null }))
  })

  it('opens uncategorised as uncategorised, not pre-set to a guess', async () => {
    // `base` has no category. Defaulting the select to 'scenario' would show
    // the user a value they never chose and make Save look meaningful.
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    expect((within(dlg).getByRole('combobox') as HTMLSelectElement).value).toBe('')
  })

  it('can take a category back off again', async () => {
    // The column is nullable and the route accepts null, but a select
    // offering only the three categories could never SEND that — every
    // project passing through the dialog would come out categorised for good.
    vi.mocked(projectsApi.updateScenario).mockResolvedValue({ name: 'typed' } as never)
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({
        name: 'typed', id: 'id-typed',
        scenario_type: 'stress', scenario_description: 'cold winter',
      }),
    ] as never)
    renderPanel()
    const row = await rowFor('typed')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    await userEvent.selectOptions(within(dlg).getByRole('combobox'), '')
    await userEvent.click(within(dlg).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(vi.mocked(projectsApi.updateScenario))
      .toHaveBeenCalledWith('id-typed', { scenario_type: null }))
  })

  it('sends only the field that changed, not both', async () => {
    // The route is partial: an absent key means "leave alone". Sending both
    // unconditionally is what made the next test's bug possible.
    vi.mocked(projectsApi.updateScenario).mockResolvedValue({ name: 'base' } as never)
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    await userEvent.type(within(dlg).getByRole('textbox'), 'only the text')
    await userEvent.click(within(dlg).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(vi.mocked(projectsApi.updateScenario))
      .toHaveBeenCalledWith('id-base', { description: 'only the text' }))
  })

  it('does not wipe a category it cannot display', async () => {
    // A category this build does not recognise — added server-side, or
    // written by an importer — resolves to `type: null`, so the select opens
    // on "— none —". Sending the select's value regardless would clear a
    // category the user never touched and could not see.
    vi.mocked(projectsApi.updateScenario).mockResolvedValue({ name: 'exotic' } as never)
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({
        name: 'exotic', id: 'id-exotic',
        scenario_type: 'sensitivity', scenario_description: 'keep my category',
      }),
    ] as never)
    renderPanel()
    const row = await rowFor('exotic')
    await userEvent.click(within(row).getByTitle(/^Edit this scenario/))
    const dlg = await screen.findByRole('dialog')
    expect((within(dlg).getByRole('combobox') as HTMLSelectElement).value).toBe('')
    await userEvent.clear(within(dlg).getByRole('textbox'))
    await userEvent.type(within(dlg).getByRole('textbox'), 'edited text')
    await userEvent.click(within(dlg).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(vi.mocked(projectsApi.updateScenario)).toHaveBeenCalled())
    const [, patch] = vi.mocked(projectsApi.updateScenario).mock.calls[0]
    expect(patch).toEqual({ description: 'edited text' })
    expect(patch).not.toHaveProperty('scenario_type')
  })

  it('is offered even when the project’s files are gone', async () => {
    // The label lives in the registry row, which is exactly what is still
    // there — and renaming what you can see is how you make sense of a list
    // you are about to clean up.
    renderPanel()
    const row = await rowFor('ghost')
    expect(within(row).getByTitle(/^Edit this scenario/)).toHaveProperty('disabled', false)
  })
})

// ── Stage 3: the tree as a comparison surface ───────────────────────────────

const SOLVED_TREE = [
  project({ name: 'loaded', id: 'id-loaded' }),
  project({ name: 'base', id: 'id-base', objective: 1_000_000, bus_count: 5 }),
  project({
    name: 'cheaper', id: 'id-cheaper', parent_project: 'base',
    objective: 900_000, bus_count: 6,
  }),
  project({
    name: 'longer', id: 'id-longer', parent_project: 'base',
    objective: 4_000_000, bus_count: 5, snapshot_count: 96,
  }),
]

describe('difference from the parent, shown inline', () => {
  it('reads the objective change as a percentage on the child row', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue(SOLVED_TREE as never)
    renderPanel()
    const row = await rowFor('cheaper')
    expect(within(row).getByText('−10.00%')).toBeTruthy()
    expect(within(row).getByText('+1 bus')).toBeTruthy()
  })

  it('shows nothing on a root — there is no parent to differ from', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue(SOLVED_TREE as never)
    renderPanel()
    const row = await rowFor('base')
    expect(within(row).queryByText(/%$/)).toBeNull()
  })

  it('marks a differing horizon rather than reporting a meaningless number', async () => {
    // 'longer' covers 96 snapshots against its parent's 24, so the objectives
    // are sums over different amounts of time. The difference is an artefact.
    vi.mocked(projectsApi.list).mockResolvedValue(SOLVED_TREE as never)
    renderPanel()
    const row = await rowFor('longer')
    const chip = within(row).getByText(/300\.00%/)
    expect(chip.textContent).toContain('≠')
    expect(chip.getAttribute('title')).toMatch(/not a like-for-like/i)
  })

  it('colours cheaper green and dearer red, not the reverse', async () => {
    // Inverting the two passed every other assertion here.
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({ name: 'base', id: 'id-base', objective: 1_000_000 }),
      project({ name: 'cheap', id: 'id-c', parent_project: 'base', objective: 900_000 }),
      project({ name: 'dear', id: 'id-d', parent_project: 'base', objective: 1_100_000 }),
    ] as never)
    renderPanel()
    expect(within(await rowFor('cheap')).getByText('−10.00%').className).toContain('text-ok')
    expect(within(await rowFor('dear')).getByText('+10.00%').className).toContain('text-danger')
  })

  it('shows the absolute difference when the parent solved to exactly zero', async () => {
    // There is no percentage against a zero baseline, but there IS a
    // difference — and requiring both hid a real one entirely.
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({ name: 'base', id: 'id-base', objective: 0 }),
      project({ name: 'kid', id: 'id-kid', parent_project: 'base', objective: 5e9 }),
    ] as never)
    renderPanel()
    expect(within(await rowFor('kid')).getByText(/\+€5\.00B/)).toBeTruthy()
  })

  it('does not claim an improvement that rounds to nothing', async () => {
    // Floating-point noise between two near-identical re-solves rendered
    // "−0.00%" in green with the tooltip "lower by €0".
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({ name: 'base', id: 'id-base', objective: 1_000_000 }),
      project({ name: 'same', id: 'id-s', parent_project: 'base', objective: 1_000_000.001 }),
    ] as never)
    renderPanel()
    const chip = within(await rowFor('same')).getByText('≈0%')
    expect(chip.className).toContain('text-muted')
    expect(chip.className).not.toContain('text-ok')
  })

  it('says nothing about the objective when the child is unsolved', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([
      project({ name: 'loaded', id: 'id-loaded' }),
      project({ name: 'base', id: 'id-base', objective: 1_000_000 }),
      project({ name: 'todo', id: 'id-todo', parent_project: 'base', objective: null }),
    ] as never)
    renderPanel()
    const row = await rowFor('todo')
    expect(within(row).queryByText(/%/)).toBeNull()
  })
})

describe('picking two rows to compare', () => {
  it('routes the pair into the compare view', async () => {
    const requestCompareNav = vi.fn()
    useUIStore.setState({ currentProject: 'loaded', readOnly: false, requestCompareNav })
    renderPanel()
    const a = await rowFor('base')
    await userEvent.click(within(a).getByRole('checkbox'))
    const b = await rowFor('variant')
    await userEvent.click(within(b).getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: /Compare these two/ }))
    expect(requestCompareNav).toHaveBeenCalledWith({ a: 'base', b: 'variant' })
  })

  it('keeps only the last two picks rather than ignoring the third', async () => {
    // A checkbox that silently does nothing reads as broken. Dropping the
    // oldest keeps every click meaningful.
    renderPanel()
    for (const name of ['base', 'variant', 'loaded']) {
      const row = await rowFor(name)
      await userEvent.click(within(row).getByRole('checkbox'))
    }
    expect((within(await rowFor('base')).getByRole('checkbox') as HTMLInputElement).checked)
      .toBe(false)
    expect((within(await rowFor('variant')).getByRole('checkbox') as HTMLInputElement).checked)
      .toBe(true)
    expect((within(await rowFor('loaded')).getByRole('checkbox') as HTMLInputElement).checked)
      .toBe(true)
  })

  it('drops a selection whose project disappears from the list', async () => {
    // Both session sets are keyed by NAME against a list that refetches every
    // ten seconds. Nothing reconciled them, so a deleted project stayed
    // ticked and the compare bar kept offering it.
    // Same mounted panel throughout — a remount would reset the state and the
    // test would pass without the prune existing at all.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ScenariosPanel /></QueryClientProvider>)
    const row = await rowFor('variant')
    await userEvent.click(within(row).getByRole('checkbox'))
    expect(screen.getByText(/Tick one more/)).toBeTruthy()

    vi.mocked(projectsApi.list).mockResolvedValue(
      PROJECTS.filter(p => p.name !== 'variant') as never,
    )
    await qc.invalidateQueries({ queryKey: ['projects'] })
    await waitFor(() => expect(screen.queryByText('variant')).toBeNull())
    expect(screen.queryByText(/Tick one more/)).toBeNull()
  })

  it('cannot select a project whose files are gone', async () => {
    // /compare-state 404s without a network.nc, so comparing one lands on an
    // error banner. Switch and branch already refuse it.
    renderPanel()
    const row = await rowFor('ghost')
    expect(within(row).getByRole('checkbox')).toHaveProperty('disabled', true)
  })

  it('the compare bar can drop a pick again', async () => {
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: /Remove base/ }))
    expect(screen.queryByText(/Tick one more/)).toBeNull()
    expect((within(await rowFor('base')).getByRole('checkbox') as HTMLInputElement).checked)
      .toBe(false)
  })

  it('the section Compare button uses the ticked pair', async () => {
    const requestCompareNav = vi.fn()
    useUIStore.setState({ currentProject: 'loaded', readOnly: false, requestCompareNav })
    renderPanel()
    for (const name of ['base', 'variant']) {
      await userEvent.click(within(await rowFor(name)).getByRole('checkbox'))
    }
    await userEvent.click(screen.getByRole('button', { name: /Compare selected/ }))
    expect(requestCompareNav).toHaveBeenCalledWith({ a: 'base', b: 'variant' })
  })

  it('asks for a second pick before offering to compare', async () => {
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByRole('checkbox'))
    expect(screen.getByText(/Tick one more/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Compare these two/ })).toBeNull()
  })
})

describe('collapsing a branch', () => {
  it('hides the children and can bring them back', async () => {
    renderPanel()
    expect(await screen.findByText('variant')).toBeTruthy()
    const row = await rowFor('base')
    const chevron = within(row).getByTitle(/Hide this branch/)
    await userEvent.click(chevron)
    expect(screen.queryByText('variant')).toBeNull()
    await userEvent.click(within(await rowFor('base')).getByTitle(/Show the 1 scenario/))
    expect(await screen.findByText('variant')).toBeTruthy()
  })

  it('reports its state to assistive tech, matching what is on screen', async () => {
    // `aria-expanded` inverted passed every other test here.
    renderPanel()
    const row = await rowFor('base')
    const chevron = within(row).getByRole('button', { name: /Hide the scenarios under base/ })
    expect(chevron.getAttribute('aria-expanded')).toBe('true')
    await userEvent.click(chevron)
    expect(
      within(await rowFor('base'))
        .getByRole('button', { name: /Show the 1 scenario/ })
        .getAttribute('aria-expanded'),
    ).toBe('false')
  })

  it('offers no chevron on a leaf', async () => {
    renderPanel()
    const row = await rowFor('variant')
    expect(within(row).queryByTitle(/Hide this branch|Show the/)).toBeNull()
  })
})

describe('queueing a whole branch to solve', () => {
  it('asks before queueing, naming what it will queue', async () => {
    renderPanel()
    const row = await rowFor('base')
    await userEvent.click(within(row).getByTitle(/Queue this project and its/))
    await waitFor(() => expect(vi.mocked(confirmToast)).toHaveBeenCalled())
    const prompt = vi.mocked(confirmToast).mock.calls[0][0]
    expect(prompt).toContain('base')
    expect(prompt).toContain('variant')
    expect(prompt).toMatch(/Queue 2 solves/)
  })

  it('is offered only where it means something', async () => {
    // On a leaf this is just "solve", which the header and the queue panel
    // already do.
    renderPanel()
    const row = await rowFor('variant')
    expect(within(row).queryByTitle(/Queue this project/)).toBeNull()
  })

  // Everything below runs the CONFIRM CALLBACK. A QA pass mutation-tested the
  // handler and found twelve survivors — including deleting the enqueue loop
  // outright — because no test had ever invoked it. The delete tests already
  // used this idiom; the queue tests did not.
  const confirmQueue = async (rowName: string) => {
    const row = await rowFor(rowName)
    await userEvent.click(within(row).getByTitle(/Queue this project and its/))
    await waitFor(() => expect(vi.mocked(confirmToast)).toHaveBeenCalled())
    const calls = vi.mocked(confirmToast).mock.calls
    await calls[calls.length - 1][1]()
  }

  it('enqueues every project in the branch, in order', async () => {
    renderPanel()
    await confirmQueue('base')
    expect(enqueueMock.mock.calls.map(c => c[0])).toEqual(['id-base', 'id-variant'])
    expect(vi.mocked(toast.success)).toHaveBeenCalledWith('Queued 2 solves')
  })

  it('flushes the active project before queueing it', async () => {
    // The dispatcher solves the SAVED file. Queue first and the job runs
    // against whatever was on disk before the user's edits.
    useUIStore.setState({ currentProject: 'base', readOnly: false })
    renderPanel()
    await confirmQueue('base')
    expect(vi.mocked(saveProjectQuietly)).toHaveBeenCalledWith('base')
    expect(vi.mocked(saveProjectQuietly).mock.invocationCallOrder[0])
      .toBeLessThan(enqueueMock.mock.invocationCallOrder[0])
  })

  it('drops the active project from the batch when its save fails', async () => {
    // `saveProjectQuietly` returns FALSE rather than throwing, and declines
    // outright while a solve is running — which is exactly when someone
    // reaches for "queue this branch". Queueing it anyway would solve a stale
    // file and report success.
    useUIStore.setState({ currentProject: 'base', readOnly: false })
    vi.mocked(saveProjectQuietly).mockResolvedValue(false)
    renderPanel()
    await confirmQueue('base')
    expect(enqueueMock.mock.calls.map(c => c[0])).toEqual(['id-variant'])
    expect(String(vi.mocked(toast.error).mock.calls[0][0])).toMatch(/could not be saved/i)
  })

  it('does not queue the same branch twice when confirmed twice', async () => {
    // The backend does NOT refuse a duplicate — `solve_queue.enqueue` appends
    // unconditionally — so a double confirm really does run every project in
    // the branch twice, the second overwriting the first's results.
    renderPanel()
    const row = await rowFor('base')
    const btn = within(row).getByTitle(/Queue this project and its/)
    await userEvent.click(btn)
    await userEvent.click(btn)
    await waitFor(() => expect(vi.mocked(confirmToast).mock.calls.length).toBe(2))
    // Both prompts accepted, as a double-click would.
    await Promise.all(vi.mocked(confirmToast).mock.calls.map(c => c[1]()))
    expect(enqueueMock.mock.calls.map(c => c[0])).toEqual(['id-base', 'id-variant'])
  })

  it('enqueues one at a time, never in a burst', async () => {
    // The code says sequential and explains why; nothing proved it, and
    // swapping the loop for `Promise.all` passed every other test here.
    // Counting overlap is the only assertion that can tell the two apart.
    let inFlight = 0
    let peak = 0
    enqueueMock.mockImplementation(async () => {
      inFlight += 1
      peak = Math.max(peak, inFlight)
      await new Promise(r => setTimeout(r, 0))
      inFlight -= 1
      return {}
    })
    renderPanel()
    await confirmQueue('base')
    expect(enqueueMock).toHaveBeenCalledTimes(2)
    expect(peak).toBe(1)
  })

  it('skips projects that already have a queued or running job', async () => {
    queueJobs = [{ id: 1, project_id: 'variant', status: 'running' }]
    renderPanel()
    await confirmQueue('base')
    expect(enqueueMock.mock.calls.map(c => c[0])).toEqual(['id-base'])
  })

  it('ignores jobs that have already finished', async () => {
    // A terminal job is not a reason to skip — that would make a branch
    // un-requeueable after its first run.
    queueJobs = [{ id: 1, project_id: 'variant', status: 'completed' }]
    renderPanel()
    await confirmQueue('base')
    expect(enqueueMock.mock.calls.map(c => c[0])).toEqual(['id-base', 'id-variant'])
  })

  it('reports the ones that failed without claiming they were queued', async () => {
    enqueueMock.mockRejectedValueOnce(new Error('409'))
    renderPanel()
    await confirmQueue('base')
    expect(vi.mocked(toast.success)).toHaveBeenCalledWith('Queued 1 solve')
    expect(String(vi.mocked(toast.error).mock.calls[0][0])).toContain('base')
  })

  it('refuses entirely while another user holds the lock', async () => {
    // Every other mutating control on the row is gated; this one queues a
    // solve that overwrites results AND writes to the project via the flush.
    useUIStore.setState({ currentProject: 'base', readOnly: true })
    renderPanel()
    const row = await rowFor('base')
    const blocked = within(row).getAllByTitle(/Read-only/)
    // Branch, queue, edit, delete. The queue button was the fourth to arrive
    // and the one that shipped ungated.
    expect(blocked).toHaveLength(4)
    expect(blocked.every(b => (b as HTMLButtonElement).disabled)).toBe(true)
  })
})

describe('the edit lock covers the project it is held on', () => {
  it('disables the active row while another user holds it', async () => {
    useUIStore.setState({ currentProject: 'loaded', readOnly: true })
    renderPanel()
    const row = await rowFor('loaded')
    // Both mutating affordances, not just one: branching the active project
    // now writes to it (the pre-copy save), so the lock has to cover it too.
    const blocked = within(row).getAllByTitle(/Read-only/)
    // Branch, edit, delete — everything that writes to the locked project.
    // ('loaded' is a leaf, so it has no subtree-queue button.)
    expect(blocked).toHaveLength(3)
    expect(blocked.every(b => (b as HTMLButtonElement).disabled)).toBe(true)
  })

  it('leaves other projects alone — the lock is not global', async () => {
    // A lock on 'loaded' says nothing about 'base', which the holder never
    // loaded; the backend gates that delete on the ACL, not on any lock.
    // Disabling it here only produced a button that looked broken.
    useUIStore.setState({ currentProject: 'loaded', readOnly: true })
    renderPanel()
    const row = await rowFor('base')
    expect(within(row).getByTitle('Delete this scenario')).toHaveProperty('disabled', false)
    expect(within(row).getByTitle(/^Branch a child scenario/)).toHaveProperty('disabled', false)
  })
})
