// The parameter → geometry rules of thumb (design D17): how much land, how
// many boxes, for each asset class, as DATA. `layout.ts` consumes them; a
// product line or a regional norm is a change here, not in code. No UI to
// edit them in Phase 1.
//
// Planning-grade, 2025. Not engineering dimensions.

export interface AssetRules {
  /** 20 ft container (east, north, height) metres. */
  container20ft: [number, number, number]
  /** 40 ft container (east, north, height) metres. */
  container40ft: [number, number, number]
  /** 3–5 MWh per 20 ft container in 2025 products. */
  mwhPerBessContainer: number
  /** Reciprocating engine / small gas turbine in a 40 ft enclosure. */
  mwPerGensetEnclosure: number
  /** PEM / alkaline 40 ft skid. */
  mwPerElectrolyserSkid: number
  /** ≈ 600 kg H₂ at 33.3 MWh/t per bullet tank. */
  mwhPerH2Bullet: number
  /** Ground-mount, fixed tilt. */
  haPerMwpPv: number
  pvRowPitchM: number
  pvRowLengthM: number
  mwPerTurbine: number
  turbineHubHeightM: number
  turbineRotorDiameterM: number
  /** IT MW → gross floor area, single storey. */
  dataHallM2PerMw: number
}

export const DEFAULT_ASSET_RULES: AssetRules = Object.freeze({
  container20ft: [6.1, 2.44, 2.9],
  container40ft: [12.2, 2.44, 2.9],
  mwhPerBessContainer: 4,
  mwPerGensetEnclosure: 2.5,
  mwPerElectrolyserSkid: 5,
  mwhPerH2Bullet: 20,
  haPerMwpPv: 2.5,
  pvRowPitchM: 6,
  pvRowLengthM: 30,
  mwPerTurbine: 5,
  turbineHubHeightM: 100,
  turbineRotorDiameterM: 140,
  dataHallM2PerMw: 800,
}) as AssetRules

const POSITIVE_SCALARS: Array<keyof AssetRules> = [
  'mwhPerBessContainer', 'mwPerGensetEnclosure', 'mwPerElectrolyserSkid', 'mwhPerH2Bullet',
  'haPerMwpPv', 'pvRowPitchM', 'pvRowLengthM', 'mwPerTurbine', 'turbineHubHeightM',
  'turbineRotorDiameterM', 'dataHallM2PerMw',
]

/** Throws with the offending field's name; every scalar finite and > 0, every box three finite positive sides. */
export function validateRules(r: AssetRules): void {
  for (const k of POSITIVE_SCALARS) {
    const v = r[k]
    if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) throw new Error(`asset rules: ${k} must be a positive number`)
  }
  for (const k of ['container20ft', 'container40ft'] as const) {
    const box = r[k]
    if (!Array.isArray(box) || box.length !== 3 || !box.every(v => Number.isFinite(v) && v > 0)) throw new Error(`asset rules: ${k} must be three positive sides`)
  }
}
