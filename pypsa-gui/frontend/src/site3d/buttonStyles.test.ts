import { describe, expect, it } from 'vitest'
import { SECONDARY_BUTTON } from './buttonStyles'

describe('SECONDARY_BUTTON', () => {
  it('keeps the border and cursor of a disabled button from reacting to hover', () => {
    expect(SECONDARY_BUTTON).toContain('disabled:opacity-50')
    expect(SECONDARY_BUTTON).toContain('disabled:cursor-not-allowed')
    expect(SECONDARY_BUTTON).toContain('disabled:hover:border-border')
  })
})
