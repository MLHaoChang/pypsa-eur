import { describe, expect, it } from 'vitest'
import { deriveEfficiencies, processRatesError } from './processUnit'

// eh_ammonia.py: 5.166 MWh_NH3/t, 5.93 MWh_H2/t, 0.2473 MWh_el per MWh_NH3 (= 1.2776 MWh_el/t).
const MWH_PER_T = 5.166
const H2_PER_T = 5.93
const EL_PER_MWH = 0.2473
const EL_KWH_PER_T = EL_PER_MWH * MWH_PER_T * 1000

describe('deriveEfficiencies', () => {
  it("reproduces eh_ammonia_staged's haber_bosch", () => {
    const r = deriveEfficiencies({ productPerT: MWH_PER_T, electricityKwhPerT: EL_KWH_PER_T, inputsPerT: [H2_PER_T] })
    expect(r.efficiency).toBeCloseTo(1 / EL_PER_MWH, 9)
    expect(r.extra).toEqual({ efficiency2: expect.closeTo(-(H2_PER_T / MWH_PER_T) / EL_PER_MWH, 9) })
  })

  it('converts kWh/t to MWh/t and signs every input negative', () => {
    const r = deriveEfficiencies({ productPerT: 2, electricityKwhPerT: 500, inputsPerT: [1, 0.25, 4] })
    expect(r.efficiency).toBe(4)
    expect(r.extra).toEqual({ efficiency2: -2, efficiency3: -0.5, efficiency4: -8 })
  })

  it('refuses rates that cannot make a unit', () => {
    expect(processRatesError({ productPerT: 1, electricityKwhPerT: 0, inputsPerT: [1] })).toMatch(/Electricity/)
    expect(processRatesError({ productPerT: 0, electricityKwhPerT: 1, inputsPerT: [1] })).toMatch(/Product/)
    expect(processRatesError({ productPerT: 1, electricityKwhPerT: 1, inputsPerT: [] })).toMatch(/1 to 3/)
    expect(processRatesError({ productPerT: 1, electricityKwhPerT: 1, inputsPerT: [1, 1, 1, 1] })).toMatch(/1 to 3/)
    expect(() => deriveEfficiencies({ productPerT: 1, electricityKwhPerT: 1, inputsPerT: [-1] })).toThrow()
  })
})

import { processUnitLink } from './processUnit'

describe('processUnitLink', () => {
  const form = {
    name: 'hb', bus0: 'hub', bus1: 'nh3', carrier: 'Haber-Bosch', p_nom: 100,
    product_per_t: MWH_PER_T, el_kwh_per_t: EL_KWH_PER_T,
    in1_bus: 'h2', in1_rate: H2_PER_T, in2_bus: '', in3_bus: '',
    committable: true, p_min_pu: 0.4, min_up_time: 24, min_down_time: 8,
  }

  it("gives the buses, efficiencies and UC attributes of eh_ammonia_staged's haber_bosch", () => {
    const l = processUnitLink(form)
    expect(l).toMatchObject({
      bus0: 'hub', bus1: 'nh3', bus2: 'h2', carrier: 'Haber-Bosch', eh_process: true,
      committable: true, p_min_pu: 0.4, min_up_time: 24, min_down_time: 8,
      start_up_cost: 0, shut_down_cost: 0, up_time_before: 0, down_time_before: 8,
    })
    expect(l.efficiency).toBeCloseTo(1 / EL_PER_MWH, 9)
    expect(l.efficiency2).toBeCloseTo(-(H2_PER_T / MWH_PER_T) / EL_PER_MWH, 9)
    expect(l.specific_energy_kwh_per_t).toBeCloseTo(EL_PER_MWH * MWH_PER_T * 1000, 9)
    for (const k of ['product_per_t', 'el_kwh_per_t', 'in1_bus', 'in1_rate', 'in2_bus', 'bus3', 'efficiency3'])
      expect(l).not.toHaveProperty(k)
  })

  it('packs inputs left and refuses a half-filled row', () => {
    const l = processUnitLink({ ...form, in1_bus: '', in1_rate: undefined, in2_bus: 'b', in2_rate: 1 })
    expect(l.bus2).toBe('b')
    expect(() => processUnitLink({ ...form, in2_bus: 'x' })).toThrow(/no rate/)
    expect(() => processUnitLink({ ...form, in3_rate: 2 })).toThrow(/no bus/)
  })
})
