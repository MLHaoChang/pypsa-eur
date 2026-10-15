import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { getAssistantActivity, type ActivitySnapshot } from '../api/assistantActivity'
import AssistantActivity from './AssistantActivity'
vi.mock('../api/assistantActivity', () => ({ getAssistantActivity: vi.fn() }))
const get = vi.mocked(getAssistantActivity)
function snapshot(cursor = 'epoch:1', status = 'running'): ActivitySnapshot {
  return { version: 1, project_id: 'p1', filter: 'all', cursor, revision: 1,
    resnapshot_required: false, next_page: null, total: 1, warnings: [], scheduling_available: false,
    items: [{ id: 'fg:j1', source_id: 'j1', project_id: 'p1', family: 'foreground_simulation',
      title: 'Network solve', native_status: status, status, needs_you: false, evidence_available: false }] }
}
describe('AssistantActivity', () => {
  beforeEach(() => {
    get.mockReset()
    useUIStore.setState({ currentProject: 'Project A' })
    useChatStore.setState({ sessionId: 'session-a' })
  })
  afterEach(() => vi.useRealTimers())
  it('observes unavailable native states without sending work', async () => {
    get.mockResolvedValue(snapshot('epoch:1', 'unavailable'))
    render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText('Network solve')).toBeTruthy()
    expect(screen.getByText(/cannot be established/)).toBeTruthy()
    expect(get.mock.calls[0].slice(0, 2)).toEqual(['Project A', 'all'])
    expect(screen.queryByRole('button', { name: /cancel|approve|retry job/i })).toBeNull()
  })
  it('never shows late project data after a switch', async () => {
    let finish!: (value: ActivitySnapshot) => void
    get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    get.mockResolvedValue(snapshot('b:1', 'failed'))
    render(<AssistantActivity onConversation={vi.fn()} />)
    act(() => useUIStore.setState({ currentProject: 'Project B' }))
    expect(await screen.findByText('failed')).toBeTruthy()
    await act(async () => finish(snapshot('old:1', 'completed')))
    expect(screen.queryByText('completed')).toBeNull()
  })
  it('marks disconnected states stale and recovers a fresh snapshot', async () => {
    get.mockResolvedValueOnce(snapshot()).mockRejectedValueOnce(new Error('offline'))
    const recovered = snapshot('new:1', 'failed')
    recovered.resnapshot_required = true
    get.mockResolvedValue(recovered)
    render(<AssistantActivity onConversation={vi.fn()} />)
    await screen.findByText('Network solve')
    fireEvent(window, new Event('online'))
    expect(await screen.findByRole('alert')).toBeTruthy()
    fireEvent(window, new Event('online'))
    expect(await screen.findByText('Activity recovered from a fresh snapshot.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('opens the exact current confirmation without approving it', async () => {
    const data = snapshot()
    data.items = [{ ...data.items[0], family: 'confirmation', status: 'waiting_for_approval',
      native_status: 'waiting_for_approval', session_id: 'session-a', needs_you: true }]
    get.mockResolvedValue(data)
    const open = vi.fn()
    render(<AssistantActivity onConversation={open} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open originating chat' }))
    expect(open).toHaveBeenCalledOnce()
    expect(get).toHaveBeenCalledOnce()
  })
  it('reports scheduling unavailable and aborts observation on close', async () => {
    get.mockResolvedValue(snapshot())
    const { unmount } = render(<AssistantActivity onConversation={vi.fn()} />)
    await screen.findByText('Network solve')
    fireEvent.click(screen.getByRole('button', { name: 'Scheduled' }))
    expect(await screen.findByText('Scheduled work is not available yet.')).toBeTruthy()
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    const signal = get.mock.calls[1][2]?.signal
    unmount()
    expect(signal?.aborted).toBe(true)
  })
  it('distinguishes retained observations from live work and grants no recovered control', async () => {
    const data = snapshot('recovered:1', 'needs_review')
    data.items[0] = { ...data.items[0], native_status: 'unknown', last_observed_status: 'running', needs_you: true,
      observation: 'retained', observed_at: 1792000000,
      reason: 'Last observed work has no matching live worker. It was not restarted.' }
    get.mockResolvedValue(data)
    const open = vi.fn()
    const { container } = render(<AssistantActivity onConversation={open} />)
    expect(await screen.findByText(/Retained observation/)).toBeTruthy()
    expect(screen.getByText(/It was not restarted/)).toBeTruthy()
    expect(screen.getByText('Native status: unknown')).toBeTruthy()
    expect(screen.getByText('Last recorded native status: running')).toBeTruthy()
    expect(container.querySelector('time')?.dateTime).toBe(new Date(1792000000000).toISOString())
    expect(screen.getByText(/Unobserved outcomes remain unknown/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /approve|retry|resume|cancel|open originating/i })).toBeNull()
    expect(open).not.toHaveBeenCalled()
  })
  it('shows missing retained history as unavailable rather than proof nothing ran', async () => {
    const data = snapshot()
    data.items = []
    data.warnings = ['activity_history_unavailable']
    get.mockResolvedValue(data)
    render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText(/missing history does not mean work never ran/)).toBeTruthy()
  })
  it('does not crash or render an invalid observation date', async () => {
    const data = snapshot()
    data.items[0] = { ...data.items[0], observation: 'retained', observed_at: 1e100 }
    get.mockResolvedValue(data)
    const { container } = render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText('Retained observation')).toBeTruthy()
    expect(container.querySelector('time')).toBeNull()
  })
})
