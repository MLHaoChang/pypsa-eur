// WALK-IMPROVE-1: the cheaper battery duration sentence, from the review's
// `levers_best_option` finding, the levers table and the report's sizing rows.
import { describe, expect, it } from 'vitest'
import type {
  EhLeverOption, EhLeverTable, EhReferenceDesignReport, EhReviewFinding, EhSizedAsset,
} from '../../api/simulation'
import { cheaperDuration, designDurationHours } from './cheaperDuration'

const NBSP = / /g
const plain = (s: string | null) => s?.replace(NBSP, ' ') ?? null

function finding(evidence: Record<string, unknown> = {}): EhReviewFinding[] {
  return [{ id: 'levers_best_option', severity: 'info', title: 'Cheapest lever option',
    evidence: { kind: 'storage_duration', value: 24, cost_at_target_eur: 3_100_000,
      spread_eur: 400_000, ...evidence },
    recommendation: '', actions: [] }]
}
const opt = (value: number, cost: number | null, over: Partial<EhLeverOption> = {}): EhLeverOption =>
  ({ kind: 'storage_duration', value, unit: 'h', status: 'ok', cost_at_target_eur: cost,
    meets_target: true, ...over })
function levers(...options: EhLeverOption[]): EhLeverTable {
  return { kind: 'storage_duration',
    options: options.length ? options : [opt(4, 3_500_000), opt(24, 3_100_000), opt(72, 3_120_000)] }
}
const battery = (over: Partial<EhSizedAsset> = {}): EhSizedAsset =>
  ({ component: 'StorageUnit', name: 'battery', carrier: 'battery', built_mw: 2, built_mwh: 8,
    max_hours: 4, ...over })
function report(assets: EhSizedAsset[] = [battery()], cost: unknown = 3_600_000): EhReferenceDesignReport {
  return { archetype: 'off_grid', pack_hash: 'h', assumptions_hash: 'a',
    cost_at_target_eur: cost as number, completeness: {},
    sections: { sizing: { status: 'ok', payload: { assets } } } } as EhReferenceDesignReport
}

const SENTENCE = 'A 24 h battery also meets the energy-not-served cap at a system cost of € 3.1 m a year (design: 4 h, € 3.5 m)'

describe('cheaperDuration', () => {
  it('names the cheaper duration, the cap and the design (its own lever cost first)', () => {
    expect(plain(cheaperDuration(finding(), levers(), report()))).toBe(SENTENCE)
  })

  it('never says "goal": the cap is the energy-not-served cap', () => {
    expect(cheaperDuration(finding(), levers(), report())).not.toMatch(/goal/i)
  })

  it('falls back to the report cost when the design duration is not a lever option', () => {
    const s = cheaperDuration(finding(), levers(opt(24, 3_100_000), opt(72, 3_120_000)), report())
    expect(plain(s)).toBe('A 24 h battery also meets the energy-not-served cap at a system cost of € 3.1 m a year (design: 4 h, € 3.6 m)')
  })

  it('"An" before 8, 11 and 18 h', () => {
    const s = cheaperDuration(finding({ value: 8 }), levers(opt(4, 3_500_000), opt(8, 3_100_000)), report())
    expect(s).toMatch(/^An 8 h battery/)
  })

  it('hidden when the best option is the design duration', () => {
    expect(cheaperDuration(finding({ value: 4, cost_at_target_eur: 3_500_000 }), levers(), report())).toBeNull()
  })

  it('hidden when the best option is not cheaper than the design', () => {
    expect(cheaperDuration(finding({ cost_at_target_eur: 3_500_000 }),
      levers(opt(4, 3_500_000), opt(24, 3_500_000)), report())).toBeNull()
    expect(cheaperDuration(finding({ cost_at_target_eur: 3_900_000 }),
      levers(opt(4, 3_500_000), opt(24, 3_900_000)), report())).toBeNull()
  })

  it('hidden when the two costs round to the same figure', () => {
    expect(cheaperDuration(finding({ cost_at_target_eur: 3_480_000 }),
      levers(opt(4, 3_500_000), opt(24, 3_480_000)), report())).toBeNull()
  })

  it('hidden unless the levers table says the option meets the cap', () => {
    expect(cheaperDuration(finding(), levers(opt(4, 3_500_000), opt(24, 3_100_000, { meets_target: false })),
      report())).toBeNull()
    expect(cheaperDuration(finding(), levers(opt(4, 3_500_000), opt(24, 3_100_000, { meets_target: null })),
      report())).toBeNull()
    expect(cheaperDuration(finding(), null, report())).toBeNull()
  })

  it('hidden when the best option is not a storage duration', () => {
    expect(cheaperDuration(finding({ kind: 'import_cap' }), levers(), report())).toBeNull()
  })

  it('hidden with no finding', () => {
    expect(cheaperDuration([], levers(), report())).toBeNull()
    expect(cheaperDuration(undefined, levers(), report())).toBeNull()
  })

  it('hidden when a cost is not a number', () => {
    expect(cheaperDuration(finding({ cost_at_target_eur: null }), levers(), report())).toBeNull()
    expect(cheaperDuration(finding({ cost_at_target_eur: '3100000' }), levers(), report())).toBeNull()
    expect(cheaperDuration(finding({ value: '24' }), levers(), report())).toBeNull()
    // no own lever cost and no report cost: nothing to compare against
    expect(cheaperDuration(finding(), levers(opt(4, null), opt(24, 3_100_000)), report(undefined, null)))
      .toBeNull()
    expect(cheaperDuration(finding(), levers(opt(24, 3_100_000)), report(undefined, Number.NaN))).toBeNull()
  })

  it('a multi-period study states the discounted system cost over the plan, not a year', () => {
    const r = { ...report(), period_basis: 'npv_multi_period' } as EhReferenceDesignReport
    expect(plain(cheaperDuration(finding(), levers(), r))).toBe(
      'A 24 h battery also meets the energy-not-served cap at a system cost of € 3.1 m over the plan, discounted (design: 4 h, € 3.5 m)')
    const y = { ...report(), period_basis: 'single_period' } as EhReferenceDesignReport
    expect(plain(cheaperDuration(finding(), levers(), y))).toBe(SENTENCE)
  })

  it('hidden when the best option is the design duration, even when it reads cheaper than the report cost', () => {
    // The design's own row has no cost, so the design cost falls back to the report's € 3.6 m:
    // only the best-equals-design check keeps "A 4 h battery … (design: 4 h …)" off.
    expect(cheaperDuration(finding({ value: 4, cost_at_target_eur: 3_100_000 }),
      levers(opt(4, null), opt(24, 3_300_000)), report())).toBeNull()
  })

  it('reads only storage_duration rows: an import_cap row with the same value does not count', () => {
    const cap = opt(24, 3_100_000, { kind: 'import_cap', meets_target: true })
    expect(cheaperDuration(finding(), levers(cap, opt(4, 3_500_000), opt(24, 3_100_000, { meets_target: false })),
      report())).toBeNull()
    // and the design cost is not read from an import_cap row sharing the design's value
    const cheapCap = opt(4, 3_000_000, { kind: 'import_cap' })
    expect(plain(cheaperDuration(finding(), levers(cheapCap, opt(4, 3_500_000), opt(24, 3_100_000)), report())))
      .toBe(SENTENCE)
  })

  it('hidden when the design duration is unknown', () => {
    expect(cheaperDuration(finding(), levers(), null)).toBeNull()
    expect(cheaperDuration(finding(), levers(), report([]))).toBeNull()
  })
})

describe('designDurationHours', () => {
  it('the one max_hours the built StorageUnits share', () => {
    expect(designDurationHours(report([battery(), battery({ name: 'b2' })]))).toBe(4)
  })
  it('counts only the battery, not another StorageUnit of a different duration', () => {
    expect(designDurationHours(report([battery(),
      { component: 'StorageUnit', name: 'h2', carrier: 'H2', built_mw: 1, built_mwh: 100, max_hours: 100 }]))).toBe(4)
    expect(designDurationHours(report([
      { component: 'StorageUnit', name: 'h2', carrier: 'H2', built_mw: 1, built_mwh: 100, max_hours: 100 }]))).toBeNull()
  })
  it('null when the durations differ, none is built, or there is no sizing', () => {
    expect(designDurationHours(report([battery(), battery({ name: 'b2', max_hours: 6 })]))).toBeNull()
    expect(designDurationHours(report([battery({ built_mw: 0 })]))).toBeNull()
    expect(designDurationHours(report([battery({ max_hours: null })]))).toBeNull()
    expect(designDurationHours(report([{ component: 'Store', name: 's', carrier: 'h2', built_mwh: 5 }])))
      .toBeNull()
    expect(designDurationHours(null)).toBeNull()
  })
})
