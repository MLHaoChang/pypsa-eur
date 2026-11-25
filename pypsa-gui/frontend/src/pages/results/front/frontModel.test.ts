// The front's words and numbers (DC review 01, UX 28).
import { describe, expect, it } from 'vitest'
import {
  applyRefusal, co2Scale, CO2_HIGH, CO2_LOW, frontNotices, frontRows, frontUnits, frontWords,
  niceTicks, OUT_OF_DATE_TEXT, pickSource, startRefusal,
} from './frontModel'
import { degenerateRecord, DEGENERATE_TEXT, record, result, point } from './testFixtures'

const err = (status: number, detail: string) => ({ response: { status, data: { detail } } })

describe('frontModel', () => {
  it('reads one row per point, the failed point in words and not drawn', () => {
    const rows = frontRows(record())
    expect(rows).toHaveLength(5)
    expect(rows[0]).toMatchObject({ label: '#1', ok: true, level: '10', co2Cap: 'none', rel: 4, onFront: true })
    expect(rows[2].co2Cap).toBe('1,500')
    expect(rows[4]).toMatchObject({ ok: false, failure: 'No design meets this level', built: [] })
    expect(frontWords(rows[3])).toBe('dominated by #2')
    expect(frontWords(rows[0])).toBe('on the front')
  })

  it('names built capacities in the component words, MWh for a Store', () => {
    const b = frontRows(record())[0].built
    expect(b.map(x => `${x.kind} ${x.name} ${x.size}`)).toEqual([
      'Generator gas_turbine 10 MW', 'Storage bess 4 MW', 'Store h2_tank 20 MWh',
    ])
  })

  it('gives one unit per header, per year only when the horizon is a year', () => {
    const u = frontUnits(record())
    expect(u).toMatchObject({ basis: 'mc', relHeader: 'EUE (MWh/yr)', loleUnit: 'h/yr', levelHeader: 'Reserve margin (%)' })
    const pts = [point(0), point(1)]
    pts[1].mc!.time_basis = 'hours_per_horizon'
    expect(frontUnits(record({}, result(pts))).relHeader).toBe('EUE (MWh per modelled horizon)')
    const lp = frontUnits(record({}, result([point(0)], { reliability_basis: 'lp_ens', lever: 'ens_cap' })))
    expect(lp).toMatchObject({ basis: 'lp', relHeader: 'ENS in the dispatch model (MWh)', levelHeader: 'ENS target (‱ of demand)' })
  })

  it('scales CO2 from the lowest to the highest emissions, both ends known', () => {
    const s = co2Scale(frontRows(record()))
    expect([s.min, s.max]).toEqual([1500, 2000])
    expect(s.colour(1500)).toBe(CO2_LOW)
    expect(s.colour(2000)).toBe(CO2_HIGH)
  })

  it('the Expert study wins unless the user picked the Energy Hub one', () => {
    const f = record(), e = record({ source: 'eh_study', applicable: false })
    expect(pickSource(f, e)?.source).toBe('front')
    expect(pickSource(null, e)?.source).toBe('eh_study')
    expect(pickSource(f, e, 'eh_study')?.source).toBe('eh_study')
    expect(pickSource(f, null, 'eh_study')?.source).toBe('front')
  })

  it('carries the degenerate finding and the ranking note in plain words', () => {
    expect(frontNotices(degenerateRecord())).toEqual([DEGENERATE_TEXT])
    for (const t of frontNotices(degenerateRecord())) {
      expect(t).not.toMatch(/—|ens_cap|reserve_margin|lower_voll/)
    }
  })

  it('says the ruling (ag) text verbatim and never "stale"', () => {
    expect(OUT_OF_DATE_TEXT).toBe('Out of date: the inputs changed after the last run. Run again to see the figures.')
    const all = [OUT_OF_DATE_TEXT, ...[409, 422, 500].map(s => applyRefusal(err(s, 'x')).text)]
    for (const t of all) expect(t.toLowerCase()).not.toContain('stale')
  })

  it('a revision refusal is one plain line with a retry; the others are not retried', () => {
    expect(applyRefusal(err(409, 'expected network revision 4, it is 5'))).toEqual({
      text: 'The network changed while you were choosing, so nothing was applied.', retry: true,
    })
    expect(applyRefusal(err(409, 'the network has changed since the front study started; rerun it')).retry).toBe(false)
    expect(applyRefusal(err(422, 'the point does not match ... generators:gas')).text).not.toContain('generators:')
  })

  it('a budget refusal keeps the count and loses the "; " chain', () => {
    const t = startRefusal(err(422, '3 × 6 points = 18 solves exceed the 16-solve budget; drop levels or caps'))
    expect(t).toBe('The study was not started: 3 × 6 points = 18 solves exceed the 16-solve budget. Drop levels or caps.')
  })

  it('round ticks cover the range and end on a tick', () => {
    expect(niceTicks(0.815, 5.31)).toEqual([0, 2, 4, 6])
    expect(niceTicks(1_000_000, 1_200_000)).toEqual([1_000_000, 1_050_000, 1_100_000, 1_150_000, 1_200_000])
    expect(niceTicks(2, 2).length).toBeGreaterThan(1)
  })
})
