// Multi-user edit-lock state shared by the workbench chrome (Task 14).
//
// In multi-user (auth) mode a project can be held by ONE editor at a time via
// a TTL'd server-side lock (backend: services/project_locks.py). Whoever holds
// the lock may edit; anyone else who opens the same scenario tree gets a
// READ-ONLY view. This module is the PURE core of that behaviour — a reducer
// that maps a lock acquire/heartbeat outcome to the UI state, plus a guard the
// destructive-action call sites consult. Kept dependency-free so it runs under
// the vitest `node` env with no DOM/axios/zustand baggage.

// The lock descriptor the backend serialises (`_serialize_project_lock`):
// who holds it, and whether that holder is the current user.
export interface LockInfo {
  holder_email: string
  yours: boolean
}

export interface LockState {
  // True when the current user may NOT mutate the active project — either
  // someone else holds the lock, or acquisition failed. Every destructive /
  // mutating affordance is gated on this being false (see `canMutate`).
  readOnly: boolean
  // Email of the current lock holder when known — surfaced in the banner so a
  // read-only viewer knows who to ask. null when no lock exists / holder
  // unknown / the lock is ours.
  holderEmail: string | null
}

// Neutral "you may edit" state. The default when auth is disabled or no lock
// machinery is in play (legacy single-user workbench) — so the legacy path is
// never accidentally read-only.
export const WRITABLE: LockState = { readOnly: false, holderEmail: null }

// Outcome of attempting to ACQUIRE (or heartbeat) a project lock.
//   ok:true  — the lock is ours; `lock` describes it (yours=true).
//   ok:false — the request was refused (HTTP 409) or otherwise failed; `lock`
//              describes the CURRENT holder (yours=false) when the backend
//              surfaced it in the error payload.
export type LockAcquireOutcome =
  | { ok: true; lock?: LockInfo | null }
  | { ok: false; lock?: LockInfo | null }

// Pure reducer: map an acquire/heartbeat outcome to the UI lock state.
// A successful acquire is always writable; a refusal is always read-only.
// When the outcome is a refusal we DON'T mark ourselves as the holder even if
// the payload's `yours` says so — a refusal means we don't hold it, full stop.
export function lockStateFromAcquire(outcome: LockAcquireOutcome): LockState {
  const holderEmail = outcome.lock?.holder_email ?? null
  if (outcome.ok) {
    // We hold the lock now. Don't advertise our own email as "someone else is
    // editing" — a writable state has no foreign holder to name.
    return { readOnly: false, holderEmail: outcome.lock?.yours ? null : holderEmail }
  }
  return { readOnly: true, holderEmail }
}

// Destructive / mutating actions consult this before running. Kept as a named
// helper (rather than an inline `!readOnly` at each call site) so the intent is
// greppable and the unit test documents the rule: read-only mode blocks every
// mutation.
export function canMutate(state: Pick<LockState, 'readOnly'>): boolean {
  return !state.readOnly
}
