import { describe, expect, it } from 'vitest'
import { keyFieldPlaceholder, probeMessage } from './localSettings'

describe('keyFieldPlaceholder', () => {
  it('prompts for a key when none is stored', () => {
    expect(keyFieldPlaceholder(null)).toBe('sk-ant-…')
    expect(keyFieldPlaceholder({ key_set: false, key_hint: null, log_path: '/l' }))
      .toBe('sk-ant-…')
  })

  it('shows the hint when one is available', () => {
    expect(keyFieldPlaceholder({ key_set: true, key_hint: '7f3a', log_path: '/l' }))
      .toBe('Key set — ending 7f3a')
  })

  it('still reports a stored key when the hint was withheld', () => {
    // The backend returns a null hint for a key under eight characters,
    // where "the last four" would disclose most of it.
    expect(keyFieldPlaceholder({ key_set: true, key_hint: null, log_path: '/l' }))
      .toBe('Key set')
  })
})

describe('probeMessage', () => {
  it('reports a verified key as verified', () => {
    expect(probeMessage('valid').tone).toBe('ok')
  })

  it('distinguishes rejected from unreachable', () => {
    // The whole point: a key we could not check must never render the same
    // as a key Anthropic accepted, nor the same as one it refused.
    const rejected = probeMessage('rejected')
    const unreachable = probeMessage('unreachable')

    expect(rejected.tone).toBe('error')
    expect(unreachable.tone).toBe('warn')
    expect(rejected.text).not.toBe(unreachable.text)
  })

  it('says the key was saved even when it could not be checked', () => {
    expect(probeMessage('unreachable').text).toMatch(/saved/i)
  })

  it('reports a cleared key', () => {
    expect(probeMessage('cleared').tone).toBe('ok')
    expect(probeMessage('cleared').text).toMatch(/removed/i)
  })

  it('reports a missing SDK distinctly', () => {
    expect(probeMessage('sdk_not_installed').tone).toBe('error')
  })
})
