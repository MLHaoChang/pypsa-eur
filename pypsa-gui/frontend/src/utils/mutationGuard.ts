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
