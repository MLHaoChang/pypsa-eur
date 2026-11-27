import { describe, expect, it } from 'vitest'
import type { EhLeverOption, EhLeverTable } from '../../api/simulation'
import { LEVER_SAME_COST, leverBaseLine, leverSameCostLines } from './leverBaseLine'

const fmt = (v: number) => `€${(v / 1e6).toFixed(1)}m`
const row = (kind: string, value: number, unit: string, cost: number | null,
  extra: Partial<EhLeverOption> = {}): EhLeverOption =>
  ({ kind, value, unit, status: 'ok', cost_at_target_eur: cost, ...extra })

describe('leverBaseLine', () => {
  it('names the base design cost and why rows differ', () => {
    const t: EhLeverTable = { base_cost_eur: 34_700_616, options: [] }
    expect(leverBaseLine(t, fmt)).toBe(
      'Base design: €34.7m at the same target. Each row changes one lever from the base, '
      + 'so its cost differs by what that change saves or adds.')
  })

  it('is absent when the study has no base cost', () => {
    expect(leverBaseLine({ options: [] }, fmt)).toBeNull()
    expect(leverBaseLine({ base_cost_eur: null, options: [] }, fmt)).toBeNull()
    expect(leverBaseLine(null, fmt)).toBeNull()
  })
})

describe('leverSameCostLines', () => {
  it('pins the sentences per lever kind', () => {
    expect(LEVER_SAME_COST).toEqual({
      storage_duration: {
        every: 'Same cost at every duration: the study does not use the extra hours.',
        some: 'Same cost at {values}: the study does not use the extra hours.',
      },
      import_cap: {
        every: 'Same cost at every import limit: the study does not use the extra import capacity.',
        some: 'Same cost at {values}: the study does not use the extra import capacity.',
      },
      import_energy: {
        every: 'Same cost at every import energy limit: the study does not use the extra import energy.',
        some: 'Same cost at {values}: the study does not use the extra import energy.',
      },
    })
  })

  it('says "every" only when every solved row of the kind shares one cost', () => {
    const t: EhLeverTable = { options: [
      row('storage_duration', 4, 'h', 34_225_422),
      row('storage_duration', 24, 'h', 34_225_422),
      row('storage_duration', 72, 'h', 34_225_422),
      row('import_cap', 0, 'MW', 64e6),
      row('import_cap', 25, 'MW', 45e6),
    ] }
    expect(leverSameCostLines(t)).toEqual([
      'Same cost at every duration: the study does not use the extra hours.',
    ])
  })

  it('names the rows when only some share a cost', () => {
    const t: EhLeverTable = { options: [
      row('storage_duration', 4, 'h', 3e6),
      row('storage_duration', 8, 'h', 3e6),
      row('storage_duration', 24, 'h', 2.9e6),
      row('import_cap', 25, 'MW', 5e6),
      row('import_cap', 50, 'MW', 4e6),
      row('import_cap', 75, 'MW', 4e6),
    ] }
    expect(leverSameCostLines(t)).toEqual([
      'Same cost at 4 h and 8 h: the study does not use the extra hours.',
      'Same cost at 50 MW and 75 MW: the study does not use the extra import capacity.',
    ])
  })

  it('lists three or more shared rows with commas', () => {
    const t: EhLeverTable = { options: [
      row('storage_duration', 2, 'h', 1e6),
      row('storage_duration', 4, 'h', 3e6),
      row('storage_duration', 8, 'h', 3e6),
      row('storage_duration', 24, 'h', 3e6),
    ] }
    expect(leverSameCostLines(t)).toEqual([
      'Same cost at 4 h, 8 h and 24 h: the study does not use the extra hours.',
    ])
  })

  it('ignores unsolved, ineffective and costless rows, and needs two rows', () => {
    const t: EhLeverTable = { options: [
      row('storage_duration', 4, 'h', 3e6),
      row('storage_duration', 24, 'h', 3e6, { status: 'infeasible' }),
      row('storage_duration', 72, 'h', 3e6, { ineffective: true }),
      row('storage_duration', 96, 'h', null),
      row('import_cap', 0, 'MW', 7e6, { status: 'optimal' }),
      row('import_cap', 25, 'MW', 7e6 + 1, { status: 'optimal' }),
    ] }
    expect(leverSameCostLines(t)).toEqual([])
  })

  it('has no line for a kind without words', () => {
    const t: EhLeverTable = { options: [
      row('redundancy', 1, '', 3e6), row('redundancy', 2, '', 3e6),
    ] }
    expect(leverSameCostLines(t)).toEqual([])
    expect(leverSameCostLines(null)).toEqual([])
  })
})
