import { describe, expect, it } from 'vitest'
import {
  WRITABLE,
  canMutate,
  effectiveLockState,
  lockFromErrorData,
  lockRefusalCode,
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
      reason: 'writable',
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
      reason: 'locked-by-user',
    })
  })

  it('a refusal without a lock payload is read-only with an unknown holder', () => {
    expect(lockStateFromAcquire({ ok: false })).toEqual({
      readOnly: true,
      holderEmail: null,
      reason: 'locked-by-user',
    })
    expect(lockStateFromAcquire({ ok: false, lock: null })).toEqual({
      readOnly: true,
      holderEmail: null,
      reason: 'locked-by-user',
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

describe('effectiveLockState', () => {
  it('is writable when neither input holds', () => {
    expect(effectiveLockState(false, false)).toEqual({ readOnly: false, reason: 'writable' })
  })

  it('reports locked-by-user when only the edit lock holds', () => {
    expect(effectiveLockState(true, false)).toEqual({ readOnly: true, reason: 'locked-by-user' })
  })

  it('reports solving when a queue job holds the project', () => {
    expect(effectiveLockState(false, true)).toEqual({ readOnly: true, reason: 'solving' })
  })

  it('prefers solving when both hold — it is the one that clears on its own', () => {
    expect(effectiveLockState(true, true)).toEqual({ readOnly: true, reason: 'solving' })
  })
})

describe('lockStateFromAcquire reasons', () => {
  it('tags a successful acquire writable', () => {
    expect(lockStateFromAcquire({ ok: true }).reason).toBe('writable')
  })

  it('tags a refusal locked-by-user', () => {
    expect(lockStateFromAcquire({ ok: false }).reason).toBe('locked-by-user')
  })
})

// ── The project_locked wire shape, read from all three emitters (I1) ────────
//
// The backend refuses a write under a foreign lock from three places, and
// before the unification they did not agree on a body:
//
//   1. `_enforce_project_lock` (route edges)  detail: {error_kind, message, lock}
//   2. the write middleware (main.py)         detail: <prose>, code: project_locked
//   3. the solve-queue enqueue check          detail: {error_kind, message}
//
// (2) carried no holder, so the read-only banner had nobody to name, and (2)
// was the only one the toast-suppression keying could see. All three now send
// the same `detail` object; the middleware keeps its top-level `code` for
// backwards compatibility. These two readers must accept every variant.

describe('lockRefusalCode', () => {
  it('reads the middleware top-level code', () => {
    expect(lockRefusalCode({
      code: 'project_locked',
      detail: { error_kind: 'project_locked', message: 'held', lock: theirs },
    })).toBe('project_locked')
  })

  it('reads the route-edge detail.error_kind, which has no top-level code', () => {
    expect(lockRefusalCode({
      detail: { error_kind: 'project_locked', message: 'held', lock: theirs },
    })).toBe('project_locked')
  })

  it('reads the enqueue refusal', () => {
    expect(lockRefusalCode({
      detail: { error_kind: 'project_locked', message: 'held', lock: null },
    })).toBe('project_locked')
  })

  it('is null for any other failure, so those still toast', () => {
    expect(lockRefusalCode({ detail: 'Project not found' })).toBeNull()
    expect(lockRefusalCode({ code: 'solver_in_flight', detail: 'busy' })).toBeNull()
    expect(lockRefusalCode({ detail: { error_kind: 'project_exists' } })).toBeNull()
    expect(lockRefusalCode(undefined)).toBeNull()
    expect(lockRefusalCode(null)).toBeNull()
    expect(lockRefusalCode('plain string body')).toBeNull()
  })
})

describe('lockFromErrorData', () => {
  it('extracts the holder from the shared detail object', () => {
    expect(lockFromErrorData({
      detail: { error_kind: 'project_locked', message: 'held', lock: theirs },
    })).toEqual(theirs)
  })

  it('extracts it from the middleware body too, now that it carries one', () => {
    expect(lockFromErrorData({
      code: 'project_locked',
      detail: { error_kind: 'project_locked', message: 'held', lock: theirs },
    })).toEqual(theirs)
  })

  it('returns null when the serialiser could not resolve the lock', () => {
    // M1: `_serialize_project_lock` degrades a DB error to `lock: null` rather
    // than 500-ing a correct refusal — the banner then says "another user".
    expect(lockFromErrorData({ detail: { error_kind: 'project_locked', lock: null } })).toBeNull()
  })

  it('returns null for bodies with no lock member at all', () => {
    expect(lockFromErrorData({ detail: 'Project not found' })).toBeNull()
    expect(lockFromErrorData(undefined)).toBeNull()
  })
})
