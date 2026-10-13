// UX M8 (U3; assessment G-1): Guided without a usable assistant. One setup
// card in the centre; "Let the assistant do this" is disabled with the
// reason; unknown readiness changes nothing.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { useUIStore } from '../../../store/uiStore'
import { AssistantSetupCard } from './AssistantSetupCard'
import { DelegateButton, NO_ASSISTANT } from './CardShell'

const readiness = vi.hoisted(() => ({ ready: undefined as boolean | undefined }))
vi.mock('../../../hooks/useChatProfiles', async (orig) => ({
  ...(await orig<typeof import('../../../hooks/useChatProfiles')>()),
  useChatReadiness: () => ({ effectiveProfileId: null, ready: readiness.ready }),
}))

const mount = (ui: ReactElement) => render(
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)

beforeEach(() => { useUIStore.setState({ uiMode: 'guided', assistantDockOpen: false }) })
afterEach(() => cleanup())

describe('Guided without an assistant', () => {
  it('shows the setup card, which opens the assistant panel', () => {
    readiness.ready = false
    mount(<AssistantSetupCard />)
    fireEvent.click(screen.getByTestId('assistant-setup-open'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('disables "Let the assistant do this" and says why', () => {
    readiness.ready = false
    mount(<DelegateButton testId="d" text="do it" />)
    const b = screen.getByTestId('d') as HTMLButtonElement
    expect(b.disabled).toBe(true)
    expect(b.title).toBe(NO_ASSISTANT)
  })

  it.each([true, undefined])('ready=%s: no card, and the button works', ready => {
    readiness.ready = ready
    mount(<><AssistantSetupCard /><DelegateButton testId="d" text="do it" /></>)
    expect(screen.queryByTestId('assistant-setup-card')).toBeNull()
    expect((screen.getByTestId('d') as HTMLButtonElement).disabled).toBe(false)
  })
})
