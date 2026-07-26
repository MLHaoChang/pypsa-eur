import { describe, it, expect } from 'vitest'
import { evaluateMutation, READ_ONLY_MUTATION_MESSAGE } from './mutationGuard'

describe('evaluateMutation', () => {
  it('allows mutation when not read-only', () => {
    const verdict = evaluateMutation(false)
    expect(verdict.allowed).toBe(true)
    expect(verdict.blockedMessage).toBeNull()
  })

  it('blocks mutation when read-only and returns the shared message', () => {
    const verdict = evaluateMutation(true)
    expect(verdict.allowed).toBe(false)
    expect(verdict.blockedMessage).toBe(READ_ONLY_MUTATION_MESSAGE)
  })

  it('uses one canonical read-only message', () => {
    expect(READ_ONLY_MUTATION_MESSAGE).toMatch(/read-only/i)
  })
})
