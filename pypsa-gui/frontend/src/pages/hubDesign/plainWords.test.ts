// P24-FE re-gate R8 and notes: every rule of the Improve card's glossary has a
// case, including the review effects the three templates do not reach.
import { describe, expect, it } from 'vitest'
import { costTileLabel, evidenceValue, feederAdvice, kpiMoney, plainIds, plainWords } from './plainWords'
import { GROUP_TITLES } from './holdWords'

describe('plainWords', () => {
  it('a feeder-bound title names the bus and feeder as the project does (GK A F6\')', () => {
    expect(plainWords('Certification not decided: it_bus could be cut off up to 43.8 h/yr while site_transformer is out'))
      .toBe('Certification not decided: IT bus could be cut off up to 43.8 h/yr while Site transformer is out')
  })

  it('a bare dtc_planning (dtc_critical_unserved effect)', () => {
    expect(plainWords('size what islanded operation needs (dtc_planning)'))
      .toBe('size what islanded operation needs (a plan for running without the grid)')
  })

  it('the VOLL effect of a not-established finding', () => {
    expect(plainWords('set VOLL to 5000 €/MWh (frontier and fmea_top need VOLL > 0)'))
      .toBe('set the price of undelivered energy to 5000 €/MWh (the cost-versus-reliability check '
        + 'and the top-risk check need a price above zero)')
  })

  it('frontier and fmea_top on their own', () => {
    expect(plainWords('re-run with the frontier stage')).toBe('re-run with the cost-versus-reliability check step')
    expect(plainWords('fmea_top: not established')).toBe('Top risks could not be worked out')
    expect(plainWords('the fmea_top ranking')).toBe('the top-risk check ranking')
  })

  it('the combined grid-loss phrase, N+1 and ‱', () => {
    expect(plainWords('(dtc_stress + dtc_planning)'))
      .toBe('(a grid-loss test and a plan for running without the grid)')
    expect(plainWords('an N+1 unit at ENS 2.5 ‱'))
      .toBe('a spare unit at unserved energy 2.5 parts per 10 000')
  })

  // P26 walkthrough (island microgrid, inconclusive): the Improve card read
  // "re-run the simulation with 1000 draws (was 500) to narrow the confidence
  // interval" — "draws" and "confidence interval" are statistics jargon.
  it('the inconclusive re-run effect (MC draws, confidence interval)', () => {
    expect(plainWords('re-run the MC with 1000 draws (was 500) to narrow the confidence interval'))
      .toBe('re-run the simulation with 1000 runs (was 500) to narrow the range of the estimate')
  })

  // P26 gate friction 3: finding titles end in "— outage-driven".
  it('outage-driven', () => {
    expect(plainWords('Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target — outage-driven'))
      .toBe('Not certified: expected shortfall 12.38 h/yr exceeds the 3 h/yr target — caused by equipment outages')
  })

  // P30 (C11, R8): the lone rule, with nothing around it.
  it('a lone dtc_planning effect', () => {
    expect(plainWords('dtc_planning')).toBe('a plan for running without the grid')
    expect(plainWords('add dtc_planning to the study'))
      .toBe('add a plan for running without the grid to the study')
  })

  // P30 (C12): "Critical demand unserved when … is lost: 12.5 MWh" kept the
  // unit; a number of MWh reads in words. A price per MWh is left alone.
  it('a number of MWh reads as megawatt-hours', () => {
    expect(plainWords('Critical demand unserved when grid_import is lost: 12.5 MWh'))
      .toBe('Critical demand unserved when grid_import is lost: 12.5 megawatt-hours')
    expect(plainWords('lost: 1,234.5 MWh')).toBe('lost: 1,234.5 megawatt-hours')
    expect(plainWords('3 MWh or 40 MWh')).toBe('3 megawatt-hours or 40 megawatt-hours')
    expect(plainWords('set VOLL to 5000 €/MWh')).toBe('set the price of undelivered energy to 5000 €/MWh')
    expect(plainWords('12 MWhx')).toBe('12 MWhx')
  })

  it('evidence values are rounded, lists too', () => {
    expect(evidenceValue(12.383928571428568)).toBe('12.38')
    expect(evidenceValue([7.68316774385459, 17.084689399002553])).toBe('[7.683, 17.08]')
    expect(evidenceValue(500)).toBe('500')
    expect(evidenceValue('fail')).toBe('fail')
  })
})

describe('plainWords: the dominant-risk line names the unit in plain words (UX audit M5)', () => {
  it('a raw id before "carries … of ranked Link risk" becomes its display name', () => {
    // The share is of the ranked connections only: the plain line keeps
    // that scope (asset-investment walkthrough 2026-11-17, issue 04).
    expect(plainWords('site_transformer carries 100% of ranked Link risk'))
      .toBe('Site transformer accounts for 100% of the outage risk from links')
    expect(plainWords('grid_import carries 62.5% of ranked Link risk'))
      .toBe('Grid import accounts for 62.5% of the outage risk from links')
  })
  it('leads with the share of all ranked risk when the review states it', () => {
    // Hand check: 657,000 / (657,000 + 3 × 302,114) = 42 %.
    expect(plainWords('hospital_feeder carries 100% of ranked Link risk (42% of all ranked risk)'))
      .toBe('Hospital feeder: 42% of the outage risk, and all of the risk from links')
  })
})

describe('plainWords: the dominant-risk line names links as the Guided assets list does', () => {
  it('uses the same word as the assets list group title for a Link', () => {
    const word = GROUP_TITLES.Link.toLowerCase()
    expect(plainWords('feeder_a carries 100% of ranked Link risk')).toContain(`risk from ${word}`)
    expect(plainWords('feeder_a carries 100% of ranked Link risk (42% of all ranked risk)'))
      .toContain(`risk from ${word}`)
  })
})

describe('plainWords: a Link share below 100% is stated, not rounded to "all"', () => {
  it('names the share of links when it is not all of it', () => {
    expect(plainWords('grid_import carries 62% of ranked Link risk (30% of all ranked risk)'))
      .toBe('Grid import: 30% of the outage risk, and 62% of the risk from links')
  })
})

describe('feederAdvice: the only feeder to a must-stay-on bus gets a feeder remedy', () => {
  it('names the bus as the project shows it', () => {
    expect(feederAdvice({ id: 'fmea_dominant_mode',
      evidence: { mode: 'hospital_feeder', feeds_critical_buses: ['hospital'] } }))
      .toBe('Add a second feeder or backup supply at Hospital. A spare unit or storage elsewhere cannot supply Hospital while Hospital feeder is out.')
  })
  it('says nothing for another finding or a Link that feeds no must-stay-on bus', () => {
    expect(feederAdvice({ id: 'fmea_dominant_mode', evidence: { mode: 'grid_import' } })).toBeNull()
    expect(feederAdvice({ id: 'dsr_off', evidence: { feeds_critical_buses: ['hospital'], mode: 'x' } })).toBeNull()
  })
})

describe('kpiMoney (UX audit M8): money in a sentence at the KPI tile rounding', () => {
  it('three significant figures, no trailing ".00"', () => {
    expect(kpiMoney(657_000)).toBe('€657 k')
    expect(kpiMoney(7_890_000)).toBe('€7.89 M')
  })
})

describe('plainIds (UX audit M5): raw ids inside a template note', () => {
  it('a snake_case id becomes its display name; other words stay', () => {
    expect(plainIds('IT load is critical (it_bus); cooling and offices are not.'))
      .toBe('IT load is critical (IT bus); cooling and offices are not.')
    expect(plainIds('The grid connection is capped at 40 MW.'))
      .toBe('The grid connection is capped at 40 MW.')
  })
})

describe('costTileLabel (UX review N3, decision B)', () => {
  it('a single-period cost is a yearly cost', () => {
    expect(costTileLabel('single_period', null)).toBe('Yearly cost of this design (before any shortfall costs)')
    expect(costTileLabel(undefined, [2030])).toBe('Yearly cost of this design (before any shortfall costs)')
  })
  it('a multi-period cost is the discounted cost over the plan, with its years', () => {
    expect(costTileLabel('npv_multi_period', [2027, 2030, 2035]))
      .toBe('Cost over the plan, 2027–2035 (discounted, before any shortfall costs)')
    expect(costTileLabel('npv_multi_period', undefined))
      .toBe('Cost over the plan (discounted, before any shortfall costs)')
    expect(costTileLabel('npv_multi_period', [2030])).not.toMatch(/Yearly|–/)
  })
})

describe('plainReason (issue 19, RAW-NAMES-1)', () => {
  it('puts the run limit, import model and Class-B terms in plain words', async () => {
    const { plainReason } = await import('./plainWords')
    expect(plainReason('not run: budget_solves exhausted (1/1) before this stage'))
      .toBe("not run: the study's run limit was reached (1 of 1 runs used) before this stage")
    expect(plainReason('import peak 12 MW (import_model=sampled_unit, import_firmness=firm)')).toBe('import peak 12 MW')
    expect(plainReason('no Class-B-eligible Links')).toBe('no links that carry the outage risk')
    expect(plainReason('pack has dsr_opt_in=True but no buses')).toBe('pack has demand response switched on but no buses')
    expect(plainReason('genset_new is capped')).toBe('Genset new is capped')
    expect(plainReason('Class-B Link sweep skipped')).toBe('link-by-link risk sweep skipped')
    // a name the reader must type stays as written
    expect(plainReason('Type `reserve_margin` or "p_nom_max" in the field')).toBe('Type `reserve_margin` or "p_nom_max" in the field')
  })
})
