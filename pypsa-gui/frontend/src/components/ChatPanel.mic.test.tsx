import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// Collapsing the dock must turn the microphone off.
//
// While ChatPanel was the 'chat' SlidePanel, closing it unmounted the panel
// and useSpeechToText's unmount cleanup stopped the session. The panel is now
// never unmounted, and SpeechSession runs `continuous = true` with an `onend`
// auto-restart — so without an explicit stop the mic keeps recording after a
// collapse, with no in-app signal, because the mic button and the interim
// transcript are both inside the dock's `hidden` body.
//
// The hook is mocked rather than driven through a fake Recognition ctor
// because ChatPanel constructs `useSpeechToText` with no injection points
// (`speechWindow` / `RecognitionCtor` are for the hook's own unit tests). What
// is under test here is ChatPanel's wiring — that a collapse calls stop() —
// which is exactly the line that was missing.

const { speechStop } = vi.hoisted(() => ({ speechStop: vi.fn() }))

vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: () => ({
    supported: true,
    available: true,
    permissionDenied: false,
    listening: true,
    interim: '',
    toggle: vi.fn(),
    stop: speechStop,
  }),
}))

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'settings', hint: '…wxyz',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

import ChatPanel from './ChatPanel'

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  speechStop.mockClear()
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: true, activeSlidePanel: null })
})

describe('dictation vs the dock', () => {
  it('stops the microphone when the dock collapses', () => {
    renderPanel()
    // The project-switch effect calls stop() once on mount; clear so this
    // asserts on the collapse alone.
    speechStop.mockClear()

    act(() => { useUIStore.getState().setAssistantDockOpen(false) })

    expect(speechStop).toHaveBeenCalled()
  })

  // The narrowness matters: the instruction was to STOP dictation, not to
  // disable it while collapsed. Expanding again must leave the mic usable, so
  // nothing may keep calling stop() once the dock is open.
  it('does not keep stopping the mic once the dock is expanded again', () => {
    renderPanel()

    act(() => { useUIStore.getState().setAssistantDockOpen(false) })
    speechStop.mockClear()
    act(() => { useUIStore.getState().setAssistantDockOpen(true) })

    expect(speechStop).not.toHaveBeenCalled()
  })

  // Render-path control: the dock effect must not stop the mic on re-renders
  // that leave `assistantDockOpen` alone.
  //
  // `currentProject` specifically, because ChatPanel subscribes to it. An
  // earlier version mutated `resultsSnapshotIdx`, which ChatPanel does not
  // subscribe to — nothing re-rendered, so the test could not observe
  // anything at all.
  //
  // What this actually catches, verified by trying each: an effect that stops
  // UNCONDITIONALLY on re-render (`useEffect(() => { speech.stop() })`) makes
  // the count 3 and fails here. It does NOT catch merely dropping the
  // dependency array while keeping the `if (!assistantDockOpen)` guard — that
  // variant re-runs but does nothing while the dock is OPEN, which is the only
  // state this test exercises. (It does differ while the dock is CLOSED:
  // `stop()` on every render rather than once. Idempotent on an already-
  // stopped session and unobservable, but not literally identical.) Exactly
  // ONE call is expected, from the project-switch effect that is supposed to
  // stop the mic on a switch.
  it('does not stop the mic again on a re-render that leaves the dock open', () => {
    renderPanel()
    speechStop.mockClear()

    act(() => { useUIStore.getState().setCurrentProject('Other') })

    expect(speechStop).toHaveBeenCalledTimes(1)
  })
})
