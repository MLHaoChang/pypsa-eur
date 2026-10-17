import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { getChatAccess, type ChatAccessSnapshot } from '../api/chatAccess'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import ChatAccess from './ChatAccess'

vi.mock('../api/chatAccess', () => ({ getChatAccess: vi.fn() }))
const get = vi.mocked(getChatAccess)
function snapshot(project = 'Project A'): ChatAccessSnapshot {
  return { version: 1, read_only: true, observed_at: 100,
    scope: { project_id: 'p1', project_name: project }, selection: { ui_mode: 'expert', checked_requested: false },
    confirmation_rules: [{ tier: 'write', native: 'none', external: 'user' }],
    project_lock: 'other', blockers: ['project_writes_locked'], session: null,
    checking: { server_mode: 'disabled', effective_for_selection: false, scope: 'bounded_profiles' },
    limits: { output_tokens_per_turn: 8192, tool_calls_per_turn: 25, output_tokens_per_session: 200000,
      turns_without_reported_usage: 100, daily_project_tokens: null, confirmation_ttl_seconds: 60 } }
}
function mount() {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={query}><ChatAccess checked={false} /></QueryClientProvider>)
}
beforeEach(() => {
  get.mockReset()
  useUIStore.setState({ currentProject: 'Project A', uiMode: 'expert', assistantDockOpen: true })
  useChatStore.setState({ sessionId: null, newChatSeq: 0 })
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
it('supports keyboard disclosure and stops observing when collapsed', async () => {
  get.mockResolvedValue(snapshot())
  mount()
  const user = userEvent.setup()
  const button = screen.getByRole('button', { name: 'Access and approvals' })
  button.focus()
  await user.keyboard(' ')
  expect(await screen.findByText('You confirm')).toBeTruthy()
  expect(button.getAttribute('aria-expanded')).toBe('true')
  button.focus()
  await user.keyboard('{Enter}')
  expect(button.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByRole('region')).toBeNull()
})
it('never presents mismatched server scope as current access', async () => {
  get.mockResolvedValue(snapshot('Another project'))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Access and approvals' }))
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('Another project')).toBeNull()
  expect(screen.queryByText('You confirm')).toBeNull()
})
