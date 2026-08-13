// D22's six reveal rules. Pure: no React, no DOM.
import { describe, expect, it } from 'vitest'
import { isRequired, isRevealed, requiredPairMessage } from './attributeCatalog'

const base = { mode: 'lopf', extendable: false, committable: false, noSlackBus: false }

describe('rule 1 — extendable reveals the bounds, unconditionally', () => {
  it('reveals p_nom_min and p_nom_max when extendable', () => {
    const ctx = { ...base, extendable: true }
    expect(isRevealed('p_nom_min', ctx)).toBe(true)
    expect(isRevealed('p_nom_max', ctx)).toBe(true)
  })

  it('hides them when not extendable — criterion 34', () => {
    expect(isRevealed('p_nom_min', base)).toBe(false)
    expect(isRevealed('p_nom_max', base)).toBe(false)
  })

  it('is NOT mode-gated: a reveal asserts nothing and cannot over-report', () => {
    const ctx = { ...base, mode: 'pf', extendable: true }
    expect(isRevealed('p_nom_min', ctx)).toBe(true)
  })

  it('covers the e_nom and s_nom families too', () => {
    const ctx = { ...base, extendable: true }
    expect(isRevealed('e_nom_min', ctx)).toBe(true)
    expect(isRevealed('s_nom_max', ctx)).toBe(true)
  })

  it('leaves an unrelated column revealed', () => {
    expect(isRevealed('marginal_cost', base)).toBe(true)
  })
})

describe('rule 5 — committable reveals the unit-commitment fields', () => {
  const uc = ['start_up_cost', 'shut_down_cost', 'min_up_time', 'min_down_time',
    'ramp_limit_up', 'ramp_limit_down', 'p_min_pu']

  it('reveals them when committable', () => {
    const ctx = { ...base, committable: true }
    for (const c of uc) expect(isRevealed(c, ctx)).toBe(true)
  })

  it('hides start_up_cost when not committable', () => {
    expect(isRevealed('start_up_cost', base)).toBe(false)
  })
})

describe('rules 2-4 — required only under lopf', () => {
  it('rule 4: a non-extendable asset requires p_nom under lopf', () => {
    expect(isRequired('p_nom', { ...base, mode: 'lopf', extendable: false })).toBe(true)
  })

  it('rule 4 does not fire under pf', () => {
    expect(isRequired('p_nom', { ...base, mode: 'pf', extendable: false })).toBe(false)
  })

  it('rule 3: an extendable asset requires both bounds under lopf', () => {
    const ctx = { ...base, mode: 'lopf', extendable: true }
    expect(isRequired('p_nom_min', ctx)).toBe(true)
    expect(isRequired('p_nom_max', ctx)).toBe(true)
  })

  it('rule 3 does not fire under pf — criterion 32', () => {
    const ctx = { ...base, mode: 'pf', extendable: true }
    expect(isRequired('p_nom_min', ctx)).toBe(false)
  })

  it('rule 2: the cost pair is marked on BOTH members under lopf + extendable', () => {
    const ctx = { ...base, mode: 'lopf', extendable: true }
    expect(isRequired('capital_cost', ctx)).toBe(true)
    expect(isRequired('overnight_cost', ctx)).toBe(true)
  })

  it('rule 2 does not fire when not extendable', () => {
    expect(isRequired('capital_cost', { ...base, mode: 'lopf' })).toBe(false)
  })

  it('rule 2 does not fire under pf', () => {
    expect(isRequired('capital_cost', { ...base, mode: 'pf', extendable: true })).toBe(false)
  })

  it('states the disjunction, so the message cannot read as "capital_cost only"', () => {
    const msg = requiredPairMessage({ ...base, mode: 'lopf', extendable: true })
    expect(msg).toContain('capital_cost')
    expect(msg).toContain('overnight_cost')
    expect(msg?.toLowerCase()).toContain('or')
  })

  it('has no pair message when the rule does not apply', () => {
    expect(requiredPairMessage(base)).toBe(null)
  })
})

describe('rule 6 — control, network-wide, pf only', () => {
  it('marks control required under pf with no Slack bus', () => {
    expect(isRequired('control', { ...base, mode: 'pf', noSlackBus: true })).toBe(true)
  })

  it('clears the moment any bus is Slack — criterion 33', () => {
    expect(isRequired('control', { ...base, mode: 'pf', noSlackBus: false })).toBe(false)
  })

  it('does not fire under lopf, even though the AC-PF chain may run', () => {
    // _check_stage2_ac_pf emits a _warn, not an _err, and is satisfied by a
    // Slack generator OR a Slack bus OR an ac_pf_slack_bus override. A warning
    // never blocks a launch, so `required` would be a lie.
    expect(isRequired('control', { ...base, mode: 'lopf', noSlackBus: true })).toBe(false)
  })
})
