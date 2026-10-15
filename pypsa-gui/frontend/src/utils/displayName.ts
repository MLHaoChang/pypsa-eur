// Raw component ids in plain words (UX overhaul S6, finding G1: Guided showed
// `grid_import (40 MW)`, `it_bus`, `site_transformer`). Presentation only —
// the id stays the key everywhere else, and Expert keeps it on hover
// (components/AssetName.tsx).
//
// An id that already reads as a name (it has a space or a capital letter) is
// returned untouched: it is what a person typed, not a machine id.

const ACRONYMS: Record<string, string> = {
  ac: 'AC', dc: 'DC', it: 'IT', pv: 'PV', h2: 'H2', ups: 'UPS', ev: 'EV',
  hv: 'HV', mv: 'MV', lv: 'LV', bess: 'BESS', chp: 'CHP', ccgt: 'CCGT', ocgt: 'OCGT',
  poc: 'PoC', co2: 'CO₂', lng: 'LNG',
}

export function displayName(id: string): string {
  if (!id || /\s|[A-Z]/.test(id)) return id
  const words = id.split(/[_-]+/).filter(Boolean)
  return words
    .map((w, i) => ACRONYMS[w] ?? (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ')
}
