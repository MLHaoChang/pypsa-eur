// Shared read-only gate for workbench mutations (Task 14 follow-up).
//
// Multiple call sites (project rename in the header, scenario create/delete in
// the sidebar, save/undo/run) must refuse to mutate the active project while
// `uiStore.readOnly` is true — i.e. while another user holds the edit lock.
// Rather than sprinkling one-off `if (readOnly) return` checks (which silently
// no-op and drift in their messaging), every call site funnels through this
// single, greppable helper. Kept dependency-free (no toast / zustand / react)
// so it runs under the vitest `node` env and unit-documents the rule.

import { canMutate, type ReadOnlyReason } from './lockState'

// One canonical message per reason so every blocked mutation reads identically
// AND honestly. The single hardcoded "another user is editing this project" was
// wrong for every solve-induced refusal.
export const READ_ONLY_MUTATION_MESSAGE =
  'Read-only — another user is editing this project.'

export const SOLVING_MUTATION_MESSAGE =
  'Read-only — this project is solving in the queue. It becomes editable when the solve finishes.'

const MESSAGE_BY_REASON: Record<ReadOnlyReason, string | null> = {
  writable: null,
  'locked-by-user': READ_ONLY_MUTATION_MESSAGE,
  solving: SOLVING_MUTATION_MESSAGE,
}

export interface MutationVerdict {
  // True when the mutation may proceed.
  allowed: boolean
  // Message to surface (e.g. via a toast) when the mutation is blocked; null
  // when allowed.
  blockedMessage: string | null
}

// Pure evaluation of whether a mutation may proceed given the current
// read-only flag and WHY it is set. `reason` defaults to 'locked-by-user' so a
// call site that has not been widened yet keeps its historical message exactly.
export function evaluateMutation(
  readOnly: boolean,
  reason: ReadOnlyReason = 'locked-by-user',
): MutationVerdict {
  const allowed = canMutate({ readOnly })
  if (allowed) return { allowed: true, blockedMessage: null }
  return {
    allowed: false,
    blockedMessage: MESSAGE_BY_REASON[reason] ?? READ_ONLY_MUTATION_MESSAGE,
  }
}

// The same message table, exposed directly for surfaces that explain a
// read-only reason WITHOUT attempting a mutation — a button/row `title`
// attribute (hover hint) needs to say why it is disabled before the user ever
// clicks it, so it cannot go through `evaluateMutation`, which only speaks
// once a mutation is refused. Kept as a thin wrapper over `MESSAGE_BY_REASON`
// rather than a second literal table, so there is exactly one place that maps
// a reason to English — the module's whole reason for existing. null for
// 'writable' (nothing to explain); callers gate on `readOnly` first, so in
// practice they only ever see the non-null branch.
export function readOnlyMessage(reason: ReadOnlyReason): string | null {
  return MESSAGE_BY_REASON[reason]
}

// Banner copy — the two DEDICATED read-only banners (LockBanner, and the lock
// chip in WorkspacePanel) say why the project is read-only while it is
// read-only, rather than answering for one refused click. Their job is
// different from a toast's in two concrete ways, which is why they get their
// own table rather than reusing the one above:
//
//   1. Both render their own "Read-only" label, so the clause must not repeat
//      the "Read-only — " prefix every toast string carries.
//   2. A banner is the only surface that can name the lock HOLDER, and a
//      holder exists for 'locked-by-user' only — under 'solving' there is no
//      email to put anywhere, which is exactly how LockBanner ended up
//      printing "Another user is editing this project" during a queue solve.
//
// Still ONE place per surface-kind that maps a reason to English: the banners
// call this, everything that speaks about a refused mutation calls the table
// above. `holderEmail` is ignored for every reason but 'locked-by-user'.
export function readOnlyBannerMessage(
  reason: ReadOnlyReason,
  holderEmail: string | null = null,
): string | null {
  if (reason === 'solving') {
    return 'This project is solving in the queue — your changes are disabled until the solve finishes.'
  }
  if (reason === 'locked-by-user') {
    return holderEmail
      ? `${holderEmail} is currently editing this project — your changes are disabled until the lock is released.`
      : 'Another user is editing this project — your changes are disabled until the lock is released.'
  }
  return null
}
