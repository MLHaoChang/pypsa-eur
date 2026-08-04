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

/** The row container for a project, so button lookups can't cross rows. */
const rowFor = async (name: string): Promise<HTMLElement> => {
  const label = await screen.findByText(name)
  return label.closest('.group') as HTMLElement
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

describe('the edit lock covers the project it is held on', () => {
  it('disables the active row while another user holds it', async () => {
    useUIStore.setState({ currentProject: 'loaded', readOnly: true })
    renderPanel()
    const row = await rowFor('loaded')
    // Both mutating affordances, not just one: branching the active project
    // now writes to it (the pre-copy save), so the lock has to cover it too.
    const blocked = within(row).getAllByTitle(/Read-only/)
    // Branch, edit, delete — everything that writes to the locked project.
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
