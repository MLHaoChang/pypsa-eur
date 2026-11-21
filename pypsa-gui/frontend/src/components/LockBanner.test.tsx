// LockBanner must name the REASON, not assume the lock.
//
// The banner folded `readOnly` + `lockHolderEmail` into two sentences, both
// about another user holding the edit lock. Since the queue work `readOnly`
// has a second cause — a queue job solving this project — and in that state
// there is no holder, so the banner took the no-holder branch and printed
// "Another user is editing this project" at a user who is the only user.
// That is the exact sentence the reason widening exists to stop being a lie.
//
// Revert the `readOnlyBannerMessage(reason, holder)` wiring in LockBanner.tsx
// and the lock cases below fail.
//
// Run progress: the solving reason is said once, by the run strip under the
// header (`runStatus/RunStrip`), next to the run's progress; LockBanner yields
// to it. The solving cases render both surfaces, as the app does.
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it } from 'vitest'
import LockBanner from './LockBanner'
import RunStrip from './runStatus/RunStrip'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'

function bannerText(): string {
  return screen.getByRole('status').textContent ?? ''
}

/** What the user reads across the read-only banner and the run strip. */
function renderBoth(): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { container } = render(
    <QueryClientProvider client={qc}><LockBanner /><RunStrip /></QueryClientProvider>,
  )
  return container.textContent ?? ''
}

beforeEach(() => {
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
})

describe('LockBanner', () => {
  it('renders nothing while the project is writable', () => {
    render(<LockBanner />)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('blames the queue solve — never another user — when that is the reason', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
    expect(useUIStore.getState().lockHolderEmail).toBeNull()

    const text = renderBoth()

    expect(text).toMatch(/being solved: edits open again when the run ends/i)
    expect(text).not.toMatch(/another user is editing/i)
    expect(text).not.toMatch(/lock is released/i)
    // Said once: the banner yields, so the sentence is not shown twice.
    expect(text.match(/solv/gi)).toHaveLength(1)
  })

  it('names the holder when another user actually holds the edit lock', () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'ada@example.com', reason: 'locked-by-user',
    })

    render(<LockBanner />)

    expect(bannerText()).toMatch(/ada@example\.com is currently editing this project/i)
    expect(bannerText()).toMatch(/lock is released/i)
  })

  it('falls back to "another user" only when the lock reason has no known holder', () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: null, reason: 'locked-by-user',
    })

    render(<LockBanner />)

    expect(bannerText()).toMatch(/another user is editing this project/i)
  })

  it('prefers the solving reason over a stale lock holder', () => {
    // Both inputs set: the store's fold makes `solving` win (it has a definite
    // end and a different remedy), and the banner must follow the fold rather
    // than the holder email it can still see.
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'ada@example.com', reason: 'locked-by-user',
    })
    useUIStore.getState().setSolvingReadOnly(true)

    const text = renderBoth()

    expect(text).toMatch(/being solved/i)
    expect(text).not.toMatch(/ada@example\.com/)
  })
})
