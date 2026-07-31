// Parses the catalog value out of a `carrier_zero_co2` validation issue's
// message text. This is the ONE place that regex runs against that message —
// components must import this helper rather than matching the string
// themselves (per docs/superpowers/specs/2026-07-31-line-parameters-and-
// carrier-icons-design.md, Part C's "offer, never rewrite" fix).
//
// The backend (`services/validation_service.py::_check_carrier_emissions`)
// appends this sentence to the message ONLY when CARRIER_CATALOG has a
// non-zero entry for the carrier:
//   f" The catalog value for '{carrier}' is {suggested} tCO2/MWh."
// When the catalog has no entry, the sentence is omitted entirely — that is
// the signal that no one-click fix exists, and this helper returns null so
// callers know not to render a button.

const SUGGESTED_CO2_RE = /The catalog value for '[^']*' is ([0-9.]+) tCO2\/MWh\./

export function parseSuggestedCo2Value(message: string): number | null {
  const match = SUGGESTED_CO2_RE.exec(message)
  if (!match) return null
  const value = Number(match[1])
  return Number.isFinite(value) ? value : null
}
