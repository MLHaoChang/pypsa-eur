/** The generator form's hint under "Fuel curve slope" (homer-parity 02i). Worded exactly as the backend's
 *  `fuel_curve.slope_warning` (the pre-run warning `fuel_curve_slope_low`); `SLOPE_HINT_CASES` is the table both
 *  sides are tested against (`fuelSlopeHint.test.ts`, `backend/tests/test_fuel_slope_hint_parity.py`).
 *  Never blocks the save: it only shows. */
export const SLOPE_LABEL = 'Fuel curve slope'

export const SLOPE_HINT_CASES: ReadonlyArray<[string, string]> = [
  ['0.5', 'Fuel curve slope: 0.5 MWh of fuel per MWh generated means 200 % efficiency. Check the units: it is MWh of fuel per MWh generated (1 / efficiency), not an efficiency or a volume per kWh.'],
  ['0.85', 'Fuel curve slope: 0.85 MWh of fuel per MWh generated means 118 % efficiency. Check the units: it is MWh of fuel per MWh generated (1 / efficiency), not an efficiency or a volume per kWh.'],
  ['0.999', 'Fuel curve slope: 0.999 MWh of fuel per MWh generated means 100 % efficiency. Check the units: it is MWh of fuel per MWh generated (1 / efficiency), not an efficiency or a volume per kWh.'],
  ['0.25', 'Fuel curve slope: 0.25 MWh of fuel per MWh generated means 400 % efficiency. Check the units: it is MWh of fuel per MWh generated (1 / efficiency), not an efficiency or a volume per kWh.'],
]

/** The hint for the typed slope text, or null when the slope is empty, not a number, or not in (0, 1). */
export function fuelSlopeHint(text: string | undefined): string | null {
  const t = (text ?? '').trim()
  if (t === '') return null
  const slope = Number(t)
  if (!Number.isFinite(slope) || !(slope > 0 && slope < 1)) return null
  return `${SLOPE_LABEL}: ${Number(slope.toPrecision(6))} MWh of fuel per MWh generated means ${(100 / slope).toFixed(0)} % efficiency. `
    + 'Check the units: it is MWh of fuel per MWh generated (1 / efficiency), not an efficiency or a volume per kWh.'
}
