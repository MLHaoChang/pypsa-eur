import { describe, expect, it } from 'vitest'
import {
  WRITABLE,
  canMutate,
  lockStateFromAcquire,
  type LockInfo,
} from './lockState'

const mine: LockInfo = { holder_email: 'me@example.com', yours: true }
const theirs: LockInfo = { holder_email: 'other@example.com', yours: false }

describe('lockStateFromAcquire', () => {
  it('a successful acquire is writable and names no foreign holder', () => {
    expect(lockStateFromAcquire({ ok: true, lock: mine })).toEqual({
      readOnly: false,
      holderEmail: null,
    })
  })

  it('a successful acquire with no lock payload is still writable', () => {
    expect(lockStateFromAcquire({ ok: true })).toEqual(WRITABLE)
    expect(lockStateFromAcquire({ ok: true, lock: null })).toEqual(WRITABLE)
  })

  it('a refusal is read-only and surfaces the current holder email', () => {
    expect(lockStateFromAcquire({ ok: false, lock: theirs })).toEqual({
      readOnly: true,
      holderEmail: 'other@example.com',
    })
  })

  it('a refusal without a lock payload is read-only with an unknown holder', () => {
    expect(lockStateFromAcquire({ ok: false })).toEqual({
      readOnly: true,
      holderEmail: null,
    })
    expect(lockStateFromAcquire({ ok: false, lock: null })).toEqual({
      readOnly: true,
      holderEmail: null,
    })
  })

  it('never trusts a refusal payload that (wrongly) claims the lock is ours', () => {
    // Defensive: a 409 means we do NOT hold the lock, regardless of the
    // serialised `yours` flag — so the state must stay read-only.
    const state = lockStateFromAcquire({ ok: false, lock: { ...mine } })
    expect(state.readOnly).toBe(true)
  })
})

describe('canMutate', () => {
  it('blocks every mutation while read-only', () => {
    expect(canMutate({ readOnly: true })).toBe(false)
  })

  it('allows mutations when writable', () => {
    expect(canMutate({ readOnly: false })).toBe(true)
    expect(canMutate(WRITABLE)).toBe(true)
  })

  it('gates the state produced by a lock refusal (integration with the reducer)', () => {
    const refused = lockStateFromAcquire({ ok: false, lock: theirs })
    expect(canMutate(refused)).toBe(false)
    const held = lockStateFromAcquire({ ok: true, lock: mine })
    expect(canMutate(held)).toBe(true)
  })
})
