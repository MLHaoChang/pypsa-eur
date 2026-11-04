import { describe, expect, it } from 'vitest'
import { FIELD_DISABLED, PRIMARY_BUTTON, PRIMARY_DISABLED, SECONDARY_BUTTON } from './buttonStyles'

describe('SECONDARY_BUTTON', () => {
  it('keeps the border and cursor of a disabled button from reacting to hover', () => {
    expect(SECONDARY_BUTTON).toContain('disabled:opacity-50')
    expect(SECONDARY_BUTTON).toContain('disabled:cursor-not-allowed')
    expect(SECONDARY_BUTTON).toContain('disabled:hover:border-border')
  })
})

describe('PRIMARY_BUTTON', () => {
  it('shares the disabled look with the secondary button', () => {
    expect(PRIMARY_BUTTON).toContain(PRIMARY_DISABLED)
    expect(PRIMARY_DISABLED).toContain('disabled:cursor-not-allowed')
    expect(PRIMARY_DISABLED).toContain('disabled:hover:bg-accent')
    expect(PRIMARY_DISABLED).toContain('disabled:opacity-50')
  })
})

describe('FIELD_DISABLED', () => {
  it('dims and shows the not-allowed cursor like the buttons', () => {
    expect(FIELD_DISABLED).toContain('disabled:opacity-50')
    expect(FIELD_DISABLED).toContain('disabled:cursor-not-allowed')
  })
})
