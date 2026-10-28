/**
 * The "Process unit" creation type's arithmetic (grid-to-process ticket 07).
 *
 * The user enters rates per tonne of product; PyPSA wants a Link's efficiencies. bus0 carries the
 * electricity, bus1 the product, bus2..bus4 the inputs, as `eh_ammonia_staged` builds `haber_bosch`:
 *
 *   efficiency   = product per t / electricity per t          (product per MWh_el)
 *   efficiencyN  = -(input N per t) / electricity per t       (negative: the Link draws bus N)
 *
 * Electricity is typed in kWh/t and used in MWh/t; every other rate is in its bus's own unit per t.
 */

export const MAX_PROCESS_INPUTS = 3

export interface ProcessUnitRates {
  /** Units of the product bus per tonne of product (MWh_NH3/t for an energy-unit bus; 1 for a bus in t). */
  productPerT: number
  /** Electricity, kWh per tonne of product. */
  electricityKwhPerT: number
  /** One entry per input (1..3): units of its bus per tonne of product. */
  inputsPerT: number[]
}

export interface ProcessUnitEfficiencies {
  efficiency: number
  /** `efficiency2` .. `efficiency4`, in input order. */
  extra: Record<string, number>
}

/** Why the rates cannot make a unit, or null. */
export function processRatesError(r: ProcessUnitRates): string | null {
  if (!(r.productPerT > 0)) return 'Product per tonne must be above 0'
  if (!(r.electricityKwhPerT > 0)) return 'Electricity must be above 0 kWh/t'
  if (r.inputsPerT.length < 1 || r.inputsPerT.length > MAX_PROCESS_INPUTS) return 'Enter 1 to 3 inputs'
  if (r.inputsPerT.some(x => !(x > 0))) return 'Every input rate must be above 0'
  return null
}

export function deriveEfficiencies(r: ProcessUnitRates): ProcessUnitEfficiencies {
  const err = processRatesError(r)
  if (err) throw new Error(err)
  const elMwhPerT = r.electricityKwhPerT / 1000
  const extra: Record<string, number> = {}
  r.inputsPerT.forEach((x, i) => { extra[`efficiency${i + 2}`] = -(x / elMwhPerT) })
  return { efficiency: r.productPerT / elMwhPerT, extra }
}

/** The form's keys for input row `i` (1..3). */
export const inputKeys = (i: number) => ({ bus: `in${i}_bus`, rate: `in${i}_rate` })

/**
 * The creation form's payload for a process unit → the Link `createLink` takes. `p` holds the form's
 * fields (`product_per_t`, `el_kwh_per_t`, `inN_bus` / `inN_rate`, and the plain Link fields); the
 * rate fields are replaced by bus2.. and efficiency, efficiency2.. . The UC attributes mirror
 * `haber_bosch`: the restart time is `min_down_time` (start_up_cost stays 0), and the unit starts
 * stopped and free to start (`up_time_before` 0, `down_time_before` = `min_down_time`).
 */
export function processUnitLink(p: Record<string, unknown>): Record<string, unknown> {
  const out = { ...p }
  const num = (v: unknown) => (v === undefined || v === '' ? NaN : Number(v))
  const inputs: { bus: string; rate: number }[] = []
  for (let i = 1; i <= MAX_PROCESS_INPUTS; i++) {
    const k = inputKeys(i)
    const bus = String(out[k.bus] ?? '').trim()
    const rate = num(out[k.rate])
    delete out[k.bus]
    delete out[k.rate]
    if (!bus && Number.isNaN(rate)) continue
    if (!bus) throw new Error(`Input ${i} has a rate but no bus`)
    if (Number.isNaN(rate)) throw new Error(`Input ${i} has a bus but no rate`)
    inputs.push({ bus, rate })
  }
  const rates: ProcessUnitRates = {
    productPerT: num(out.product_per_t), electricityKwhPerT: num(out.el_kwh_per_t),
    inputsPerT: inputs.map(x => x.rate),
  }
  const { efficiency, extra } = deriveEfficiencies(rates)
  delete out.product_per_t
  delete out.el_kwh_per_t
  inputs.forEach((x, i) => { out[`bus${i + 2}`] = x.bus })
  Object.assign(out, extra, { efficiency, eh_process: true, specific_energy_kwh_per_t: rates.electricityKwhPerT })
  if (out.committable) {
    out.up_time_before = 0
    out.down_time_before = Number(out.min_down_time ?? 0) || 0
    out.start_up_cost = 0
    out.shut_down_cost = 0
  }
  return out
}
