// Assistant memory 02b (plan §4): the Memory page, opened as a dialog from the
// account menu. Evidence labels come from the server, which hides the name of a
// project the viewer cannot open (§9 M1); sharing is hidden in a one-member
// workspace; Edit writes a new version.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MemoryDialog, { MEMORY_EMPTY } from './MemoryDialog'
import { useMemoryStore } from '../../store/memoryStore'
import type { MemoryItem } from '../../api/memory'

const list = vi.fn()
const act = vi.fn()
const edit = vi.fn()
const versions = vi.fn()
const orgMembers = vi.fn()

vi.mock('../../api/memory', async (orig) => {
  const real = await orig<typeof import('../../api/memory')>()
  return {
    ...real,
    memoryApi: {
      ...real.memoryApi,
      list: (...a: unknown[]) => list(...a),
      act: (...a: unknown[]) => act(...a),
      edit: (...a: unknown[]) => edit(...a),
      versions: (...a: unknown[]) => versions(...a),
    },
  }
})
vi.mock('../../api/auth', () => ({ authApi: { orgMembers: () => orgMembers() } }))
vi.mock('../../auth/AuthProvider', () => ({ useAuth: () => ({ user: { id: 'u1' } }) }))

function item(over: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: 'mem_1', kind: 'preference', scope: 'user', applies_to: null,
    concept_keys: ['topic:units'], status: 'confirmed', origin: 'chat', version: 1,
    summary: 'Show power in GW', body: { key: 'units.power', value: 'GW' },
    evidence: [{ kind: 'chat', id: 's1', label: 'from this chat' }],
    nomination: null, promoted_from: null, recall_count: 0, apply_count: 2,
    last_used_at: null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
    ...over,
  }
}

function listing(byStatus: Record<string, MemoryItem[]>, canPromote = false) {
  list.mockImplementation((p: { status?: string; nominated?: boolean } = {}) => Promise.resolve({
    items: p.nominated ? byStatus.nominated ?? [] : byStatus[p.status ?? 'confirmed'] ?? [],
    can_promote: canPromote,
  }))
}

function renderDialog(opts: Parameters<ReturnType<typeof useMemoryStore.getState>['openMemory']>[0] = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useMemoryStore.getState().openMemory(opts)
  return render(<QueryClientProvider client={qc}><MemoryDialog /></QueryClientProvider>)
}

beforeEach(() => {
  for (const f of [list, act, edit, versions, orgMembers]) f.mockReset()
  orgMembers.mockResolvedValue([{ id: 'u1', email: 'a@x', role: 'member' }])
  act.mockResolvedValue(item())
  edit.mockResolvedValue(item({ version: 2 }))
})
afterEach(() => { cleanup(); useMemoryStore.getState().closeMemory() })

describe('Memory dialog', () => {
  it('says how items get there when there are none', async () => {
    listing({})
    renderDialog()
    expect((await screen.findByTestId('memory-empty')).textContent).toBe(MEMORY_EMPTY)
  })

  it('lists items with plain words and the source label from the server, capitalised', async () => {
    listing({ confirmed: [
      item(),
      item({ id: 'mem_2', kind: 'lesson', scope: 'org', summary: 'Check curtailment first',
        applies_to: 'capacity_expansion', body: { text: 'Check curtailment first' },
        evidence: [{ kind: 'project', id: null, label: 'source project (no access)', access: false }] }),
    ] })
    renderDialog()
    const rows = await screen.findAllByTestId('memory-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Preference')).toBeTruthy()
    expect(within(rows[0]).getByText('Any project')).toBeTruthy()
    expect(within(rows[0]).getByText('Just me')).toBeTruthy()
    expect(within(rows[0]).getByText(/^2× ·/)).toBeTruthy()
    expect(within(rows[1]).getByText('Capacity expansion projects')).toBeTruthy()
    expect(within(rows[1]).getByText('Workspace')).toBeTruthy()
    // M1: a project the viewer lost access to shows no name, only the server's label.
    const src = within(rows[1]).getByTestId('memory-source')
    expect(src.textContent).toBe('Source project (no access)')
    expect(src.className).toContain('italic')
    // A workspace item is not the member's to edit or retire.
    expect(within(rows[1]).queryByText('Retire')).toBeNull()
    expect(within(rows[1]).queryByText('Edit')).toBeNull()
  })

  it('hides Suggest to workspace in a one-member workspace', async () => {
    listing({ confirmed: [item()] })
    renderDialog()
    const row = await screen.findByTestId('memory-row')
    await waitFor(() => expect(orgMembers).toHaveBeenCalled())
    expect(within(row).queryByText('Suggest to workspace')).toBeNull()
  })

  it('offers Suggest to workspace when the workspace has other members', async () => {
    orgMembers.mockResolvedValue([{ id: 'u1' }, { id: 'u2' }])
    listing({ confirmed: [item()] })
    renderDialog()
    const btn = await screen.findByText('Suggest to workspace')
    fireEvent.click(btn)
    await waitFor(() => expect(act).toHaveBeenCalledWith('mem_1', 'nominate'))
  })

  it('retires an item', async () => {
    listing({ confirmed: [item()] })
    renderDialog()
    fireEvent.click(await screen.findByText('Retire'))
    await waitFor(() => expect(act).toHaveBeenCalledWith('mem_1', 'retire'))
  })

  it('Edit writes a new version with the new value', async () => {
    listing({ confirmed: [item()] })
    renderDialog()
    fireEvent.click(await screen.findByText('Edit'))
    const form = await screen.findByTestId('memory-edit-form')
    expect(within(form).getByText(/saving writes v2/)).toBeTruthy()
    fireEvent.change(within(form).getByLabelText('Value'), { target: { value: 'MW' } })
    fireEvent.change(within(form).getByLabelText('Summary'), { target: { value: 'Show power in MW' } })
    fireEvent.click(within(form).getByText('Save'))
    await waitFor(() => expect(edit).toHaveBeenCalledWith('mem_1', {
      summary: 'Show power in MW', body: { key: 'units.power', value: 'MW' },
    }))
    expect(act).not.toHaveBeenCalled()
  })

  it('an empty summary blocks Save with a visible reason', async () => {
    listing({ confirmed: [item()] })
    renderDialog()
    fireEvent.click(await screen.findByText('Edit'))
    const form = await screen.findByTestId('memory-edit-form')
    fireEvent.change(within(form).getByLabelText('Summary'), { target: { value: '  ' } })
    expect((within(form).getByText('Save') as HTMLButtonElement).disabled).toBe(true)
    expect(within(form).getByText('A summary is needed.')).toBeTruthy()
  })

  it('opens straight on the edit form named by the store (Edit first, toast Edit)', async () => {
    listing({ confirmed: [], all: [item({ id: 'mem_9', summary: 'From the card' })] })
    renderDialog({ editId: 'mem_9' })
    const form = await screen.findByTestId('memory-edit-form')
    expect((within(form).getByLabelText('Summary') as HTMLInputElement).value).toBe('From the card')
  })

  it('shows the history of versions, newest first', async () => {
    listing({ confirmed: [item({ version: 2 })] })
    versions.mockResolvedValue({ id: 'mem_1', versions: [
      { version: 1, summary: 'Old', body: {}, evidence: [], edited_by: 'u1', edited_at: '2026-11-01T00:00:00Z' },
      { version: 2, summary: 'New', body: {}, evidence: [], edited_by: 'u7', edited_at: '2026-11-02T00:00:00Z' },
    ] })
    renderDialog()
    fireEvent.click(await screen.findByText('History'))
    const hist = await screen.findByTestId('memory-history')
    const lines = within(hist).getAllByRole('listitem').map(li => li.textContent ?? '')
    expect(lines[0]).toMatch(/^v2New.*A workspace admin/)
    expect(lines[1]).toMatch(/^v1Old.*You/)
  })

  it('Proposals: confirm, or edit and confirm', async () => {
    const p = item({ id: 'mem_p', status: 'proposed', summary: 'Use EUR' ,
      body: { key: 'currency', value: 'EUR' } })
    listing({ confirmed: [], proposed: [p], all: [p] })
    renderDialog({ tab: 'proposals' })
    expect(await screen.findByText('Proposals · 1')).toBeTruthy()
    fireEvent.click(await screen.findByText('Confirm'))
    await waitFor(() => expect(act).toHaveBeenCalledWith('mem_p', 'confirm'))
    fireEvent.click(screen.getByText('Edit and confirm'))
    const form = await screen.findByTestId('memory-edit-form')
    fireEvent.click(within(form).getByText('Save and confirm'))
    await waitFor(() => expect(edit).toHaveBeenCalledWith('mem_p',
      { summary: 'Use EUR', body: { key: 'currency', value: 'EUR' } }))
    await waitFor(() => expect(act).toHaveBeenCalledWith('mem_p', 'confirm'))
  })

  it('shows the admin queue only to an admin of a shared workspace', async () => {
    orgMembers.mockResolvedValue([{ id: 'u1' }, { id: 'u2' }])
    listing({ confirmed: [], nominated: [item({ id: 'mem_n', nomination: 'pending' })] }, true)
    renderDialog()
    fireEvent.click(await screen.findByText('Suggested to workspace'))
    fireEvent.click(await screen.findByText('Promote'))
    await waitFor(() => expect(act).toHaveBeenCalledWith('mem_n', 'promote'))
  })

  it('no admin queue in a one-member workspace, even for an admin', async () => {
    listing({ confirmed: [item()] }, true)
    renderDialog()
    await screen.findByTestId('memory-row')
    await waitFor(() => expect(orgMembers).toHaveBeenCalled())
    expect(screen.queryByText('Suggested to workspace')).toBeNull()
  })
})
