// Hover tooltips for the Properties Panel fields the asset schema does not
// describe: the Bus card, and a line's impedances typed per km (the panel's
// own block; PyPSA stores absolute Ω and S).
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
  'bus.name':
    'Unique identifier for the bus. Must be unique across the network and is used as foreign key by every component connected to it.',
  'bus.v_nom':
    'Nominal voltage of the bus in kV. Lines are coloured by voltage class on the canvas; AC topology and transformer ratios reference this value.',
  'bus.carrier':
    'Energy carrier modelled at this bus (AC / DC / H₂ / heat / gas / …). Determines which assets can attach and how the optimisation handles its energy balance.',
  'bus.control':
    'Power-flow control mode for non-linear AC solves: PQ (load), PV (regulated voltage), or Slack (reference). For linear OPF only the slack matters.',
  'bus.country':
    'ISO country code for grouping and policy constraints (CO₂ caps, country-level statistics, country-coloured maps).',
  'bus.x':
    'Geographic longitude (°E). Used to position the bus on the canvas; not used by the optimisation itself.',
  'bus.y':
    'Geographic latitude (°N). Used to position the bus on the canvas; not used by the optimisation itself.',
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
