import { afterEach, describe, expect, it, vi } from 'vitest'
import { isMacPlatform, shiftEnterLabel } from './platformKeys'

afterEach(() => vi.unstubAllGlobals())

describe('platform shortcut labels', () => {
  it('macOS symbols on a Mac, words elsewhere', () => {
    vi.stubGlobal('navigator', { platform: 'MacIntel' })
    expect(isMacPlatform()).toBe(true)
    expect(shiftEnterLabel()).toBe('⇧⏎')
    vi.stubGlobal('navigator', { platform: 'Win32' })
    expect(shiftEnterLabel()).toBe('Shift+⏎')
  })
})
