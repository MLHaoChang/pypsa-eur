import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { getChatAccess, saveChatRestriction, type ChatAccessSnapshot } from '../api/chatAccess'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import ChatAccess from './ChatAccess'

vi.mock('../api/chatAccess', () => ({ getChatAccess: vi.fn(), saveChatRestriction: vi.fn() }))
const get = vi.mocked(getChatAccess)
const save = vi.mocked(saveChatRestriction)
function snapshot(project = 'Project A'): ChatAccessSnapshot {
  return { version: 1, read_only: true, observed_at: 100,
    scope: { project_id: 'p1', project_name: project }, selection: { ui_mode: 'expert', checked_requested: false },
    restrictions: { mode: 'standard', revision: 'revision-1' },
    confirmation_rules: [{ tier: 'write', native: 'none', external: 'user' }],
    guided_uncarded: [],
    project_lock: 'other', blockers: ['project_writes_locked'], session: null,
    checking: { server_mode: 'disabled', effective_for_selection: false, scope: 'bounded_profiles' },
    limits: { output_tokens_per_turn: 8192, tool_calls_per_turn: 25, output_tokens_per_session: 200000,
      turns_without_reported_usage: 100, daily_project_tokens: null, confirmation_ttl_seconds: 60 } }
}
function mount(companion = false, checked = false) {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={query}><ChatAccess checked={checked} companion={companion} /></QueryClientProvider>)
}
beforeEach(() => {
  get.mockReset()
  save.mockReset()
  useUIStore.setState({ currentProject: 'Project A', uiMode: 'expert', assistantDockOpen: true })
  useChatStore.setState({ sessionId: null, newChatSeq: 0 })
})
it('shows observed access in in-page chat before opening the full review', async () => {
  get.mockResolvedValue(snapshot())
  useUIStore.setState({ assistantDockOpen: false })
  mount(true)
  const summary = await screen.findByRole('region', { name: 'In-page chat access' })
  await screen.findByText('Your rule: Standard')
  expect(summary.textContent).toContain('Project A · Expert')
  expect(summary.textContent).toContain('Your rule: Standard')
  expect(summary.textContent).toContain('Answer checks off for this selection')
  expect(summary.textContent).toContain('Project writes are locked by another user')
  expect(screen.queryByRole('table')).toBeNull()
  expect(screen.queryByRole('combobox', { name: 'Your saved rule' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  expect(await screen.findByRole('table')).toBeTruthy()
  expect(screen.getByRole('combobox', { name: 'Your saved rule' })).toBeTruthy()
  expect(save).not.toHaveBeenCalled()
})
it('hides an in-page observation while refreshing and after a failed refresh', async () => {
  let fail!: (error: Error) => void
  get.mockResolvedValueOnce(snapshot()).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
    .mockResolvedValue(snapshot())
  useUIStore.setState({ assistantDockOpen: false })
  mount(true)
  await screen.findByText('Your rule: Standard')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh access' }))
  await screen.findByText('Refreshing access…')
  expect(screen.queryByText('Your rule: Standard')).toBeNull()
  await act(async () => fail(new Error('offline')))
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('Answer checks off for this selection')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh access' }))
  expect(await screen.findByText('Your rule: Standard')).toBeTruthy()
})
it('rejects a late in-page observation from a previous project', async () => {
  let finish!: (value: ChatAccessSnapshot) => void
  get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  get.mockResolvedValue(snapshot('Project B'))
  useUIStore.setState({ assistantDockOpen: false })
  mount(true)
  act(() => useUIStore.setState({ currentProject: 'Project B' }))
  await screen.findByText('Next turn: Project B · Expert')
  await act(async () => finish(snapshot()))
  expect(screen.queryByText('Next turn: Project A · Expert')).toBeNull()
})
it.each(['mode', 'session', 'checked'])('refuses an in-page response for the wrong %s selection', async fault => {
  const data = snapshot()
  if (fault === 'mode') data.selection.ui_mode = 'guided'
  if (fault === 'session') data.session = { id: 'other-session', turn_in_flight: false, output_tokens_used: 0 }
  if (fault === 'checked') data.selection.checked_requested = true
  get.mockResolvedValue(data)
  useUIStore.setState({ assistantDockOpen: false })
  mount(true)
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('Your rule: Standard')).toBeNull()
})
it('distinguishes an unavailable saved rule from standard and an unsaved draft', async () => {
  const data = snapshot()
  data.restrictions = null
  get.mockResolvedValue(data)
  useUIStore.setState({ assistantDockOpen: false })
  mount(true)
  expect(await screen.findByText('Your rule: Unavailable')).toBeTruthy()
  expect(screen.queryByText('Your rule: Standard')).toBeNull()
})
it('does not label unavailable requested checks as an unchecked selection', async () => {
  const data = snapshot()
  data.selection.checked_requested = true
  data.blockers = ['requested_checks_unavailable']
  get.mockResolvedValue(data)
  useUIStore.setState({ assistantDockOpen: false })
  mount(true, true)
  expect(await screen.findByText('Requested answer checks unavailable')).toBeTruthy()
  expect(screen.queryByText('Answer checks off for this selection')).toBeNull()
})
it('loads only on explicit disclosure and explains actual rules and blockers', async () => {
  get.mockResolvedValue(snapshot())
  mount()
  expect(get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  expect(await screen.findByText('Project writes are locked by another user. Reads remain available.')).toBeTruthy()
  expect(screen.getByRole('columnheader', { name: 'External tools' })).toBeTruthy()
  expect(screen.getByText('You confirm')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /approve|run|connect/i })).toBeNull()
})
it('rejects late data from the old project after a rebind', async () => {
  let finish!: (value: ChatAccessSnapshot) => void
  get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  get.mockResolvedValue(snapshot('Project B'))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  act(() => useUIStore.setState({ currentProject: 'Project B' }))
  expect(await screen.findByText('Project B')).toBeTruthy()
  await act(async () => finish(snapshot()))
  expect(screen.queryByText('Project A')).toBeNull()
})
it('hides previous rules on failed refresh and permits recovery', async () => {
  get.mockResolvedValueOnce(snapshot()).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(snapshot())
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  await screen.findByText('You confirm')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh access' }))
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('You confirm')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh access' }))
  expect(await screen.findByText('You confirm')).toBeTruthy()
})
it.each([false, true])('supports keyboard access review (in-page: %s)', async companion => {
  get.mockResolvedValue(snapshot())
  useUIStore.setState({ assistantDockOpen: !companion })
  mount(companion)
  const user = userEvent.setup()
  const button = screen.getByRole('button', { name: 'Access and approvals' })
  button.focus()
  await user.keyboard(' ')
  expect(await screen.findByText('You confirm')).toBeTruthy()
  expect(button.getAttribute('aria-expanded')).toBe('true')
  button.focus()
  await user.keyboard('{Enter}')
  expect(button.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByRole('region', { name: 'Current access and approvals' })).toBeNull()
})
it('never presents mismatched server scope as current access', async () => {
  get.mockResolvedValue(snapshot('Another project'))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('Another project')).toBeNull()
  expect(screen.queryByText('You confirm')).toBeNull()
})
it.each([false, true])('saves a personal restriction only on explicit click and server confirmation (in-page: %s)', async companion => {
  get.mockResolvedValue(snapshot())
  let finish!: (value: { project_id: string; revision: string; mode: 'read_only' }) => void
  save.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  useUIStore.setState({ assistantDockOpen: !companion })
  mount(companion)
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  await screen.findByText('You confirm')
  const field = await screen.findByRole('combobox', { name: 'Your saved rule' })
  fireEvent.change(field, { target: { value: 'read_only' } })
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Save restriction' }))
  expect(save).toHaveBeenCalledWith('p1', 'revision-1', 'read_only')
  expect(screen.getByRole('button', { name: 'Saving restriction…' }).hasAttribute('disabled')).toBe(true)
  expect(screen.queryByText('Restriction saved.')).toBeNull()
  const updated = snapshot()
  updated.restrictions = { mode: 'read_only', revision: 'revision-2' }
  get.mockResolvedValue(updated)
  await act(async () => finish({ project_id: 'p1', revision: 'revision-2', mode: 'read_only' }))
  expect(await screen.findByText('Restriction saved.')).toBeTruthy()
  expect((await screen.findByRole('combobox', { name: 'Your saved rule' }) as HTMLSelectElement).value).toBe('read_only')
  if (companion) expect(screen.getByText('Your rule: Read-only tools')).toBeTruthy()
  get.mockResolvedValue({ ...updated, restrictions: { mode: 'confirm_changes', revision: 'revision-newer' } })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh access' }))
  await screen.findByText('You confirm')
  expect(screen.queryByText('Restriction saved.')).toBeNull()
  expect((screen.getByRole('combobox', { name: 'Your saved rule' }) as HTMLSelectElement).value).toBe('confirm_changes')
})
it('keeps a rejected save unconfirmed and requires a fresh revision for retry', async () => {
  get.mockResolvedValue(snapshot())
  save.mockRejectedValueOnce(new Error('409 stale'))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  await screen.findByText('You confirm')
  fireEvent.change(screen.getByRole('combobox', { name: 'Your saved rule' }), { target: { value: 'read_only' } })
  expect(screen.getByText('Unsaved choice. Save to apply it.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Save restriction' }))
  expect(await screen.findByText('Save could not be confirmed. Refresh access and choose again.')).toBeTruthy()
  expect(screen.queryByText('Restriction saved.')).toBeNull()
  const fresh = snapshot()
  fresh.restrictions = { mode: 'confirm_changes', revision: 'revision-newer' }
  get.mockResolvedValue(fresh)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh access' }))
  await screen.findByText('You confirm')
  expect((screen.getByRole('combobox', { name: 'Your saved rule' }) as HTMLSelectElement).value).toBe('confirm_changes')
  fireEvent.change(screen.getByRole('combobox', { name: 'Your saved rule' }), { target: { value: 'read_only' } })
  save.mockResolvedValue({ project_id: 'p1', mode: 'read_only', revision: 'revision-final' })
  fireEvent.click(screen.getByRole('button', { name: 'Save restriction' }))
  expect(save).toHaveBeenLastCalledWith('p1', 'revision-newer', 'read_only')
})
it.each(['project', 'chat'])('hides a late save acknowledgement after %s changes', async change => {
  get.mockResolvedValue(snapshot())
  let finish!: (value: { project_id: string; revision: string; mode: 'read_only' }) => void
  save.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  await screen.findByText('You confirm')
  fireEvent.change(screen.getByRole('combobox', { name: 'Your saved rule' }), { target: { value: 'read_only' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save restriction' }))
  if (change === 'project') {
    const next = snapshot('Project B')
    next.scope.project_id = 'p2'
    get.mockResolvedValue(next)
    act(() => useUIStore.setState({ currentProject: 'Project B' }))
    await screen.findByText('Project B')
  } else {
    act(() => useChatStore.setState({ newChatSeq: 1 }))
    await screen.findByText('You confirm')
  }
  await act(async () => finish({ project_id: 'p1', mode: 'read_only', revision: 'revision-late' }))
  expect(screen.queryByText('Restriction saved.')).toBeNull()
  expect((screen.getByRole('combobox', { name: 'Your saved rule' }) as HTMLSelectElement).value).toBe('standard')
})
it('cannot save rules for an unsaved draft', async () => {
  const data = snapshot()
  data.scope = { project_id: null, project_name: null }
  data.restrictions = null
  get.mockResolvedValue(data)
  useUIStore.setState({ currentProject: null })
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  await screen.findByText('Unsaved draft')
  expect(screen.getByRole('button', { name: 'Save restriction' }).hasAttribute('disabled')).toBe(true)
  expect(save).not.toHaveBeenCalled()
})
it.each(['wrong-project', 'old-revision'])('does not claim a %s acknowledgement saved this choice', async fault => {
  get.mockResolvedValue(snapshot())
  save.mockResolvedValue({ project_id: fault === 'wrong-project' ? 'p2' : 'p1', mode: 'read_only',
    revision: fault === 'old-revision' ? 'revision-1' : 'revision-new' })
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  await screen.findByText('You confirm')
  fireEvent.change(screen.getByRole('combobox', { name: 'Your saved rule' }), { target: { value: 'read_only' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save restriction' }))
  expect(await screen.findByText('Save could not be confirmed. Refresh access and choose again.')).toBeTruthy()
  expect(screen.queryByText('Restriction saved.')).toBeNull()
})
it('names the Guided tools that skip the write card with their actual rule', async () => {
  get.mockResolvedValue({ ...snapshot(), guided_uncarded: [{ tool: 'preview_project_changes', native: 'none' }] })
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  expect(await screen.findByText(/In Guided, these Write tools have their own rule/)).toBeTruthy()
  expect(screen.getByRole('listitem').textContent).toBe('Preview project changes · No approval card')
})
it('adds no Guided exception line when there are none', async () => {
  get.mockResolvedValue(snapshot())
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  expect(await screen.findByText('Confirmation rules')).toBeTruthy()
  expect(screen.queryByText(/these Write tools/)).toBeNull()
})
