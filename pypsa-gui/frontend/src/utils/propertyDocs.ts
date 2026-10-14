// Hover tooltips for the Properties Panel rows the asset schema does not
// describe: a bus's coordinate pair (one row over x and y), and a line's
// impedances typed per km (the panel's own block; PyPSA stores absolute Ω and S).
// M5 folded the Bus card's field tooltips into its descriptors.
//
// S1d-3: every field of a described class (Generator, StorageUnit, Store,
// Load, Link, Line, Transformer) takes its tooltip from its descriptor's
// `help` (services/asset_schema/fields.py), so these entries were folded in
// there and removed; a test keeps a described field from coming back here.
//
// Keys are `<component>.<field>`. Descriptions are short (1–2 sentences),
// in PyPSA's terminology.

export const PROPERTY_DOCS: Record<string, string> = {
  // ── Bus ──────────────────────────────────────────────────────────────────
  'bus.coordinates':
    'Geographic position (longitude °E, latitude °N) used to place the bus on the canvas.',

  // ── Line impedances, typed per km ────────────────────────────────────────
  'line.r':
    'Series resistance per km (Ω/km). UI multiplies by length on save; PyPSA stores absolute Ω and then converts to per-unit via r_pu = r / v_nom². Rule of thumb: ~0.03 Ω/km on a 380 kV overhead line. Drives Ohmic losses when transmission_losses is enabled.',
  'line.x':
    'Series reactance per km (Ω/km). UI multiplies by length on save; PyPSA stores absolute Ω. The dominant term in DC OPF — flows split between parallel paths inversely proportional to x. Typical 380 kV value: ~0.3 Ω/km.',
  'line.b':
    'Shunt susceptance per km (S/km). UI multiplies by length on save; PyPSA stores absolute Siemens. Models charging current; usually negligible for short overhead lines and important for long underground/submarine cables.',
}

// Convenience accessor — returns undefined when the key isn't in the map so
// callers can pass it straight into a `tip?: string` prop.
export function tip(key: string): string | undefined {
  return PROPERTY_DOCS[key]
}
