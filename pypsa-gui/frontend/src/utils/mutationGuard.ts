// Shared read-only gate for workbench mutations (Task 14 follow-up).
//
// Multiple call sites (project rename in the header, scenario create/delete in
// the sidebar, save/undo/run) must refuse to mutate the active project while
// `uiStore.readOnly` is true — i.e. while another user holds the edit lock.
// Rather than sprinkling one-off `if (readOnly) return` checks (which silently
// no-op and drift in their messaging), every call site funnels through this
// single, greppable helper. Kept dependency-free (no toast / zustand / react)
// so it runs under the vitest `node` env and unit-documents the rule.

import { canMutate } from './lockState'

// One canonical message so every blocked mutation reads identically.
export const READ_ONLY_MUTATION_MESSAGE =
  'Read-only — another user is editing this project.'

export interface MutationVerdict {
  // True when the mutation may proceed.
  allowed: boolean
  // Message to surface (e.g. via a toast) when the mutation is blocked; null
  // when allowed.
  blockedMessage: string | null
}

// Pure evaluation of whether a mutation may proceed given the current
// read-only flag. Wraps the shared `canMutate` rule so the intent is
// greppable and the unit test documents the behaviour at every call site.
export function evaluateMutation(readOnly: boolean): MutationVerdict {
  const allowed = canMutate({ readOnly })
  return { allowed, blockedMessage: allowed ? null : READ_ONLY_MUTATION_MESSAGE }
}
