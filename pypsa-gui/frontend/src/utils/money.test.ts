// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { describe, expect, it } from 'vitest'
import { money } from './money'
import cases from './money.cases.json'
import { money as dcMoney } from '../pages/DcBackupSection'
import { moneyForGridRisk } from '../pages/results/GridRiskTab'

const N = '\u00a0'

describe('money', () => {
  it('pins the notation', () => {
    expect(money(1_200_000, 'EUR')).toBe(`€${N}1.2${N}m`)
    expect(money(350_000, 'EUR')).toBe(`€${N}350${N}k`)
    expect(money(12, 'EUR')).toBe(`€${N}12`)
    expect(money(-1_250_000, 'USD')).toBe(`$${N}1.3${N}m`)
    expect(money(8230)).toBe(`8.23${N}k`)
  })

  it('writes the cases the report writes (money.cases.json, also read by the backend test)', () => {
    for (const [v, want] of cases.cases as [number, string][]) expect(money(v, cases.currency)).toBe(want)
  })

  it('renders the same value identically on the grid-risk tab and the DC backup card', () => {
    for (const v of [12, 350_000, 1_200_000, 8230, 2_400_000_000]) {
      expect(moneyForGridRisk(v)).toBe(money(v, 'EUR'))
      expect(dcMoney(v, 'EUR')).toBe(money(v, 'EUR'))
    }
  })
})
