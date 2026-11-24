// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { describe, expect, it } from 'vitest'
import { passNeedsLimit } from './CampusElectricalPanel'

describe('a check row with no limit never reads PASS', () => {
  it('reads not checked when the limit and the band are missing', () => {
    expect(passNeedsLimit('pass', null, null, null)).toBe('unknown')
    expect(passNeedsLimit('pass', undefined)).toBe('unknown')
  })
  it('keeps pass when a limit or a whole band is there, and keeps every other status', () => {
    expect(passNeedsLimit('pass', 31.5)).toBe('pass')
    expect(passNeedsLimit('pass', null, 0.95, 1.05)).toBe('pass')
    expect(passNeedsLimit('not_rated', null)).toBe('not_rated')
    expect(passNeedsLimit('fail', null)).toBe('fail')
  })
})
