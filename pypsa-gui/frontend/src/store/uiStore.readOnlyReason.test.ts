// R11 — the project the user is viewing is read-only, with the SOLVING reason,
// while a queue job runs on it.
//
// This is honest rather than defensive: once increment 1 lands the session on
// the solving context, the backend's global middleware already refuses every
// `/api/network/*` and `/api/io/*` write for the duration
// (main.py's solver-in-flight gate). The UI previously had no way to say so —
// `readOnly` was one boolean whose only message named another user.
import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'
import { WRITABLE } from '../utils/lockState'

describe('uiStore read-only reason', () => {
  beforeEach(() => {
    useUIStore.getState().setLockState(WRITABLE)
    useUIStore.getState().setSolvingReadOnly(false)
  })

  it('starts writable', () => {
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).toBe('writable')
  })

  it('reflects the edit-lock reason immediately after setLockState alone (no solve involved)', () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user',
    })
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('locked-by-user')
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com')
  })

  it('goes read-only with the solving reason while a queue job runs on it', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
  })

  it('returns to writable when the solve ends', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    useUIStore.getState().setSolvingReadOnly(false)
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).toBe('writable')
  })

  it('falls back to the edit lock when the solve ends but another user holds it', () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user',
    })
    useUIStore.getState().setSolvingReadOnly(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
    useUIStore.getState().setSolvingReadOnly(false)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('locked-by-user')
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com')
  })

  it('acquiring the edit lock does not clear a live solve', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    useUIStore.getState().setLockState(WRITABLE)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
  })
})
