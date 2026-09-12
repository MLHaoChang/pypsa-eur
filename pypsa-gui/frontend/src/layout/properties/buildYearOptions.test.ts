// The Build Year dropdown read cfg.investment_periods, a SolverConfig field no
// frontend code ever writes — Model Horizon writes n.investment_periods on the
// network. So the dropdown always fell to its generic 5-year grid, and a user
// with periods 2026/2027/2028 was offered 2025/2030/2035/... Since
// `build_year <= period` gates asset availability, picking one of those makes
// the asset invisible to the LP with no error.
import { describe, it, expect } from 'vitest'
import { buildYearOptions } from './buildYearOptions'

describe('buildYearOptions', () => {
  it('offers exactly the configured investment periods, sorted', () => {
    expect(buildYearOptions([2028, 2026, 2027], null, 2026))
      .toEqual([2026, 2027, 2028])
  })

  it('falls back to a 5-year grid when no periods are configured', () => {
    const opts = buildYearOptions([], null, 2026)
    expect(opts).toEqual([2025, 2030, 2035, 2040, 2045, 2050, 2055, 2060])
  })

  it('merges the asset current value in so a non-standard year is not lost', () => {
    expect(buildYearOptions([2030, 2040], 2033, 2026))
      .toEqual([2030, 2033, 2040])
  })

  it('does not duplicate a current value that is already an option', () => {
    expect(buildYearOptions([2030, 2040], 2040, 2026))
      .toEqual([2030, 2040])
  })

  it('ignores a zero / blank current value — PyPSA default, not a real year', () => {
    expect(buildYearOptions([2030, 2040], 0, 2026)).toEqual([2030, 2040])
    expect(buildYearOptions([2030, 2040], undefined, 2026)).toEqual([2030, 2040])
  })
})
