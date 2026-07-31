// Pins the parsing of validation_service.py::_check_carrier_emissions's
// message format. That backend function appends, only when the catalog has
// an entry for the carrier:
//   f" The catalog value for '{carrier}' is {suggested} tCO2/MWh."
// (see pypsa-gui/backend/services/validation_service.py). This is the ONE
// place that regex runs — components must call parseSuggestedCo2Value
// instead of matching the message themselves.
import { describe, expect, it } from 'vitest'
import { parseSuggestedCo2Value } from './carrierZeroCo2'

describe('parseSuggestedCo2Value', () => {
  it('extracts the catalog value from a full carrier_zero_co2 message', () => {
    const message =
      "Carrier 'gas' looks like a fossil fuel but has co2_emissions = 0, " +
      "so every emissions figure for it is zero. The catalog value for " +
      "'gas' is 0.187 tCO2/MWh."
    expect(parseSuggestedCo2Value(message)).toBe(0.187)
  })

  it('extracts a different carrier/value pair (diesel)', () => {
    const message =
      "Carrier 'diesel' looks like a fossil fuel but has co2_emissions = 0, " +
      "so every emissions figure for it is zero. The catalog value for " +
      "'diesel' is 0.267 tCO2/MWh."
    expect(parseSuggestedCo2Value(message)).toBe(0.267)
  })

  it('returns null when the message carries no catalog value', () => {
    // _check_carrier_emissions omits the hint sentence entirely when
    // CARRIER_CATALOG has no entry for the carrier (suggested == 0.0 is
    // falsy in the backend's `if suggested else ""` branch).
    const message =
      "Carrier 'some_unlisted_fuel' looks like a fossil fuel but has " +
      "co2_emissions = 0, so every emissions figure for it is zero."
    expect(parseSuggestedCo2Value(message)).toBeNull()
  })

  it('returns null for an unrelated message', () => {
    expect(parseSuggestedCo2Value('Network has no buses.')).toBeNull()
  })

  it('returns null for empty input', () => {
    expect(parseSuggestedCo2Value('')).toBeNull()
  })

  it('ignores a non-finite parse result', () => {
    // Defensive: if the sentence format ever carries something Number()
    // can't parse cleanly, fail closed (no button) rather than surface NaN.
    expect(parseSuggestedCo2Value("The catalog value for 'gas' is NaN tCO2/MWh.")).toBeNull()
  })

  it('parses a carrier name containing an apostrophe', () => {
    // Not reachable today — every CARRIER_CATALOG key is apostrophe-free —
    // but the regex must not stop the name-span at an embedded quote. `.+`
    // backtracks to the LAST `' is `, which is the sentence's own closing
    // quote regardless of quotes embedded earlier in the name.
    const message =
      "Carrier \"Bob's gas\" looks like a fossil fuel but has co2_emissions " +
      "= 0, so every emissions figure for it is zero. The catalog value " +
      "for 'Bob's gas' is 0.187 tCO2/MWh."
    expect(parseSuggestedCo2Value(message)).toBe(0.187)
  })
})
