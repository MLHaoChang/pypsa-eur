/**
 * Live-voice 11c-2 — the line under a watched tool row: Stop monitoring (R3,
 * UX 1/2), what a lost or ended stream reads (F1), the one muted gap line
 * (UX 3), teardown (F2), a failed stop POST (F6), the keyboard (F7) and the
 * in-place results refresh on job_terminal (C3/R1, F5).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import {
  createChatStream, createResumableProgressStream, getChatHistory, postChatAbort, postChatConfirm,
  postChatProgressStop, type ChatFrame, type ProgressStreamEnd,
} from '../api/chat'
import { networkApi } from '../api/network'
import { invalidateNetworkQueries } from '../utils/projectActions'
import ChatPanel, { MONITOR_WORDS, refreshIfStillCurrent } from './ChatPanel'

vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: () => ({
    available: false, supported: false, listening: false, interim: '',
    permissionDenied: false, toggle: vi.fn(), stop: vi.fn(),
  }),
}))
vi.mock('../api/dictation', () => ({
  getDictationConfig: vi.fn().mockResolvedValue({
    available: false, model: '', languages: ['en'],
    max_bytes: 10485760, max_recording_seconds: 120,
  }),
}))
vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
    createResumableProgressStream: vi.fn(() => () => {}),
    getChatHistory: vi.fn().mockResolvedValue({
      turns: [], last_session_id: null, bound_project: null,
      history_gap: 0, pending_turn: null,
    }),
    postChatAbort: vi.fn(),
    postChatProgressStop: vi.fn().mockResolvedValue({ ok: true, stopped: true }),
    postChatConfirm: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'env', hint: 'test',
      overridden_by_environment: false, storage_path: '/tmp/synthetic',
    }),
  }
})
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 1, snapshot_count: 2 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0, unsaved: false, network_revision: 7 }),
  },
}))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return { ...actual, invalidateNetworkQueries: vi.fn() }
})
vi.mock('../api/simulation', () => ({
  simulationApi: {
    getStatus: vi.fn().mockResolvedValue({
      running: false, status: 'idle', dispatch: 'none',
    }),
  },
}))
vi.mock('../api/llmSettings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/llmSettings')>()
  return {
    ...actual,
    getChatProfiles: vi.fn().mockResolvedValue({
      profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic', chat_ready: true }],
      active_profile_id: 'claude',
    }),
  }
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

const initialUiMode = useUIStore.getState().uiMode

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ uiMode: initialUiMode })
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [], last_session_id: null, bound_project: null,
    history_gap: 0, pending_turn: null,
  })
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: true,
    projectMismatch: null,
  })
  useChatStore.setState({
    sessionId: 'sess-detached', pending: null, messages: [], error: null,
    profileId: null, streaming: false, streamCleanup: null, toolProgress: {},
    lastRequest: null, requestQueue: [], activeRequest: null,
  })
  vi.mocked(networkApi.undoInfo).mockReset()
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false, network_revision: 7 })
  vi.mocked(postChatProgressStop).mockReset()
  vi.mocked(postChatProgressStop).mockResolvedValue({ ok: true, stopped: true })
})

afterEach(() => cleanup())


type Watch = {
  push: (frame: ChatFrame) => void
  end: (end: ProgressStreamEnd) => void
  close: ReturnType<typeof vi.fn>
}

/** Run a turn whose tool_result hands the solve to a detached monitor. */
async function startWatching({ requestRow = false } = {}): Promise<Watch> {
  const watch = { close: vi.fn() } as unknown as Watch
  vi.mocked(createResumableProgressStream).mockImplementation((_sid, onFrame, onEnd) => {
    watch.push = (frame) => act(() => onFrame(frame))
    watch.end = (end) => act(() => onEnd?.(end))
    return watch.close as unknown as () => void
  })
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    queueMicrotask(() => {
      onFrame({ event: 'session_init', data: { session_id: 'sess-detached', model: 'claude', turn_id: 't1' } })
      if (requestRow) onFrame({ event: 'tool_request', data: { tool_name: 'run_simulation', tool_use_id: 'tu-sim' } })
      onFrame({
        event: 'tool_result',
        data: { tool_name: 'run_simulation', tool_use_id: 'tu-sim', result: { status: 'started', detached_progress: true } },
      })
      onFrame({ event: 'turn_done', data: {} })
      onFrame({ event: 'session_done', data: { reason: 'completed' } })
    })
    return () => {}
  })
  renderPanel()
  await screen.findByTestId('chat-input')
  act(() => { useChatStore.getState().sendRequest('solve please', { source: 'hub-card' }) })
  await waitFor(() => expect(vi.mocked(createResumableProgressStream)).toHaveBeenCalled())
  expect(vi.mocked(createResumableProgressStream).mock.calls[0][0]).toBe('sess-detached')
  await screen.findByTestId('chat-monitor-line')
  return watch
}

const line = () => screen.queryByTestId('chat-monitor-line')
const stopButton = () => screen.queryByRole('button', { name: MONITOR_WORDS.stop })

describe('Stop monitoring', () => {
  it('UX 1: shows the button with "the job keeps running" under the watched row', async () => {
    await startWatching()
    expect(line()?.dataset.state).toBe('monitoring')
    expect(stopButton()).not.toBeNull()
    expect(line()?.textContent).toContain(MONITOR_WORDS.keepsRunning)
    expect(line()?.textContent).not.toMatch(/tu-sim|run_simulation|fg:/)
  })

  it('R3 / UX 2: POSTs /progress/stop, closes the stream, cancels nothing, and says so', async () => {
    const watch = await startWatching()
    const streamsBefore = vi.mocked(createChatStream).mock.calls.length
    await userEvent.click(stopButton() as HTMLElement)
    expect(postChatProgressStop).toHaveBeenCalledTimes(1)
    expect(postChatProgressStop).toHaveBeenCalledWith('sess-detached')
    expect(watch.close).toHaveBeenCalledTimes(1)
    expect(postChatAbort).not.toHaveBeenCalled()
    expect(postChatConfirm).not.toHaveBeenCalled()
    expect(vi.mocked(createChatStream).mock.calls.length).toBe(streamsBefore)
    expect(line()?.dataset.state).toBe('stopped')
    expect(line()?.textContent).toBe(MONITOR_WORDS.stopped)
    expect(stopButton()).toBeNull()
  })

  it('F6: a failed stop POST still closes the stream, its rejection is handled, and the line reads the same', async () => {
    // Made when the panel calls it, so the only handler it can get is the panel's.
    let handled: ReturnType<typeof vi.fn> | undefined
    vi.mocked(postChatProgressStop).mockImplementationOnce(() => {
      const failed = Promise.reject(new Error('HTTP 500'))
      handled = vi.spyOn(failed, 'catch') as unknown as ReturnType<typeof vi.fn>
      return failed
    })
    const unhandled = vi.fn()
    window.addEventListener('unhandledrejection', unhandled)
    try {
      const watch = await startWatching()
      await userEvent.click(stopButton() as HTMLElement)
      await new Promise((r) => setTimeout(r, 0))
      expect(handled).toBeDefined()
      expect(handled).toHaveBeenCalledTimes(1)
      expect(watch.close).toHaveBeenCalledTimes(1)
      expect(postChatAbort).not.toHaveBeenCalled()
      expect(line()?.textContent).toBe(MONITOR_WORDS.stopped)
      expect(unhandled).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('unhandledrejection', unhandled)
    }
  })

  it('F7: the button has an accessible name and works from the keyboard', async () => {
    await startWatching()
    const button = stopButton() as HTMLElement
    expect(button.tagName).toBe('BUTTON')
    button.focus()
    expect(document.activeElement).toBe(button)
    await userEvent.keyboard('{Enter}')
    expect(postChatProgressStop).toHaveBeenCalledTimes(1)
    // The state is words in a status region, not a colour.
    expect(screen.getByRole('status').textContent).toBe(MONITOR_WORDS.stopped)
  })
})

it('Expert: the request row and the result row share the tool id, and the line shows once', async () => {
  useUIStore.setState({ uiMode: 'expert' })
  await startWatching({ requestRow: true })
  expect(screen.getAllByTestId('chat-monitor-line')).toHaveLength(1)
  expect(screen.getAllByRole('button', { name: MONITOR_WORDS.stop })).toHaveLength(1)
  // It sits in the result row, the later of the two, not in the request row.
  const rows = screen.getAllByTestId('chat-message')
  const requestRow = rows.findIndex((r) => r.textContent?.includes('→ run_simulation'))
  const owner = (line() as HTMLElement).closest('[data-testid="chat-message"]') as HTMLElement
  expect(requestRow).toBeGreaterThanOrEqual(0)
  expect(rows.indexOf(owner)).toBeGreaterThan(requestRow)
  expect(owner.textContent).not.toContain('→ run_simulation')
})

describe('F1: the stream ending by itself', () => {
  it('a stream that gave up no longer reads as monitoring', async () => {
    const watch = await startWatching()
    watch.end('gave_up')
    expect(line()?.dataset.state).toBe('lost')
    expect(line()?.textContent).toBe(MONITOR_WORDS.lost)
    expect(stopButton()).toBeNull()
  })

  it('a stream the server closed (the monitor is gone) leaves no line', async () => {
    const watch = await startWatching()
    watch.end('monitor_ended')
    expect(line()).toBeNull()
    expect(stopButton()).toBeNull()
  })
})

it('UX 3: snapshots after gaps give one muted line, never a repeated one', async () => {
  const watch = await startWatching()
  const snap = { event: 'job_snapshot', id: 'e.1:4', data: { epoch: 'e.1', seq: 4, monitor_active: true, jobs: [] } }
  watch.push(snap)
  watch.push({ ...snap, id: 'e.1:9' })
  const notes = screen.getAllByTestId('chat-monitor-note')
  expect(notes).toHaveLength(1)
  expect(notes[0].textContent).toBe(MONITOR_WORDS.caughtUp)
  expect(stopButton()).not.toBeNull()
})

it('F2: unmounting the panel closes the stream', async () => {
  const watch = await startWatching()
  cleanup()
  expect(watch.close).toHaveBeenCalled()
})

describe('C3: job_terminal refreshes in place only when everything still matches', () => {
  const terminal = (job: Record<string, unknown> | null) => ({
    event: 'job_terminal', id: 'e.1:5', data: { job_id: 'fg:1', tool_use_id: 'tu-sim', job },
  })

  it('a succeeded job on the same project and revision invalidates that project, without navigating', async () => {
    const watch = await startWatching()
    const panelBefore = useUIStore.getState().activeSlidePanel
    watch.push(terminal({ outcome: 'succeeded', input_revision: 7 }))
    await waitFor(() => expect(invalidateNetworkQueries).toHaveBeenCalledTimes(1))
    expect(vi.mocked(invalidateNetworkQueries).mock.calls[0][1]).toBe('Demo')
    expect(useUIStore.getState().currentProject).toBe('Demo')
    expect(useUIStore.getState().activeSlidePanel).toBe(panelBefore)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a refused job (null) does nothing', async () => {
    const watch = await startWatching()
    watch.push(terminal(null))
    await new Promise((r) => setTimeout(r, 0))
    expect(networkApi.undoInfo).not.toHaveBeenCalled()
    expect(invalidateNetworkQueries).not.toHaveBeenCalled()
  })
})

describe('refreshIfStillCurrent (F5: each clause has its own failing row)', () => {
  const qc = {} as never
  const ok = { outcome: 'succeeded', input_revision: 7 }

  it.each([
    ['all clauses hold', ok, 'Demo', 'Demo', 7, true],
    ['outcome failed', { ...ok, outcome: 'failed' }, 'Demo', 'Demo', 7, false],
    ['outcome cancelled', { ...ok, outcome: 'cancelled' }, 'Demo', 'Demo', 7, false],
    ['input_revision null', { ...ok, input_revision: null }, 'Demo', 'Demo', 7, false],
    ['input_revision missing', { outcome: 'succeeded' }, 'Demo', 'Demo', 7, false],
    ['input_revision 0 is a number', { ...ok, input_revision: 0 }, 'Demo', 'Demo', 0, true],
    ['the tab moved to another project', ok, 'Other', 'Demo', 7, false],
    ['the project was renamed during the job', ok, 'Demo (renamed)', 'Demo', 7, false],
    ['no project open', ok, null, 'Demo', 7, false],
    ['the revision moved', ok, 'Demo', 'Demo', 8, false],
    ['no revision on either side (the counter is unknown)', { outcome: 'succeeded' }, 'Demo', 'Demo', undefined, false],
  ] as const)('%s', async (_name, job, current, origin, revision, refreshed) => {
    useUIStore.setState({ currentProject: current })
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false, network_revision: revision })
    await expect(refreshIfStillCurrent(qc, job, origin)).resolves.toBe(refreshed)
    expect(vi.mocked(invalidateNetworkQueries).mock.calls).toEqual(refreshed ? [[qc, origin]] : [])
  })

  it('no job: nothing is read', async () => {
    await expect(refreshIfStillCurrent(qc, null, 'Demo')).resolves.toBe(false)
    expect(networkApi.undoInfo).not.toHaveBeenCalled()
  })

  it('the tab moves while the revision is read: no refresh', async () => {
    vi.mocked(networkApi.undoInfo).mockImplementationOnce(async () => {
      useUIStore.setState({ currentProject: 'Other' })
      return { depth: 0, unsaved: false, network_revision: 7 }
    })
    await expect(refreshIfStillCurrent(qc, ok, 'Demo')).resolves.toBe(false)
    expect(invalidateNetworkQueries).not.toHaveBeenCalled()
  })

  it('the revision cannot be read: no refresh', async () => {
    vi.mocked(networkApi.undoInfo).mockRejectedValueOnce(new Error('offline'))
    await expect(refreshIfStillCurrent(qc, ok, 'Demo')).resolves.toBe(false)
    expect(invalidateNetworkQueries).not.toHaveBeenCalled()
  })
})
