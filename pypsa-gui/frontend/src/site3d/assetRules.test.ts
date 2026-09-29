import { describe, it, expect } from 'vitest'
import { DEFAULT_ASSET_RULES, validateRules } from './assetRules'

describe('asset rules', () => {
  it('the defaults validate and are frozen', () => {
    expect(() => validateRules(DEFAULT_ASSET_RULES)).not.toThrow()
    expect(Object.isFrozen(DEFAULT_ASSET_RULES)).toBe(true)
  })
  it.each([
    ['mwhPerBessContainer', 0], ['haPerMwpPv', -1], ['mwPerTurbine', NaN], ['dataHallM2PerMw', Infinity],
  ] as const)('rejects %s = %s, naming the field', (k, v) => {
    expect(() => validateRules({ ...DEFAULT_ASSET_RULES, [k]: v })).toThrow(new RegExp(k))
  })
  it('rejects a container with a non-positive side', () => {
    expect(() => validateRules({ ...DEFAULT_ASSET_RULES, container20ft: [6.1, 0, 2.9] })).toThrow(/container20ft/)
  })
})
