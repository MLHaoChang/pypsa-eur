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
// and the solving case below fails.
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import LockBanner from './LockBanner'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'

function bannerText(): string {
  return screen.getByRole('status').textContent ?? ''
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

    render(<LockBanner />)

    expect(bannerText()).toMatch(/solving in the queue/i)
    expect(bannerText()).not.toMatch(/another user is editing/i)
    expect(bannerText()).not.toMatch(/lock is released/i)
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

    render(<LockBanner />)

    expect(bannerText()).toMatch(/solving in the queue/i)
    expect(bannerText()).not.toMatch(/ada@example\.com/)
  })
})
