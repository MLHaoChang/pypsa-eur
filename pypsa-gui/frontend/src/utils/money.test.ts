// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { describe, expect, it } from 'vitest'
import { money, unitPrice } from './money'
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

  it('writes a per-unit price without k or m (UX 30, issue 28a)', () => {
    expect(unitPrice(1.725, 'EUR')).toBe('€\u00a01.73')
    expect(unitPrice(439.09, 'EUR')).toBe('€\u00a0439')
    expect(unitPrice(1150.4, 'EUR')).toBe('€\u00a01,150')
    expect(unitPrice(1_234_567, 'USD')).toBe('$\u00a01,234,567')
    expect(unitPrice(12.5, null)).toBe('12.5')
    // Signed, the minus before the symbol (ruling (ad)).
    expect(unitPrice(-1150.4, 'EUR')).toBe('-€\u00a01,150')
    expect(unitPrice(-1.725, 'EUR')).toBe('-€\u00a01.73')
    expect(unitPrice(-1000.2, 'EUR')).toBe('-€\u00a01,000')
  })
})
