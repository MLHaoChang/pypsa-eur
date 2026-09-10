import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// Escape stops dictation — and must not travel any further.
//
// App.tsx's window-level keydown handler also acts on Escape (close the
// compare rail, then the active slide panel). `preventDefault()` does NOT stop
// propagation, so before this the keystroke that stopped the mic also closed
// the panel the agent had just opened. App.tsx now skips Escape for editable
// targets as well, which is the far side of the same fix; this pins the near
// side, because with only the App-level guard in place nothing here would fail
// if `stopPropagation()` were deleted.
//
// A separate file from ChatPanel.test.tsx because it needs useSpeechToText
// mocked into a `listening` state for the whole module, which would change the
// composer's mic affordance for every other test in that file.

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

let windowSawEscape: number
const countEscape = (e: KeyboardEvent) => { if (e.key === 'Escape') windowSawEscape += 1 }

beforeEach(() => {
  windowSawEscape = 0
  speechStop.mockClear()
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'results', assistantDockOpen: true })
  // Stands in for App's own window-level handler — same target (window) and
  // same phase, so it observes exactly what App would.
  window.addEventListener('keydown', countEscape)
})

afterEach(() => {
  window.removeEventListener('keydown', countEscape)
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

// `speech.stop()` is also called once on mount, by the effect that stops the
// mic across project switches (`useEffect(… , [currentProject])`). Clearing
// after render keeps these assertions about the keystroke alone.
function renderAndSettle() {
  renderPanel()
  speechStop.mockClear()
}

describe('Escape in the composer while dictating', () => {
  it('stops the mic without letting the keystroke reach the window', () => {
    renderAndSettle()

    fireEvent.keyDown(screen.getByTestId('chat-input'), { key: 'Escape' })

    // Exactly once, from the composer's own onKeyDown. ChatPanel ALSO installs
    // a window-level Escape listener while dictating (so Escape stops the mic
    // from anywhere in the app); stopping propagation means that one does not
    // also fire for this keystroke, which is why the count is 1 and not 2.
    expect(speechStop).toHaveBeenCalledTimes(1)
    expect(windowSawEscape).toBe(0)
  })

  // The negative control. Swallowing every keystroke would pass the test above
  // while breaking the app's global shortcuts from the composer — and Escape
  // is only special here because dictation consumed it.
  it('lets other keys through to the window', () => {
    renderAndSettle()
    let windowSawLetter = 0
    const countLetter = (e: KeyboardEvent) => { if (e.key === 'a') windowSawLetter += 1 }
    window.addEventListener('keydown', countLetter)
    try {
      fireEvent.keyDown(screen.getByTestId('chat-input'), { key: 'a' })
    } finally {
      window.removeEventListener('keydown', countLetter)
    }

    expect(windowSawLetter).toBe(1)
    expect(speechStop).not.toHaveBeenCalled()
  })
})
